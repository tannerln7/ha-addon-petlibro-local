package embedded

import (
	"errors"
	"slices"
	"sync"

	"github.com/AlexxIT/go2rtc/internal/api"
	"github.com/AlexxIT/go2rtc/internal/api/ws"
	"github.com/AlexxIT/go2rtc/internal/app"
	"github.com/AlexxIT/go2rtc/internal/exec"
	"github.com/AlexxIT/go2rtc/internal/ffmpeg"
	"github.com/AlexxIT/go2rtc/internal/hls"
	"github.com/AlexxIT/go2rtc/internal/http"
	"github.com/AlexxIT/go2rtc/internal/mjpeg"
	"github.com/AlexxIT/go2rtc/internal/mp4"
	"github.com/AlexxIT/go2rtc/internal/petlibro"
	"github.com/AlexxIT/go2rtc/internal/rtsp"
	"github.com/AlexxIT/go2rtc/internal/streams"
	"github.com/AlexxIT/go2rtc/internal/webrtc"
)

var (
	startMu sync.Mutex
	started bool
)

type module struct {
	name string
	init func()
}

func Start(configYAML string) error {
	startMu.Lock()
	defer startMu.Unlock()

	if started {
		return nil
	}

	app.InitEmbedded(configYAML)
	initModules()
	started = true
	return nil
}

func initModules() {
	for _, m := range []module{
		{"api", api.Init},
		{"ws", ws.Init},
		{"", streams.Init},
		{"http", http.Init},
		{"rtsp", rtsp.Init},
		{"webrtc", webrtc.Init},
		{"mp4", mp4.Init},
		{"hls", hls.Init},
		{"mjpeg", mjpeg.Init},
		{"exec", exec.Init},
		{"ffmpeg", ffmpeg.Init},
		{"petlibro", petlibro.Init},
	} {
		if app.Modules == nil || m.name == "" || slices.Contains(app.Modules, m.name) {
			m.init()
		}
	}
}

func AddStream(name, source string) error {
	if name == "" {
		return errors.New("go2rtc: stream name required")
	}
	if source == "" {
		return errors.New("go2rtc: stream source required")
	}
	_, err := streams.Patch(name, source)
	return err
}

func DeleteStream(name string) error {
	if name == "" {
		return errors.New("go2rtc: stream name required")
	}
	streams.Delete(name)
	return nil
}
