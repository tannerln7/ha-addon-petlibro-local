# Development guide

## Repository layout

| Path | Purpose |
|---|---|
| `src/plaf203.py` | AppDaemon application, MQTT protocol model, and Home Assistant discovery |
| `src/apps.example.yaml` | Safe configuration template for local AppDaemon setup |
| `tests/test_protocol_3_1_48.py` | Protocol and regression tests for firmware 3.1.48 behavior |
| `tests/fixtures/` | Synthetic, sanitized protocol examples used by tests |
| `docs/protocol-capture-analysis.md` | Detailed capture-backed protocol notes |

The local `src/apps.yaml` and `capture/` directory are ignored. They may contain
device identifiers, network details, MQTT credentials, camera authorization
data, or account information and must not be committed.

## Development environment

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

AppDaemon is a development dependency because importing `src/plaf203.py`
requires its application and MQTT APIs. The test suite does not require a live
feeder, broker, Home Assistant instance, or internet connection.

## Run the tests

```bash
python -m pytest -q
```

For the standard-library runner:

```bash
python -m unittest discover -s tests -v
```

The fixture file contains invented identifiers and representative message
shapes. Tests compare parser and serializer behavior with those schemas; they
do not replay raw network traffic.

## Protocol-change workflow

1. Capture only the traffic needed to answer the protocol question.
2. Keep raw PCAP, logs, and decoded exports under the ignored `capture/`
   directory or outside the repository.
3. Assume captures are sensitive. MQTT CONNECT frames can expose credentials,
   while topics and payloads can expose device serials, MAC addresses, Wi-Fi
   names, account IDs, and camera authorization material.
4. Reduce a finding to the smallest synthetic fixture that reproduces the
   behavior. Replace every identifier and endpoint, and use `<redacted>` for
   credential-like values.
5. Add or update a focused regression test.
6. Update `docs/protocol-capture-analysis.md` when the protocol contract or the
   confidence in a finding changes.

Never log or persist `cameraAuthInfo` in diagnostic output. The implementation
redacts it before recording incoming payloads; preserve that behavior in future
debugging changes.

## Validation before committing

Run:

```bash
python -m pytest -q
python -m unittest discover -s tests -v
python -m py_compile src/plaf203.py tests/test_protocol_3_1_48.py
git diff --check
```

Also review `git status --short` and scan newly tracked text for real device
serials, MAC addresses, private network addresses, personal paths, credentials,
and raw capture artifacts. Do not stage `.venv`, caches, local `apps.yaml`, or
capture output.

## Contribution notes

- Keep changes focused and preserve compatibility with existing firmware when
  adding a newer message variant.
- Treat capture observations as evidence, not universal firmware guarantees.
- Prefer optional parsing for newly observed fields unless the protocol proves
  they are mandatory.
- Include a regression test and update the relevant documentation with behavior
  changes.
