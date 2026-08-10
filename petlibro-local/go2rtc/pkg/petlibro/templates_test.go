package petlibro

import (
	"encoding/binary"
	"encoding/hex"
	"testing"
)

// Original opaque template hex strings — kept here as a regression
// fixture. The byte-exact equivalence check below is the contract
// that anchors the refactored, field-driven builder.
//
// Seed embedded in these fixtures (bytes [0x14:0x18] LE) = 0x49a53e94.

const origLoginATemplateHex = "" +
	"00000c0000000000000000000000000022020100943ea54961646d696e000000" +
	"0000000086d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c0451c1e4840db50ce8d2e640" +
	"878c2e8f86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0cc7d0c2e4e42d0d13e8d2e640" +
	"840cbd0c86d0c2e4843dad2c18d3e640840cad0c436860726c69"

const origLoginBTemplateHex = "" +
	"00200c0000000000000000000000000024020000953ea54961646d696e000000" +
	"0000000086d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c0451c1e4840db50ce8d2e640" +
	"878c2e8f86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0c86d0c2e4842dad0ce8d2e640" +
	"840cad0c86d0c2e4842dad0ce8d2e640840cad0cc7d0c2e4e42d0d13e8d2e640" +
	"840cbd0c86d0c2e4843dad2c18d3e640840cad0c436861736c696520"

const origLoginSeed uint32 = 0x49a53e94

// TestBuildLoginPairMatchesOriginalTemplates anchors the refactor:
// the field-driven builder must reproduce the original opaque hex
// templates byte-for-byte when given the seed embedded in those
// templates (login_serial at wrapper offset 0x14, LE).
func TestBuildLoginPairMatchesOriginalTemplates(t *testing.T) {
	wantA, err := hex.DecodeString(origLoginATemplateHex)
	if err != nil {
		t.Fatalf("decode origLoginATemplateHex: %v", err)
	}
	wantB, err := hex.DecodeString(origLoginBTemplateHex)
	if err != nil {
		t.Fatalf("decode origLoginBTemplateHex: %v", err)
	}
	if len(wantA) != 570 {
		t.Fatalf("LOGIN A fixture length = %d, want 570", len(wantA))
	}
	if len(wantB) != 572 {
		t.Fatalf("LOGIN B fixture length = %d, want 572", len(wantB))
	}

	// Sanity-check our claim about the embedded seed.
	if got := binary.LittleEndian.Uint32(wantA[0x14:]); got != origLoginSeed {
		t.Fatalf("LOGIN A fixture seed = 0x%08x, want 0x%08x", got, origLoginSeed)
	}
	if got := binary.LittleEndian.Uint32(wantB[0x14:]); got != origLoginSeed+1 {
		t.Fatalf("LOGIN B fixture seed = 0x%08x, want 0x%08x", got, origLoginSeed+1)
	}

	a, b := buildLoginPair(origLoginSeed)
	if hex.EncodeToString(a) != origLoginATemplateHex {
		t.Fatalf("LOGIN A mismatch\n got=%s\nwant=%s", hex.EncodeToString(a), origLoginATemplateHex)
	}
	if hex.EncodeToString(b) != origLoginBTemplateHex {
		t.Fatalf("LOGIN B mismatch\n got=%s\nwant=%s", hex.EncodeToString(b), origLoginBTemplateHex)
	}
}

// TestBuildLoginPairBSeedIsAPlusOne is a smoke check across a few
// seeds confirming the SDK invariant: LOGIN B's login_serial is
// always LOGIN A's + 1, and the rest of the buffer is independent
// of seed apart from the 4-byte field at [0x14:0x18].
func TestBuildLoginPairBSeedIsAPlusOne(t *testing.T) {
	seeds := []uint32{0, 1, 0x49a53e94, 0xDEADBEEF, 0xFFFFFFFE, 0xFFFFFFFF}
	for _, seed := range seeds {
		a, b := buildLoginPair(seed)
		if len(a) != 570 {
			t.Errorf("seed=0x%08x: len(A)=%d, want 570", seed, len(a))
		}
		if len(b) != 572 {
			t.Errorf("seed=0x%08x: len(B)=%d, want 572", seed, len(b))
		}
		gotA := binary.LittleEndian.Uint32(a[0x14:])
		gotB := binary.LittleEndian.Uint32(b[0x14:])
		if gotA != seed {
			t.Errorf("seed=0x%08x: A login_serial=0x%08x, want 0x%08x", seed, gotA, seed)
		}
		// B = A+1 with wraparound; uint32 addition already wraps.
		if gotB != seed+1 {
			t.Errorf("seed=0x%08x: B login_serial=0x%08x, want 0x%08x", seed, gotB, seed+1)
		}
	}
}

// TestBuildLoginPairLengths — formerly an init()-time panic; promoted
// to a test so a typo in any of the wrapper / trailer constants fails
// CI instead of taking down every binary that imports go2rtc on first
// run.  Lengths match what every captured PCAP shows on the wire
// (570 / 572 B).
func TestBuildLoginPairLengths(t *testing.T) {
	a, b := buildLoginPair(0)
	if len(a) != 570 {
		t.Errorf("len(loginA) = %d, want 570", len(a))
	}
	if len(b) != 572 {
		t.Errorf("len(loginB) = %d, want 572", len(b))
	}
}
