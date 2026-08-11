package petlibro

import (
	"encoding/binary"
	"errors"
	"io"
	"os"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/tutk"
)

// petlibro divergence vs pkg/tutk: tutk's worker (pkg/tutk/conn.go:164)
// is a single goroutine that owns the socket: Conn.Read decrypts inline
// and dispatches via handleMsg without any intermediate channel.  Petlibro
// decouples the read syscall from decryption with a reader goroutine
// feeding a 4096-deep channel, so a slow decrypt or GC pause can't
// stall the read syscall and cause kernel UDP drops on high-bitrate
// HD streams.  The cost is one channel hop per packet; the benefit is
// the readerDrops counter making any backlog visible.

// handleEncryptedDatagram decrypts a raw wire packet and dispatches it.
// Tests prefer parseDatagram (plaintext input) directly.
func (c *Client) handleEncryptedDatagram(raw []byte) {
	// petlibro divergence vs pkg/tutk: tutk uses
	// ReverseTransCodePartial in the receive direction (pkg/tutk/conn.go:84)
	// — same primitive, same direction; the only thing different here is
	// that the call lives in a processor goroutine downstream of the
	// read syscall instead of inside the read loop itself.
	pkt := tutk.ReverseTransCodePartial(nil, raw)
	if c.verbose && c.tracePackets {
		log.Debug().Int("wireLen", len(raw)).Hex("plain", pkt).Msg("petlibro D2C packet")
	}
	if c.d2cPlainDump != nil {
		record := make([]byte, 4+len(pkt))
		binary.LittleEndian.PutUint32(record, uint32(len(pkt)))
		copy(record[4:], pkt)

		c.d2cDumpMu.Lock()
		n, err := c.d2cPlainDump.Write(record)
		c.d2cDumpMu.Unlock()
		if err == nil && n != len(record) {
			err = io.ErrShortWrite
		}
		if err != nil {
			log.Warn().Err(err).Msg("petlibro: write plaintext datagram dump")
		}
	}
	c.parseDatagram(pkt)
}

// initACKTracking establishes the last sequence position covered by the
// bootstrap AV-ready ACK. It is separate from avNextExt so assembler skips
// can never acknowledge data that was not actually received.
func (c *Client) initACKTracking(watermark uint64) {
	c.ackMu.Lock()
	if c.ackMode == "" {
		c.ackMode = ackModeHigh
	}
	if c.ackLagWindow == 0 {
		c.ackLagWindow = defaultACKLagWindow
	}
	if c.ackInterval == 0 {
		c.ackInterval = defaultACKInterval
	}
	c.ackPendingWarn = 32
	c.ackWatermarkExt = watermark
	c.ackSeen = make(map[uint64]struct{})
	c.ackLastPrev = uint16(watermark)
	c.ackLastCurrent = uint16(watermark)
	c.ackHaveLast = true
	c.stats.ackWatermark.Store(watermark)
	c.stats.ackSeenPending.Store(0)
	c.stats.ackHigh.Store(watermark)
	c.stats.ackPrevLow16.Store(uint64(uint16(watermark)))
	c.stats.ackCurrentLow16.Store(uint64(uint16(watermark)))
	c.avNextObserved.Store(c.avNextExt)
	c.ackMu.Unlock()
}

// markACKReceived records one accepted AV/media datagram. Out-of-order
// packets remain pending until every sequence position below them has also
// been observed; forceDrain and avNextExt never participate in this state.
func (c *Client) markACKReceived(subExt uint64) {
	c.ackMu.Lock()
	defer c.ackMu.Unlock()

	if subExt <= c.ackWatermarkExt {
		c.stats.ackDuplicateOrOld.Add(1)
		return
	}
	if _, ok := c.ackSeen[subExt]; ok {
		c.stats.ackDuplicateOrOld.Add(1)
		return
	}
	if c.ackSeen == nil {
		c.ackSeen = make(map[uint64]struct{})
	}
	c.ackSeen[subExt] = struct{}{}

	var advanced uint64
	for {
		next := c.ackWatermarkExt + 1
		if _, ok := c.ackSeen[next]; !ok {
			break
		}
		delete(c.ackSeen, next)
		c.ackWatermarkExt = next
		advanced++
	}
	if advanced != 0 {
		c.stats.ackAdvanced.Add(advanced)
		if c.verbose && !c.traceACK && !c.ackGapStarted.IsZero() && time.Since(c.ackGapStarted) >= time.Second {
			log.Debug().Uint64("advanced", advanced).Uint64("watermark", c.ackWatermarkExt).
				Int("pending", len(c.ackSeen)).Dur("stalledFor", time.Since(c.ackGapStarted)).
				Msg("petlibro ACK gap advanced")
			c.ackGapStarted = time.Now()
		}
	}
	if len(c.ackSeen) == 0 {
		c.ackGapStarted = time.Time{}
		c.ackPendingWarn = 32
	} else {
		if c.ackGapStarted.IsZero() {
			c.ackGapStarted = time.Now()
		}
		if c.verbose && !c.traceACK && uint64(len(c.ackSeen)) >= c.ackPendingWarn {
			log.Warn().Uint64("watermark", c.ackWatermarkExt).Uint64("high", c.stats.ackHigh.Load()).
				Int("pending", len(c.ackSeen)).Msg("petlibro ACK receive gap stalled")
			c.ackPendingWarn *= 2
		}
	}
	c.stats.ackWatermark.Store(c.ackWatermarkExt)
	c.stats.ackSeenPending.Store(uint64(len(c.ackSeen)))
}

func (c *Client) contiguousAckExt() uint64 {
	c.ackMu.Lock()
	watermark := c.ackWatermarkExt
	c.ackMu.Unlock()
	return watermark
}

type ackFields struct {
	prev         uint16
	current      uint16
	prevRole     string
	currentRole  string
	watermarkExt uint64
	highExt      uint64
	lagWindow    uint64
	seenPending  uint64
	shouldSend   bool
}

// nextACKFields maps the tracked receive state onto innerAck's two sequence
// fields. Their wire positions are known (avPrev at bytes 8..9 and avCurr at
// bytes 10..11), but their full protocol semantics remain under test. Hybrid
// deliberately places the contiguous and observed-high positions in those
// fields so live dumps can show how the camera reacts to that pair.
func (c *Client) nextACKFields() ackFields {
	c.ackMu.Lock()
	state := ackFields{
		watermarkExt: c.ackWatermarkExt,
		seenPending:  uint64(len(c.ackSeen)),
		lagWindow:    c.ackLagWindow,
	}
	lastPrev := c.ackLastPrev
	lastCurrent := c.ackLastCurrent
	haveLast := c.ackHaveLast
	c.ackMu.Unlock()

	state.highExt = c.stats.ackHigh.Load()
	laggedHigh := state.watermarkExt + state.lagWindow
	if laggedHigh < state.watermarkExt || laggedHigh > state.highExt {
		laggedHigh = state.highExt
	}
	pairChanged := func() bool {
		return !haveLast || state.prev != lastPrev || state.current != lastCurrent
	}
	switch c.ackMode {
	case ackModeContig:
		state.prev = c.avPrevSubWire
		state.current = uint16(state.watermarkExt)
		state.prevRole = "previous_sent_avCurr"
		state.currentRole = "contiguous_watermark"
		state.shouldSend = state.watermarkExt >= 0x4000 && state.current != c.avPrevSubWire
	case ackModeHybrid, ackModePrevContigCurrHigh:
		state.prev = uint16(state.watermarkExt)
		state.current = uint16(state.highExt)
		state.prevRole = "contiguous_watermark"
		state.currentRole = "highest_observed"
		state.shouldSend = state.highExt >= 0x4000 && pairChanged()
	case ackModeHybridRev:
		state.prev = uint16(state.highExt)
		state.current = uint16(state.watermarkExt)
		state.prevRole = "highest_observed"
		state.currentRole = "contiguous_watermark"
		state.shouldSend = state.highExt >= 0x4000 && pairChanged()
	case ackModeLagHigh:
		state.prev = c.avPrevSubWire
		state.current = uint16(laggedHigh)
		state.prevRole = "previous_sent_avCurr"
		state.currentRole = "min(highest_observed,contiguous_watermark+ack_lag_window)"
		state.shouldSend = laggedHigh >= 0x4000 && state.current != c.avPrevSubWire
	case ackModeLagHybrid:
		state.prev = uint16(state.watermarkExt)
		state.current = uint16(laggedHigh)
		state.prevRole = "contiguous_watermark"
		state.currentRole = "min(highest_observed,contiguous_watermark+ack_lag_window)"
		state.shouldSend = laggedHigh >= 0x4000 && pairChanged()
	default: // high and prev-sent-curr-high preserve the original flow behavior
		state.prev = c.avPrevSubWire
		state.current = uint16(state.highExt)
		state.prevRole = "previous_sent_avCurr"
		state.currentRole = "highest_observed"
		state.shouldSend = state.highExt >= 0x4000 && state.current != c.avPrevSubWire
	}
	if c.ackRepeatUnchanged && state.highExt >= 0x4000 {
		state.shouldSend = true
	}
	return state
}

// recvLoop runs two goroutines: a tight reader that does nothing but
// drain the UDP socket into a channel, and a processor that decrypts
// and dispatches.  Decoupling means a slow decrypt or GC pause can't
// stall the read syscall and cause kernel UDP drops.
func (c *Client) recvLoop() {
	defer c.Close()

	rawChan := make(chan []byte, 4096) // ~4 MiB at peak packet sizes
	go c.readerGoroutine(rawChan)

	lastForce := time.Now()
	lastStats := time.Now()
	lastRuntimeStatus := time.Now()
	tick := time.NewTicker(20 * time.Millisecond)
	defer tick.Stop()

	for {
		select {
		case <-c.done:
			return
		case raw, ok := <-rawChan:
			if !ok {
				return
			}
			c.stats.bytesIn.Add(uint64(len(raw)))
			c.stats.pktsIn.Add(1)
			c.handleEncryptedDatagram(raw)
		case <-tick.C:
			// fall through to periodic work below
		}

		// Shorter forceDrain interval — the camera (over LAN) has
		// usually retransmitted lost packets within ~50-100 ms or
		// they're not coming at all.  Holding longer just adds
		// rendering latency without recovering more data.
		if time.Since(lastForce) > 100*time.Millisecond {
			c.forceDrain()
			lastForce = time.Now()
		}
		if c.verbose && time.Since(lastStats) > 5*time.Second {
			c.dumpStats()
			lastStats = time.Now()
		}
		if c.runtimeStatus != nil && time.Since(lastRuntimeStatus) > 5*time.Second {
			c.runtimeStatus.updateHealth(c.stats.snapshot())
			lastRuntimeStatus = time.Now()
		}
	}
}

// readerGoroutine does nothing but pull bytes off the wire as fast as
// the kernel will deliver them and hand them to the processor.  Each
// iteration allocates a fresh buffer because the channel may queue
// many at once.
func (c *Client) readerGoroutine(out chan<- []byte) {
	defer close(out)
	for {
		select {
		case <-c.done:
			return
		default:
		}
		_ = c.conn.SetReadDeadline(time.Now().Add(1 * time.Second))
		buf := make([]byte, 65535)
		n, _, err := c.conn.ReadFromUDP(buf)
		if err != nil {
			if errors.Is(err, os.ErrDeadlineExceeded) {
				c.stats.recvTimeouts.Add(1)
				continue
			}
			return
		}
		if n == 0 {
			continue
		}
		select {
		case <-c.done:
			return
		case out <- buf[:n]:
		default:
			// Processor is behind by 4096 packets. Drop the new
			// one rather than block the reader and let the kernel
			// drop instead — the inner cmd counter / frag_idx gap
			// detection will mark the resulting AU as incomplete.
			c.stats.readerDrops.Add(1)
		}
	}
}

// dumpStats prints a one-line stream-health summary every ~5 s when
// the client is in verbose mode.  Numbers cover the most recent
// interval; the cumulative counters are also visible.
func (c *Client) dumpStats() {
	cur := c.stats.snapshot()
	delta := cur
	if c.havePrevStats {
		delta = countersSnapshot{
			bytesIn:      cur.bytesIn - c.prevStats.bytesIn,
			pktsIn:       cur.pktsIn - c.prevStats.pktsIn,
			mainFrags:    cur.mainFrags - c.prevStats.mainFrags,
			subFrags:     cur.subFrags - c.prevStats.subFrags,
			audioFrags:   cur.audioFrags - c.prevStats.audioFrags,
			otherFrags:   cur.otherFrags - c.prevStats.otherFrags,
			vidFrags:     cur.vidFrags - c.prevStats.vidFrags,
			vidFramesIn:  cur.vidFramesIn - c.prevStats.vidFramesIn,
			vidFramesOut: cur.vidFramesOut - c.prevStats.vidFramesOut,
			vidDropped:   cur.vidDropped - c.prevStats.vidDropped,
			fragSkips:    cur.fragSkips - c.prevStats.fragSkips,
			fragsLost:    cur.fragsLost - c.prevStats.fragsLost,
			forceDrains:  cur.forceDrains - c.prevStats.forceDrains,
			recvTimeouts: cur.recvTimeouts - c.prevStats.recvTimeouts,
			readerDrops:  cur.readerDrops - c.prevStats.readerDrops,
			emitDrops:    cur.emitDrops - c.prevStats.emitDrops,
			dualStreamIL: cur.dualStreamIL - c.prevStats.dualStreamIL,

			fragIdxGap:                 cur.fragIdxGap - c.prevStats.fragIdxGap,
			frameNumJumpMain:           cur.frameNumJumpMain - c.prevStats.frameNumJumpMain,
			frameNumJumpSub:            cur.frameNumJumpSub - c.prevStats.frameNumJumpSub,
			expectedDataShortfall:      cur.expectedDataShortfall - c.prevStats.expectedDataShortfall,
			zeroDataHardDrop:           cur.zeroDataHardDrop - c.prevStats.zeroDataHardDrop,
			wrongStreamDrop:            cur.wrongStreamDrop - c.prevStats.wrongStreamDrop,
			strictIDRDrop:              cur.strictIDRDrop - c.prevStats.strictIDRDrop,
			strictPDrop:                cur.strictPDrop - c.prevStats.strictPDrop,
			forceDrainFlush:            cur.forceDrainFlush - c.prevStats.forceDrainFlush,
			forceDrainEntries:          cur.forceDrainEntries - c.prevStats.forceDrainEntries,
			deferredDrop:               cur.deferredDrop - c.prevStats.deferredDrop,
			framesWithLoss:             cur.framesWithLoss - c.prevStats.framesWithLoss,
			idrFramesWithLoss:          cur.idrFramesWithLoss - c.prevStats.idrFramesWithLoss,
			pFramesWithLoss:            cur.pFramesWithLoss - c.prevStats.pFramesWithLoss,
			missingFragmentsTotal:      cur.missingFragmentsTotal - c.prevStats.missingFragmentsTotal,
			maxMissingFragmentsInFrame: cur.maxMissingFragmentsInFrame,
			normalMediaPackets:         cur.normalMediaPackets - c.prevStats.normalMediaPackets,
			extendedMediaCandidates:    cur.extendedMediaCandidates - c.prevStats.extendedMediaCandidates,
			extendedMediaParsed:        cur.extendedMediaParsed - c.prevStats.extendedMediaParsed,
			extendedMediaRejected:      cur.extendedMediaRejected - c.prevStats.extendedMediaRejected,
			extendedMediaDataPackets:   cur.extendedMediaDataPackets - c.prevStats.extendedMediaDataPackets,
			extendedMediaEndPackets:    cur.extendedMediaEndPackets - c.prevStats.extendedMediaEndPackets,
			extendedMediaRarePackets:   cur.extendedMediaRarePackets - c.prevStats.extendedMediaRarePackets,
			unknown0c08Remaining:       cur.unknown0c08Remaining - c.prevStats.unknown0c08Remaining,
			unknown0c0dRemaining:       cur.unknown0c0dRemaining - c.prevStats.unknown0c0dRemaining,
			sequenceSeenButUnhandled:   cur.sequenceSeenButUnhandled - c.prevStats.sequenceSeenButUnhandled,
			sequenceSeenAndAssembled:   cur.sequenceSeenAndAssembled - c.prevStats.sequenceSeenAndAssembled,
			frameInfoChanges:           cur.frameInfoChanges - c.prevStats.frameInfoChanges,
			frameInfoUnexpected:        cur.frameInfoUnexpected - c.prevStats.frameInfoUnexpected,
			frameInfoCodec:             cur.frameInfoCodec,
			frameInfoFlag:              cur.frameInfoFlag,
			frameInfoByte4:             cur.frameInfoByte4,

			ackWatermark:      cur.ackWatermark,
			ackSeenPending:    cur.ackSeenPending,
			ackDuplicateOrOld: cur.ackDuplicateOrOld - c.prevStats.ackDuplicateOrOld,
			ackAdvanced:       cur.ackAdvanced - c.prevStats.ackAdvanced,
			ackHigh:           cur.ackHigh,
			ackSent:           cur.ackSent - c.prevStats.ackSent,
			ackPrevLow16:      cur.ackPrevLow16,
			ackCurrentLow16:   cur.ackCurrentLow16,
		}
	}
	if c.havePrevStats && cur.ackCurrentLow16 == c.prevStats.ackCurrentLow16 && cur.ackHigh > c.prevStats.ackHigh && !c.ackCurrStallWarned {
		log.Warn().Str("ackMode", string(c.ackMode)).Uint64("watermark", cur.ackWatermark).
			Uint64("high", cur.ackHigh).Uint16("current", uint16(cur.ackCurrentLow16)).
			Uint64("pending", cur.ackSeenPending).Msg("petlibro ACK current stalled while high advances")
		c.ackCurrStallWarned = true
	} else if !c.havePrevStats || cur.ackCurrentLow16 != c.prevStats.ackCurrentLow16 {
		c.ackCurrStallWarned = false
	}
	stalled := delta.mainFrags == 0 && delta.subFrags == 0 && delta.audioFrags == 0 && delta.vidFramesIn == 0
	sameStallState := c.stallStatsActive && cur.ackWatermark == c.stallStatsWatermark &&
		cur.ackHigh == c.stallStatsHigh && cur.ackCurrentLow16 == c.stallStatsCurrent &&
		cur.ackSeenPending == c.stallStatsPending
	if stalled && sameStallState {
		c.stallStatsRepeat++
		c.stallControlPkts += delta.otherFrags
		log.Debug().Msgf("stats: stalled repeat=%d in=%d controlOnlyPackets=%d ackMode=%s watermark=0x%x high=0x%x pending=%d sent=%d current=0x%04x",
			c.stallStatsRepeat, delta.pktsIn, c.stallControlPkts, c.ackMode, cur.ackWatermark, cur.ackHigh, cur.ackSeenPending, delta.ackSent, uint16(cur.ackCurrentLow16))
		c.prevStats = cur
		return
	}
	c.stallStatsActive = stalled
	c.stallStatsRepeat = 0
	c.stallControlPkts = delta.otherFrags
	c.stallStatsWatermark = cur.ackWatermark
	c.stallStatsHigh = cur.ackHigh
	c.stallStatsCurrent = cur.ackCurrentLow16
	c.stallStatsPending = cur.ackSeenPending
	log.Debug().Msgf("stats: in=%d pkts (%d KiB) channels: main=%d sub=%d audio=%d other=%d | video: %d frames in -> %d out (drop %d) | loss: frames=%d idr=%d p=%d missing=%d maxFrame=%d | frag skips: %d (%d frags lost) | forceDrain: %d | qDrops reader=%d emit=%d | dualStreamIL=%d | reasons: fragIdxGap=%d frameNumJumpMain=%d frameNumJumpSub=%d expectedDataShortfall=%d zeroDataHardDrop=%d wrongStreamDrop=%d strictIDRDrop=%d strictPDrop=%d forceDrainFlush=%d forceDrainEntries=%d deferredDrop=%d | mediaHeaders: normal=%d extendedMedia parsed=%d rejected=%d data=%d end=%d rare=%d unknown0c08=%d unknown0c0d=%d candidates=%d seqAssembled=%d seqUnhandled=%d | frameinfo: codec=0x%04x flag=%d onlineNumOrStreamByte=%d changes=%d unexpected=%d | ack: ackMode=%s repeat=%t interval=%s watermark=0x%x high=0x%x avNext=0x%x pending=%d advanced=%d old=%d sent=%d prev=0x%04x current=0x%04x lagWindow=%d",
		delta.pktsIn, delta.bytesIn/1024,
		delta.mainFrags, delta.subFrags, delta.audioFrags, delta.otherFrags,
		delta.vidFramesIn, delta.vidFramesOut, delta.vidDropped,
		delta.framesWithLoss, delta.idrFramesWithLoss, delta.pFramesWithLoss, delta.missingFragmentsTotal, delta.maxMissingFragmentsInFrame,
		delta.fragSkips, delta.fragsLost, delta.forceDrains,
		delta.readerDrops, delta.emitDrops, delta.dualStreamIL,
		delta.fragIdxGap, delta.frameNumJumpMain, delta.frameNumJumpSub,
		delta.expectedDataShortfall, delta.zeroDataHardDrop, delta.wrongStreamDrop,
		delta.strictIDRDrop, delta.strictPDrop, delta.forceDrainFlush,
		delta.forceDrainEntries, delta.deferredDrop,
		delta.normalMediaPackets, delta.extendedMediaParsed, delta.extendedMediaRejected,
		delta.extendedMediaDataPackets, delta.extendedMediaEndPackets, delta.extendedMediaRarePackets,
		delta.unknown0c08Remaining, delta.unknown0c0dRemaining, delta.extendedMediaCandidates,
		delta.sequenceSeenAndAssembled, delta.sequenceSeenButUnhandled,
		uint16(delta.frameInfoCodec), delta.frameInfoFlag, delta.frameInfoByte4, delta.frameInfoChanges, delta.frameInfoUnexpected,
		c.ackMode, c.ackRepeatUnchanged, c.ackInterval, delta.ackWatermark, delta.ackHigh, c.avNextObserved.Load(), delta.ackSeenPending,
		delta.ackAdvanced, delta.ackDuplicateOrOld, delta.ackSent,
		uint16(delta.ackPrevLow16), uint16(delta.ackCurrentLow16), c.ackLagWindow)
	c.prevStats = cur
	c.havePrevStats = true
}

// maintenanceLoop fires heartbeat, alive, and sliding-window ACK
// packets on cadences calibrated to the official Petlibro app's
// behaviour.  Exits on c.done.
func (c *Client) maintenanceLoop() {
	tickBase := time.Now().UnixMilli()
	tick32 := func() uint32 { return uint32((time.Now().UnixMilli() - tickBase + 0xC000) & 0xFFFFFFFF) }
	tick16 := func() uint16 { return uint16(time.Now().UnixMilli() & 0xFFFF) }

	hb := time.NewTicker(1 * time.Second)
	alive := time.NewTicker(1500 * time.Millisecond)
	interval := c.ackInterval
	if interval == 0 {
		interval = defaultACKInterval
	}
	ack := time.NewTicker(interval)
	defer hb.Stop()
	defer alive.Stop()
	defer ack.Stop()

	for {
		select {
		case <-c.done:
			return
		case <-hb.C:
			_ = c.sendInner(innerHeartbeat(c.icounter, tick32()))
			c.icounter++
		case <-alive.C:
			_ = c.send(buildAliveC2D(c.nonce))
		case <-ack.C:
			fields := c.nextACKFields()
			if fields.shouldSend {
				const chanIdx = uint32(3)
				const subIdx = uint16(0x34)
				tick := tick16()
				body := innerAck(c.icounter, fields.prev, fields.current, chanIdx, subIdx, tick)
				if c.verbose && c.traceACK {
					log.Debug().
						Str("ackMode", string(c.ackMode)).
						Str("avPrevRole", fields.prevRole).
						Str("avCurrRole", fields.currentRole).
						Uint16("icounter", c.icounter).
						Uint16("avPrev", fields.prev).
						Uint16("avCurr", fields.current).
						Uint64("ackWatermarkExt", fields.watermarkExt).
						Uint64("avHighExt", fields.highExt).
						Uint64("avNextExt", c.avNextObserved.Load()).
						Uint64("ackSeenPending", fields.seenPending).
						Uint64("ackLagWindow", fields.lagWindow).
						Uint32("chanIdx", chanIdx).
						Uint16("subIdx", subIdx).
						Uint16("tick16", tick).
						Hex("body", body).
						Msg("petlibro ACK send")
				}
				err := c.sendInner(body)
				c.icounter++
				c.avPrevSubWire = fields.current
				c.ackMu.Lock()
				c.ackLastPrev = fields.prev
				c.ackLastCurrent = fields.current
				c.ackHaveLast = true
				c.ackMu.Unlock()
				c.stats.ackPrevLow16.Store(uint64(fields.prev))
				c.stats.ackCurrentLow16.Store(uint64(fields.current))
				if err == nil {
					c.stats.ackSent.Add(1)
				}
			}
		}
	}
}
