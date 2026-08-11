package petlibro

import (
	"testing"
	"time"
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
