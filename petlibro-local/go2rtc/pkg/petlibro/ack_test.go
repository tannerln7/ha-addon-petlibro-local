package petlibro

import (
	"testing"
	"time"
)

func TestParseACKInterval(t *testing.T) {
	if got, err := parseACKInterval(""); err != nil || got != 25*time.Millisecond {
		t.Fatalf("default interval=%s err=%v", got, err)
	}
	if got, err := parseACKInterval("50"); err != nil || got != 50*time.Millisecond {
		t.Fatalf("explicit interval=%s err=%v", got, err)
	}
	if _, err := parseACKInterval("0"); err == nil {
		t.Fatal("zero interval accepted")
	}
}

func TestParseACKMode(t *testing.T) {
	tests := []struct {
		value string
		want  ackMode
	}{
		{"", ackModeHigh},
		{"high", ackModeHigh},
		{"contig", ackModeContig},
		{"hybrid", ackModeHybrid},
		{"hybrid-rev", ackModeHybridRev},
		{"prev-contig-curr-high", ackModePrevContigCurrHigh},
		{"prev-sent-curr-high", ackModePrevSentCurrHigh},
		{"lag-high", ackModeLagHigh},
		{"lag-hybrid", ackModeLagHybrid},
	}
	for _, tt := range tests {
		got, err := parseACKMode(tt.value)
		if err != nil {
			t.Fatalf("parseACKMode(%q): %v", tt.value, err)
		}
		if got != tt.want {
			t.Fatalf("parseACKMode(%q)=%q, want %q", tt.value, got, tt.want)
		}
	}
	if _, err := parseACKMode("bogus"); err == nil {
		t.Fatal("parseACKMode accepted an unsupported mode")
	}
}

func TestParseACKLagWindow(t *testing.T) {
	for _, tt := range []struct {
		value string
		want  uint64
	}{
		{"", defaultACKLagWindow},
		{"1", 1},
		{"16", 16},
		{"65535", 65535},
	} {
		got, err := parseACKLagWindow(tt.value)
		if err != nil {
			t.Fatalf("parseACKLagWindow(%q): %v", tt.value, err)
		}
		if got != tt.want {
			t.Errorf("parseACKLagWindow(%q)=%d, want %d", tt.value, got, tt.want)
		}
	}
	for _, value := range []string{"0", "-1", "65536", "bogus"} {
		if _, err := parseACKLagWindow(value); err == nil {
			t.Errorf("parseACKLagWindow(%q) unexpectedly succeeded", value)
		}
	}
}

func TestACKModeFields(t *testing.T) {
	c := newTestClient(0x4000, "hd")
	c.markACKReceived(0x4000)
	c.markACKReceived(0x4020) // 0x4001..0x401f are missing
	c.avHighExt = 0x4020
	c.stats.ackHigh.Store(c.avHighExt)
	c.ackLagWindow = 8

	tests := []struct {
		mode            ackMode
		wantPrev        uint16
		wantCurrent     uint16
		wantPrevRole    string
		wantCurrentRole string
	}{
		{ackModeHigh, 0x3fff, 0x4020, "previous_sent_avCurr", "highest_observed"},
		{ackModeContig, 0x3fff, 0x4000, "previous_sent_avCurr", "contiguous_watermark"},
		{ackModeHybrid, 0x4000, 0x4020, "contiguous_watermark", "highest_observed"},
		{ackModeHybridRev, 0x4020, 0x4000, "highest_observed", "contiguous_watermark"},
		{ackModePrevContigCurrHigh, 0x4000, 0x4020, "contiguous_watermark", "highest_observed"},
		{ackModePrevSentCurrHigh, 0x3fff, 0x4020, "previous_sent_avCurr", "highest_observed"},
		{ackModeLagHigh, 0x3fff, 0x4008, "previous_sent_avCurr", "min(highest_observed,contiguous_watermark+ack_lag_window)"},
		{ackModeLagHybrid, 0x4000, 0x4008, "contiguous_watermark", "min(highest_observed,contiguous_watermark+ack_lag_window)"},
	}
	for _, tt := range tests {
		c.ackMode = tt.mode
		fields := c.nextACKFields()
		if !fields.shouldSend {
			t.Errorf("mode %q did not request an ACK", tt.mode)
		}
		if fields.prev != tt.wantPrev || fields.current != tt.wantCurrent {
			t.Errorf("mode %q fields=%04x/%04x, want %04x/%04x",
				tt.mode, fields.prev, fields.current, tt.wantPrev, tt.wantCurrent)
		}
		if fields.prevRole != tt.wantPrevRole || fields.currentRole != tt.wantCurrentRole {
			t.Errorf("mode %q roles=%q/%q, want %q/%q", tt.mode,
				fields.prevRole, fields.currentRole, tt.wantPrevRole, tt.wantCurrentRole)
		}
		if fields.watermarkExt != 0x4000 || fields.highExt != 0x4020 || fields.seenPending != 1 {
			t.Errorf("mode %q evidence watermark/high/pending=%x/%x/%d, want 4000/4020/1",
				tt.mode, fields.watermarkExt, fields.highExt, fields.seenPending)
		}
		if fields.lagWindow != 8 {
			t.Errorf("mode %q lagWindow=%d, want 8", tt.mode, fields.lagWindow)
		}
	}
}

func TestACKLagModesCapAtHigh(t *testing.T) {
	c := newTestClient(0x4000, "hd")
	c.markACKReceived(0x4000)
	c.markACKReceived(0x4003)
	c.stats.ackHigh.Store(0x4003)
	c.ackLagWindow = 8

	for _, mode := range []ackMode{ackModeLagHigh, ackModeLagHybrid} {
		c.ackMode = mode
		if fields := c.nextACKFields(); fields.current != 0x4003 {
			t.Errorf("mode %q current=0x%04x, want high-water cap 0x4003", mode, fields.current)
		}
	}
}

func TestACKModesDoNotResendUnchangedFields(t *testing.T) {
	c := newTestClient(0x4000, "hd")
	c.markACKReceived(0x4000)
	c.stats.ackHigh.Store(0x4000)
	c.avPrevSubWire = 0x4000
	c.ackLastPrev = 0x4000
	c.ackLastCurrent = 0x4000
	c.ackHaveLast = true

	for _, mode := range []ackMode{
		ackModeHigh,
		ackModeContig,
		ackModeHybrid,
		ackModeHybridRev,
		ackModePrevContigCurrHigh,
		ackModePrevSentCurrHigh,
		ackModeLagHigh,
		ackModeLagHybrid,
	} {
		c.ackMode = mode
		if fields := c.nextACKFields(); fields.shouldSend {
			t.Errorf("mode %q requested duplicate ACK %04x/%04x", mode, fields.prev, fields.current)
		}
	}
}

func TestACKRepeatUnchanged(t *testing.T) {
	c := newTestClient(0x4000, "hd")
	c.markACKReceived(0x4000)
	c.stats.ackHigh.Store(0x4000)
	c.avPrevSubWire = 0x4000
	c.ackHaveLast = true
	c.ackLastPrev, c.ackLastCurrent = 0x4000, 0x4000
	c.ackRepeatUnchanged = true
	if fields := c.nextACKFields(); !fields.shouldSend {
		t.Fatal("ack_repeat_unchanged did not schedule the unchanged ACK")
	}
}
