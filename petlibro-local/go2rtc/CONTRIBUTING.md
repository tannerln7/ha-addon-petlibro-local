# Contributing

Contributions are welcome, especially test results from additional Petlibro
models and firmware versions. Keep reports evidence-based: this protocol is
clean-room reverse engineered, and similar-looking packet fields are not always
semantically equivalent.

## Before changing code

1. Read the [development guide](docs/DEVELOPMENT.md).
2. For stream failures or protocol changes, follow the
   [debugging guide](docs/PETLIBRO_DEBUGGING.md) and preserve a reproducible
   plaintext dump outside the repository.
3. Check whether the behavior belongs in `pkg/petlibro` (transport and media
   implementation) or `internal/petlibro` (go2rtc registration and logging).
4. Keep ACK/window experiments, stream-control experiments, and assembler
   policy changes separate when possible so results remain attributable.

## Reporting a problem

Include:

- Camera model and firmware version
- Whether the camera and go2rtc host share a broadcast domain
- The Petlibro URL options used, with the UID and IP address redacted
- The bootstrap, probe-selection, SPS-change, and five-second `stats:` lines
- FFmpeg or client errors with timestamps
- Whether the same session reproduces through `TestReplayPlainDump`

Do not post a real UID, public IP, credentials, or an unreviewed plaintext dump.
The dumps can contain device and session information even though media payloads
are not account passwords.

## Making a change

- Follow existing Go formatting and naming conventions.
- Add a focused regression test for parser, assembler, ACK, or bootstrap fixes.
- Keep generated binaries, packet captures, logs, and local `go2rtc.yaml` files
  out of Git.
- Update the relevant documentation when adding a URL option, changing a wire
  assumption, or altering setup behavior.
- Avoid unrelated refactors in protocol patches; they make live comparisons
  and dump replay harder to interpret.

## Validation

Run the package checks first, followed by the repository build:

```bash
gofmt -w path/to/changed.go
go test ./pkg/petlibro -count=1
go test -race ./pkg/petlibro -count=1
go build -o ./go2rtc .
git diff --check
```

Remove the local `go2rtc` build artifact after testing. If a change needs a live
camera, report the model, test duration, selected URL options, observed SPS
resolution, loss counters, and decoder result.

## Pull requests

Keep each pull request focused on one diagnosable behavior. Describe the wire
evidence, the previous result, the new result, and the exact commands used for
validation. Do not include private captures or personal configuration.
