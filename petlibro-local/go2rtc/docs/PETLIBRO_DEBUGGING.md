# Petlibro debugging guide

Use this guide when a Petlibro stream fails to start, advertises an unexpected
resolution, stalls, or produces decoder errors. Start with compact health logs;
packet traces and plaintext dumps are intentionally opt-in because they are
high volume and may contain device or session data.

## Recommended diagnostic baseline

The following options are a useful PLAF203 HD baseline:

```yaml
log:
  level: info
  petlibro: debug

streams:
  petlibro_feeder: petlibro://192.168.1.42?uid=PLAF20300000000ABCD0&quality=hd&ack=hybrid&send_delay_ctrl=1&hd_probe_wait_ms=15000&verbose=1
```

Replace the placeholder IP and UID. Use one viewer at a time while comparing
protocol changes so reconnects and concurrent consumers do not obscure the
timeline.

## Diagnostic URL options

### Compact logging

| Option | Default | Effect |
| --- | --- | --- |
| `verbose=1` | off | Logs effective debug configuration, bootstrap state, probe selection, SPS changes, and five-second health statistics |
| `trace_ack=1` | off | Logs every maintenance ACK and its plaintext body; requires `verbose=1` |
| `trace_frag=1` | off | Logs important fragment/frame decisions; requires `verbose=1` |
| `trace_frameinfo=1` | off | Logs frame-info observations and changes; requires `verbose=1` |
| `trace_packets=1` | off | Logs decrypted packets and accepted/rejected extended-media candidates; requires `verbose=1` |

Enable traces one at a time unless full wire correlation is necessary.

### Stream and probe controls

| Option | Default | Effect |
| --- | --- | --- |
| `quality=hd` or `quality=sd` | `hd` | Selects the requested stream and builds the corresponding stream-control command |
| `audio=true` | `false` | Requests AAC audio after video start |
| `strict=1` | off | Drops damaged IDRs and dependent P-frames until a clean GOP begins; may freeze on genuine loss |
| `send_delay_ctrl=1` | off | Sends the AVAPI data-delay IOCtrl immediately before `IPCAM_START` |
| `hd_probe_wait_ms=N` | `0` | For HD, waits up to 60000 ms for a higher-resolution SPS before publishing the track |
| `streamctrl_variant=legacy`, `standard`, or `none` | `legacy` | Selects captured Petlibro control, standard AVAPI control, or no stream-control request |
| `streamctrl_quality=N` | derived from `quality` | Overrides the stream-control quality byte with a value from 0 through 255 |

`streamctrl_variant` and `streamctrl_quality` are protocol experiments, not
general tuning knobs. Record both the outgoing body and camera response when
using them.

### ACK/window controls

| Option | Default | Effect |
| --- | --- | --- |
| `ack=high` | `high` | Uses highest-observed sequence behavior compatible with the original implementation |
| `ack=contig` | — | Uses only the highest contiguous receive watermark |
| `ack=hybrid` | — | Sends contiguous watermark and highest observed sequence as the two ACK endpoints |
| `ack=hybrid-rev` | — | Reverses the hybrid endpoint order |
| `ack=prev-contig-curr-high` | — | Explicit contiguous/high field-role candidate |
| `ack=prev-sent-curr-high` | — | Uses the previously sent current value and current high-water value |
| `ack=lag-high` | — | Caps forward progress near the contiguous watermark |
| `ack=lag-hybrid` | — | Hybrid candidate with bounded lag |
| `ack_lag_window=N` | `8` | Sets the packet window used by lag modes; valid range 1 through 65535 |
| `ack_interval_ms=N` | `25` | Sets maintenance ACK cadence; valid range 1 through 65535 ms |
| `ack_repeat_unchanged=1` | off | Sends an ACK at every cadence even when its sequence fields did not change |

The field semantics are inferred, not vendor-confirmed. On the tested PLAF203,
holding the current/high endpoint behind a hole can exhaust the sender window
and stall media. `ack=hybrid` is the preferred diagnostic mode because it keeps
high-water progress while preserving the independent contiguous watermark in
the other field. Do not interpret `avPrev` as a proven retransmission request.

## Reading the five-second stats line

With `verbose=1`, each interval reports input, assembly, loss, media-header, and
ACK state. Focus on these groups:

| Field | Interpretation |
| --- | --- |
| `video: frames in -> out` | Frames reaching the assembler and frames emitted to go2rtc |
| `qDrops reader/emit` | Local userspace receive or output-channel overflow; both should normally be zero |
| `loss: frames/idr/p/missing` | Frames with fragment loss and the total missing fragments inferred by assembly |
| `fragIdxGap` | Fragment indices skipped within a frame |
| `expectedDataShortfall` | End fragment arrived before all expected data fragments |
| `zeroDataHardDrop` | A multi-fragment frame ended with no usable data fragments |
| `wrongStreamDrop` | Frame-info selection byte did not match the requested quality |
| `strictIDRDrop/strictPDrop` | Frames suppressed by strict GOP policy |
| `deferredDrop` | Packet arrived after the assembler output cursor had already passed it |
| `extendedMedia parsed/rejected` | Alternate 44-byte media-header candidates accepted or rejected |
| `unknown0c08/unknown0c0d` | Remaining unparsed members of the common extended-media families |
| `seqAssembled/seqUnhandled` | Recognized wire sequences delivered to assembly or seen but not handled |
| `ack watermark/high/pending` | Highest contiguous receive sequence, highest observed sequence, and unresolved sequences above the watermark |
| `ack prev/current` | The actual low-16-bit fields most recently sent for the selected ACK mode |

Healthy live behavior is not defined by one number, but these are useful signs:

- `readerDrops=0` and `emitDrops=0`
- extended-media candidates are parsed rather than left as unknown `0c08` or
  `0c0d`
- `missingFragmentsTotal`, gapped IDRs, and decoder errors remain near zero
- ACK watermark follows high-water with little or no pending backlog
- the expected SPS transition is logged before probe selection when
  `hd_probe_wait_ms` is enabled

A repeating `stats: stalled` line with control-only packets means the session is
alive but the camera is no longer sending media. Compare `watermark`, `high`,
and ACK `current` before changing assembly policy.

## Common failure modes

### Login timeout

`petlibro: LOGIN_RESP timeout` means the camera did not acknowledge the login
pair. Check, in order:

1. The camera completed provisioning in the Petlibro app and remains online.
2. The 20-character UID is exact.
3. A fixed camera IP is current, or discovery traffic can reach the camera's
   broadcast domain.
4. UDP port `32761` is not blocked between go2rtc and the camera.

### RTSP returns 404

A `404 Not Found` on the configured RTSP name commonly means the Petlibro
producer failed during startup or codec probe, so go2rtc could not expose a
usable stream. Inspect the go2rtc log before the RTSP request for:

- bootstrap or IOCtrl failure
- probe timeout or EOF
- absence of an SPS-bearing IDR
- repeated startup retries

### HD request advertises 640x360

The camera can begin with a 640x360 SPS and switch to 1920x1080 several seconds
later. Enable Petlibro debug logging and look for `SPS resolution`, `spsChange`,
and `probe selected`. Use a bounded `hd_probe_wait_ms`, such as 15000, if the
consumer must receive an HD SDP from its first RTSP probe.

### Corrupt H.264 or concealment warnings

Corruption plus nonzero fragment-loss counters indicates an incomplete access
unit, not necessarily a viewer problem. Check extended-media reject counters
and sequence backlog first. `strict=1` can suppress damaged GOPs but is a
presentation policy, not packet recovery, and may replace corruption with a
freeze.

## Plaintext packet captures

Add capture paths to the Petlibro URL:

```text
dump_d2c_plain=/tmp/petlibro_d2c.dat
dump_c2d_plain=/tmp/petlibro_c2d.dat
```

`dump_plain` remains an alias for `dump_d2c_plain`. Each file is created or
truncated when the client starts.

The formats are intentionally simple and replayable:

```text
D2C record:
  uint32 little-endian decrypted datagram length
  decrypted datagram bytes

C2D record:
  uint64 little-endian Unix timestamp in nanoseconds
  uint32 little-endian plaintext inner-body length
  plaintext inner-body bytes
```

These files may contain camera identifiers, session values, media, network
metadata, or protocol state. Store them outside the repository, review before
sharing, and delete them when no longer needed.

## Offline replay and summaries

Replay a D2C dump through the real unexported parser and assembler without a
camera:

```bash
PETLIBRO_REPLAY_DUMP=/tmp/petlibro_d2c.dat \
PETLIBRO_REPLAY_QUALITY=hd \
go test ./pkg/petlibro -run TestReplayPlainDump -v -count=1
```

Set `PETLIBRO_REPLAY_STRICT=1` to compare strict GOP behavior against the exact
same packet sequence. `PETLIBRO_D2C_DUMP` is accepted as an alias for the replay
path.

Print a stable inventory of recognized and unknown packet families:

```bash
PETLIBRO_C2D_DUMP=/tmp/petlibro_c2d.dat \
PETLIBRO_D2C_DUMP=/tmp/petlibro_d2c.dat \
go test ./pkg/petlibro -run TestDumpPacketSummary -v -count=1
```

Print only decoded maintenance ACK bodies and their timing:

```bash
PETLIBRO_C2D_DUMP=/tmp/petlibro_c2d.dat \
go test ./pkg/petlibro -run TestDumpAckSummary -v -count=1
```

The dump-backed tests skip when their environment variables are unset and do
not require a live camera.

## Extended media packets

The PLAF203 alternates between a normal 36-byte media header and a structurally
validated 44-byte header. The extended path recognizes inner families `0c08`,
`0c09`, `0c0c`, and `0c0d`; the common `0c08` packets carry data fragments and
`0c0d` packets commonly carry the final fragment and frame-info bytes.

When `trace_packets=1`, each candidate logs its type, acceptance, channel,
`subWire`, fragment counts, payload length, frame number, `nextFrameLike`, extra
length, and rejection reason. A valid extended packet is marked received for
ACK purposes before assembly decisions, preventing parser rejection from
creating an artificial ACK hole.

See the [development guide](DEVELOPMENT.md#media-header-layouts) for the current
offset map and naming rules.

## Preparing a useful report

For a bounded live test, retain:

- sanitized Petlibro URL options
- camera model and firmware
- go2rtc bootstrap, SPS, probe, and `stats:` lines
- viewer/FFmpeg diagnostics from the same interval
- `TestReplayPlainDump` and `TestDumpPacketSummary` output
- the smallest unknown packet examples needed to support a new parser rule

Do not upload raw dumps by default. If a maintainer needs one, agree on a secure
transfer and disclose what identifiers or media it contains.
