// Package petlibro registers the `petlibro://` source URL scheme with
// go2rtc's stream router.  Unlike Wyze/Tapo we do NOT need a cloud
// account: the camera is reached directly over LAN by UID + IP.
//
// Example go2rtc.yaml:
//
//	streams:
//	  cam1: petlibro://<camera-ip>?uid=<20-char-UID>&audio=true
//	  cam2: petlibro://?uid=<20-char-UID>&subnet=192.168.1.0/24&quality=sd
//
// The UID is the 20-character identifier printed on the camera's
// back-label or visible in the Petlibro app under camera details.  If
// the URL omits the host, go2rtc discovers the local camera IP by UID.
//
// The `quality=hd|sd` query parameter selects the corresponding
// stream-control body and filters the media frames emitted by the
// camera. HD sessions may begin with a low-resolution SPS before the
// camera switches profiles; `hd_probe_wait_ms` can provide a bounded
// probe-stabilization window. See internal/petlibro/README.md.
package petlibro

import (
	"github.com/AlexxIT/go2rtc/internal/app"
	"github.com/AlexxIT/go2rtc/internal/streams"
	"github.com/AlexxIT/go2rtc/pkg/core"
	"github.com/AlexxIT/go2rtc/pkg/petlibro"
)

func Init() {
	log := app.GetLogger("petlibro")
	// Inject the zerolog instance into the library so its diagnostic
	// messages reach the same sink as the rest of go2rtc.  The pkg/
	// side defaults to a Nop logger so unit tests don't depend on
	// internal/ being loaded.
	petlibro.SetLogger(log)
	streams.HandleFunc("petlibro", func(rawURL string) (core.Producer, error) {
		log.Debug().Msgf("petlibro: dial %s", rawURL)
		return petlibro.NewProducer(rawURL)
	})
}
