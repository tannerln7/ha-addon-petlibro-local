package petlibro

import (
	"strings"
	"testing"
)

func TestRedactSourceURL(t *testing.T) {
	const uid = "PLAF20300000000ABCD0"
	for _, source := range []string{
		"petlibro://192.0.2.10?uid=" + uid + "&quality=hd",
		"petlibro://?uid=" + uid + "&subnet=192.0.2.0%2F24",
	} {
		redacted := redactSourceURL(source)
		if strings.Contains(redacted, uid) {
			t.Fatalf("redacted URL still contains UID: %s", redacted)
		}
		if !strings.Contains(redacted, "uid=REDACTED") {
			t.Fatalf("redacted URL does not identify the hidden UID field: %s", redacted)
		}
		if !strings.Contains(redacted, "quality=hd") && !strings.Contains(redacted, "subnet=") {
			t.Fatalf("redacted URL lost non-sensitive diagnostics: %s", redacted)
		}
	}
}

func TestRedactSourceURLDoesNotEchoMalformedInput(t *testing.T) {
	const malformed = "petlibro://camera?uid=SECRET%zz"
	if got := redactSourceURL(malformed); got != "petlibro://<redacted-invalid-url>" {
		t.Fatalf("redactSourceURL=%q", got)
	}
}
