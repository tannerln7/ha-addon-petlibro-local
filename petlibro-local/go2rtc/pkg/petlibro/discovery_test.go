package petlibro

import (
	"context"
	"encoding/binary"
	"errors"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/tutk"
)

func TestResolveUIDValidatesInputBeforeNetwork(t *testing.T) {
	if _, err := ResolveUID("short", []string{"192.0.2.0/24"}, time.Second); err == nil {
		t.Fatal("expected invalid UID error")
	}
	if _, err := ResolveUID("PLAF20300000000ABCD0", []string{"not-a-cidr"}, time.Second); err == nil {
		t.Fatal("expected invalid subnet error")
	}
	if _, err := ResolveUID("PLAF20300000000ABCD0", []string{"192.0.2.0/24"}, 0); err == nil {
		t.Fatal("expected invalid timeout error")
	}
	options := testResolveOptions(time.Second)
	options.MaxUnicastPerSecond = 513
	if _, err := ResolveUIDContext(context.Background(), options); err == nil {
		t.Fatal("expected invalid unicast rate error")
	}
}

type fakeDiscoveryPacket struct {
	data []byte
	addr *net.UDPAddr
}

type fakeDiscoveryConn struct {
	mu         sync.Mutex
	deadline   time.Time
	reads      chan fakeDiscoveryPacket
	writes     []*net.UDPAddr
	wireWrites [][]byte
	writeTimes []time.Time
	onWrite    func(*net.UDPAddr)
	blockWrite bool
}

func newFakeDiscoveryConn() *fakeDiscoveryConn {
	return &fakeDiscoveryConn{reads: make(chan fakeDiscoveryPacket, 16)}
}

func (f *fakeDiscoveryConn) SetDeadline(deadline time.Time) error {
	f.mu.Lock()
	f.deadline = deadline
	f.mu.Unlock()
	return nil
}

func (f *fakeDiscoveryConn) SetReadDeadline(deadline time.Time) error {
	return f.SetDeadline(deadline)
}

func (f *fakeDiscoveryConn) WriteToUDP(data []byte, addr *net.UDPAddr) (int, error) {
	f.mu.Lock()
	f.writes = append(f.writes, addr)
	f.wireWrites = append(f.wireWrites, append([]byte(nil), data...))
	f.writeTimes = append(f.writeTimes, time.Now())
	deadline := f.deadline
	onWrite := f.onWrite
	block := f.blockWrite
	f.mu.Unlock()
	if onWrite != nil {
		onWrite(addr)
	}
	if block {
		if delay := time.Until(deadline); delay > 0 {
			time.Sleep(delay)
		}
		return 0, errors.New("write deadline exceeded")
	}
	return 88, nil
}

func TestDiscoveryProbeUsesCompleteFirmwareSequence(t *testing.T) {
	conn := newFakeDiscoveryConn()
	nonce := []byte{1, 2, 3, 4, 5, 6, 7, 8}
	probe := newDiscoveryProbe("PLAF20300000000ABCD0", nonce)
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	counters := &resolveCounters{}
	if err := sendDiscoveryProbe(
		ctx,
		conn,
		probe,
		&net.UDPAddr{IP: net.IPv4(192, 0, 2, 1), Port: lanPort},
		false,
		counters,
	); err != nil {
		t.Fatal(err)
	}
	if len(conn.wireWrites) != 3 {
		t.Fatalf("expected three probe packets, got %d", len(conn.wireWrites))
	}
	wantTypes := []uint16{msgLANSearch3, msgLANSearch3, msgKnock2}
	for index, wire := range conn.wireWrites {
		plain := tutk.ReverseTransCodePartial(nil, wire)
		if got := binary.LittleEndian.Uint16(plain[8:]); got != wantTypes[index] {
			t.Fatalf("packet %d type=0x%04x want=0x%04x", index, got, wantTypes[index])
		}
		if index < 2 && plain[0x40] != byte(index+1) {
			t.Fatalf("LAN_SEARCH3 packet %d w3=%d", index, plain[0x40])
		}
	}
	if counters.unicasts.Load() != 1 {
		t.Fatalf("expected one logical unicast probe, got %d", counters.unicasts.Load())
	}
	if counters.lanSearchW3_1Sent.Load() != 1 || counters.lanSearchW3_2Sent.Load() != 1 || counters.knock2Sent.Load() != 1 {
		t.Fatalf("unexpected per-leg counters: w3_1=%d w3_2=%d knock2=%d",
			counters.lanSearchW3_1Sent.Load(), counters.lanSearchW3_2Sent.Load(), counters.knock2Sent.Load())
	}
}

func (f *fakeDiscoveryConn) ReadFromUDP(buf []byte) (int, *net.UDPAddr, error) {
	f.mu.Lock()
	deadline := f.deadline
	f.mu.Unlock()
	delay := time.Until(deadline)
	if delay <= 0 {
		return 0, nil, errors.New("read deadline exceeded")
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case packet := <-f.reads:
		copy(buf, packet.data)
		return len(packet.data), packet.addr, nil
	case <-timer.C:
		return 0, nil, errors.New("read deadline exceeded")
	}
}

func encryptedLANSearchResponse(uid string) []byte {
	plain := make([]byte, 88)
	binary.LittleEndian.PutUint16(plain[8:], msgLANSearchR)
	copy(plain[0x10:0x24], uid)
	return tutk.TransCodePartial(nil, plain)
}

func encryptedKnockResponse(uid string, nonce []byte) []byte {
	plain := make([]byte, 52)
	binary.LittleEndian.PutUint16(plain[8:], msgKnockRR2)
	copy(plain[0x10:0x24], uid)
	copy(plain[0x24:0x2C], nonce)
	return tutk.TransCodePartial(nil, plain)
}

func testResolveOptions(timeout time.Duration) ResolveOptions {
	return ResolveOptions{
		UID: "PLAF20300000000ABCD0", Timeout: timeout,
		BroadcastDuration: 5 * time.Millisecond, MaxUnicastPerSecond: 1000,
	}
}

func TestBroadcastResponseResolvesImmediately(t *testing.T) {
	conn := newFakeDiscoveryConn()
	options := testResolveOptions(250 * time.Millisecond)
	plan := discoveryPlan{broadcasts: []*net.UDPAddr{{IP: net.IPv4bcast, Port: lanPort}}}
	conn.onWrite = func(_ *net.UDPAddr) {
		conn.reads <- fakeDiscoveryPacket{
			data: encryptedLANSearchResponse(options.UID),
			addr: &net.UDPAddr{IP: net.IPv4(192, 0, 2, 44), Port: lanPort},
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
	defer cancel()
	result, err := resolveWithPlan(ctx, conn, options, make([]byte, 8), plan, time.Now())
	if err != nil || result.Method != "broadcast" || result.IPAddress == nil || *result.IPAddress != "192.0.2.44" {
		t.Fatalf("unexpected result=%+v err=%v", result, err)
	}
	if result.Stats.BroadcastsSent != 1 || result.Stats.PacketsReceived != 1 {
		t.Fatalf("unexpected stats: %+v", result.Stats)
	}
	conn.mu.Lock()
	deadline := conn.deadline
	conn.mu.Unlock()
	if !deadline.IsZero() {
		t.Fatalf("successful resolver left a socket deadline installed: %s", deadline)
	}
}

func TestUnicastResponseIsProcessedDuringPacedSweep(t *testing.T) {
	conn := newFakeDiscoveryConn()
	options := testResolveOptions(500 * time.Millisecond)
	options.MaxUnicastPerSecond = 100
	plan := discoveryPlan{unicasts: []*net.UDPAddr{
		{IP: net.IPv4(192, 0, 2, 1), Port: lanPort},
		{IP: net.IPv4(192, 0, 2, 2), Port: lanPort},
		{IP: net.IPv4(192, 0, 2, 3), Port: lanPort},
	}}
	conn.onWrite = func(addr *net.UDPAddr) {
		if addr.IP.Equal(net.IPv4(192, 0, 2, 2)) {
			conn.reads <- fakeDiscoveryPacket{
				data: encryptedLANSearchResponse(options.UID),
				addr: &net.UDPAddr{IP: addr.IP, Port: lanPort},
			}
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
	defer cancel()
	result, err := resolveWithPlan(ctx, conn, options, make([]byte, 8), plan, time.Now())
	if err != nil || result.Method != "unicast" || result.Stats.UnicastsSent > 2 {
		t.Fatalf("response was not handled promptly: result=%+v err=%v", result, err)
	}
}

func TestNoResponseAndBlockedWriteRespectOverallDeadline(t *testing.T) {
	for _, blocked := range []bool{false, true} {
		t.Run(map[bool]string{false: "no-response", true: "blocked-write"}[blocked], func(t *testing.T) {
			conn := newFakeDiscoveryConn()
			conn.blockWrite = blocked
			options := testResolveOptions(80 * time.Millisecond)
			plan := discoveryPlan{unicasts: []*net.UDPAddr{{IP: net.IPv4(192, 0, 2, 1), Port: lanPort}}}
			started := time.Now()
			ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
			defer cancel()
			result, err := resolveWithPlan(ctx, conn, options, make([]byte, 8), plan, started)
			elapsed := time.Since(started)
			wantCode := "deadline_exceeded"
			if blocked {
				wantCode = "send_failed"
			}
			if err == nil || result.ErrorCode != wantCode || !result.Stats.DeadlineExceeded {
				t.Fatalf("unexpected result=%+v err=%v", result, err)
			}
			if elapsed > 180*time.Millisecond {
				t.Fatalf("deadline exceeded by too much: %s", elapsed)
			}
		})
	}
}

func TestUnicastFallbackIsRateLimited(t *testing.T) {
	conn := newFakeDiscoveryConn()
	options := testResolveOptions(400 * time.Millisecond)
	options.MaxUnicastPerSecond = 20
	plan := discoveryPlan{unicasts: []*net.UDPAddr{
		{IP: net.IPv4(192, 0, 2, 1), Port: lanPort},
		{IP: net.IPv4(192, 0, 2, 2), Port: lanPort},
		{IP: net.IPv4(192, 0, 2, 3), Port: lanPort},
	}}
	ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
	defer cancel()
	_, _ = resolveWithPlan(ctx, conn, options, make([]byte, 8), plan, time.Now())
	if len(conn.writeTimes) != 9 {
		t.Fatalf("expected three packet exchanges for three targets, got %d writes", len(conn.writeTimes))
	}
	if span := conn.writeTimes[6].Sub(conn.writeTimes[0]); span < 90*time.Millisecond {
		t.Fatalf("unicast target probes were not rate limited: %s", span)
	}
}

func TestKnockResponseRequiresMatchingUIDAndNonce(t *testing.T) {
	uid := "PLAF20300000000ABCD0"
	nonce := []byte{1, 2, 3, 4, 5, 6, 7, 8}
	response := make([]byte, 52)
	binary.LittleEndian.PutUint16(response[8:], msgKnockRR2)
	copy(response[0x10:0x24], uid)
	copy(response[0x24:0x2C], nonce)
	if reason := classifyDiscoveryResponse(response, uid, nonce); reason != discoveryResponseAccepted {
		t.Fatal("matching KNOCK_RR2 was rejected")
	}
	response[0x24] ^= 0xFF
	if reason := classifyDiscoveryResponse(response, uid, nonce); reason != discoveryResponseNonceMismatch {
		t.Fatalf("KNOCK_RR2 nonce mismatch reason=%d", reason)
	}
}

func TestKnockResponseStats(t *testing.T) {
	conn := newFakeDiscoveryConn()
	options := testResolveOptions(250 * time.Millisecond)
	nonce := []byte{1, 2, 3, 4, 5, 6, 7, 8}
	plan := discoveryPlan{cached: []*net.UDPAddr{{IP: net.IPv4(192, 0, 2, 44), Port: lanPort}}}
	var once sync.Once
	conn.onWrite = func(_ *net.UDPAddr) {
		once.Do(func() {
			conn.reads <- fakeDiscoveryPacket{
				data: encryptedKnockResponse(options.UID, nonce),
				addr: &net.UDPAddr{IP: net.IPv4(192, 0, 2, 44), Port: lanPort},
			}
		})
	}
	ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
	defer cancel()
	result, err := resolveWithPlan(ctx, conn, options, nonce, plan, time.Now())
	if err != nil || !result.Resolved {
		t.Fatalf("unexpected result=%+v err=%v", result, err)
	}
	if result.Stats.KnockRR2Received != 1 || result.Stats.ResponsesRejected != 0 {
		t.Fatalf("unexpected KNOCK_RR2 stats: %+v", result.Stats)
	}
}

func TestKnockNonceMismatchStats(t *testing.T) {
	conn := newFakeDiscoveryConn()
	options := testResolveOptions(80 * time.Millisecond)
	nonce := []byte{1, 2, 3, 4, 5, 6, 7, 8}
	wrongNonce := append([]byte(nil), nonce...)
	wrongNonce[0] ^= 0xFF
	plan := discoveryPlan{cached: []*net.UDPAddr{{IP: net.IPv4(192, 0, 2, 44), Port: lanPort}}}
	var once sync.Once
	conn.onWrite = func(_ *net.UDPAddr) {
		once.Do(func() {
			conn.reads <- fakeDiscoveryPacket{
				data: encryptedKnockResponse(options.UID, wrongNonce),
				addr: &net.UDPAddr{IP: net.IPv4(192, 0, 2, 44), Port: lanPort},
			}
		})
	}
	ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
	defer cancel()
	result, err := resolveWithPlan(ctx, conn, options, nonce, plan, time.Now())
	if err == nil || result.Resolved {
		t.Fatalf("nonce mismatch unexpectedly resolved: %+v", result)
	}
	if result.Stats.KnockRR2Received != 1 || result.Stats.NonceMismatchRejected != 1 {
		t.Fatalf("unexpected nonce mismatch stats: %+v", result.Stats)
	}
}

func TestWrongUIDResponseIsRejected(t *testing.T) {
	conn := newFakeDiscoveryConn()
	options := testResolveOptions(80 * time.Millisecond)
	plan := discoveryPlan{broadcasts: []*net.UDPAddr{{IP: net.IPv4bcast, Port: lanPort}}}
	conn.onWrite = func(_ *net.UDPAddr) {
		conn.reads <- fakeDiscoveryPacket{
			data: encryptedLANSearchResponse("PLAF20300000000WRONG"),
			addr: &net.UDPAddr{IP: net.IPv4(192, 0, 2, 99), Port: lanPort},
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), options.Timeout)
	defer cancel()
	result, err := resolveWithPlan(ctx, conn, options, make([]byte, 8), plan, time.Now())
	if err == nil || result.Resolved || result.Stats.ResponsesRejected == 0 || result.Stats.WrongUIDRejected == 0 {
		t.Fatalf("wrong UID was not rejected: result=%+v err=%v", result, err)
	}
}

func TestCIDRDiscoveryTargets(t *testing.T) {
	targets := cidrDiscoveryTargets("192.0.2.0/30", 16)
	if len(targets) != 2 || targets[0].String() != "192.0.2.1" || targets[1].String() != "192.0.2.2" {
		t.Fatalf("unexpected targets: %v", targets)
	}
	if targets := cidrDiscoveryTargets("192.0.2.0/16", 256); targets != nil {
		t.Fatalf("expected oversized network rejection, got %d targets", len(targets))
	}
}
