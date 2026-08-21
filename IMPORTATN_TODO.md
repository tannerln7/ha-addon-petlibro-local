Yep, we found the last loose wire. The **OTA itself succeeded**. The scrolling bar is stale add-on state, and your `16:34:57` timeout is the smoking gun.

`request_install()` sets `_busy = True` and immediately publishes `in_progress=True` *before* launching the executor worker.  Then, right when the supervisor stopped `0.3.0` for the swap, the add-on logged:

```text
16:34:57 ERROR ... state agent request timed out
```

The client uses the same short state-agent HTTP timeout for `submit_update()` as ordinary API requests.

And here is the killer detail: AppDaemon's current `submit_to_executor()` only dispatches the result callback if `f.result()` succeeds. If the worker raises, AppDaemon logs the exception and **does not invoke our callback at all**. 

So the sequence was almost certainly:

```text
HA Install
   ↓
_publish in_progress=true
_busy=true
   ↓
OTA bytes successfully reach feeder
   ↓
feeder stages 0.3.1
   ↓
supervisor sees pending and stops 0.3.0
   ↓
add-on's HTTP operation times out during that transition
   ↓
worker raises StateAgentTimeout
   ↓
AppDaemon logs exception
   ↓
_on_install_finished() NEVER RUNS
   ↓
_busy stays true
retained MQTT state stays in_progress=true
   ↓
HA spinner forever 🌀
```

Meanwhile the feeder happily finished the transaction anyway.

### First, clear the current stale state

**Restart add-on 0.3.5 once.**

That resets `_busy`, then the normal connection check should discover:

```text
installed_version=0.3.1
latest_version=0.3.1
update_available=False
in_progress=False
```

and the HA update entity should settle to **Up to date**.

A manual Check probably won't help right now because the stuck `_busy=True` causes checks to be skipped. The code explicitly does that.

### Before the next OTA, we should fix this properly in 0.3.6

There are really **three related fixes**.

First, wrap executor workers ourselves so exceptions become callback results. `_extract_result()` already has code intended to handle `Exception`, but today an actual executor exception never reaches it because AppDaemon intercepts it first.

Something like:

```python
def _submit_worker(self, worker, finished_callback) -> None:
    def guarded_worker():
        try:
            return worker()
        except Exception as exc:
            return exc

    self.ad.submit_to_executor(
        guarded_worker,
        callback=lambda result, **_kwargs: finished_callback(result=result),
    )
```

Then use that for check, install, and status polling.

Second, an **OTA submission timeout is ambiguous**, not necessarily a failed update. In our case it very clearly wasn't a failure. The feeder had already committed the candidate transaction before the HTTP client lost contact.

So `_install_latest()` should treat `StateAgentTimeout` from `submit_update()` as:

```text
"I don't know whether I received the HTTP acknowledgement.
Start polling the transaction."
```

not:

```text
"Update failed."
```

After a positively accepted POST, we should also unconditionally enter polling rather than immediately depending on another API request surviving the swap.

Third, the polling path itself needs to tolerate the expected reboot/swap window. Right now `_safe_update_status()` converts an unreachable State Agent into a fake `idle` status; then `_poll_feeder_update_status()` may immediately call `/v1/version`.  During the exact few seconds where the agent is intentionally down, that's backwards.

During an OTA, temporary State Agent unavailability should mean:

```python
in_progress=True
retry in 5 seconds
```

Only when we can actually observe a terminal status **and** read the running version should we publish:

```python
in_progress=False
```

For this successful update that would have looked like:

```text
POST timeout
    ↓
keep HA in_progress=true
    ↓
poll: agent unavailable
    ↓
keep true, retry
    ↓
poll: status idle/update_applied
    ↓
GET /v1/version → 0.3.1
    ↓
publish installed=0.3.1 latest=0.3.1 in_progress=false
    ↓
HA spinner stops automatically
```

That's the behavior we actually want.

I'd also add a hard polling deadline so even some bizarre future failure cannot leave the HA entity spinning forever.

So: **restart 0.3.5 now to clear the cosmetic stuck state.** If it comes back as `0.3.1 / 0.3.1 / Up to date`, we've conclusively closed the first OTA.

Then I'd make this the scope of add-on `0.3.6`:

```text
- executor exception delivery
- ambiguous OTA POST timeout recovery
- transient agent-unavailable polling
- positive terminal-version confirmation
- finite polling timeout
- regression tests reproducing this exact 0.3.0 → 0.3.1 race
```

No State Agent update needed. It stays at `0.3.1`.

The dragon is dead. It just fell on the progress indicator on the way down. 🐉📊
