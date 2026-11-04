# Test definitions

The catalog contains five **connectivity or reliability checks** and one **wireless observation session**. None is a security-policy test.
A connectivity loss or a failure to recover is a reliability observation, **not** a security
violation. A timeout is never read as a policy "success".

Common request mechanics (pass criteria below apply to connectivity/reliability checks):

* Each request gets a fresh UUIDv4 `request_id`. Each run gets a `run_id` and each step a `step_id`.
* Requests are bounded: 5 s connect timeout, 5 s read timeout, a 4 KB request body and an 8 KB
  response. The message body is fixed, harmless text.
* **Configured mode** (`usb` or `wifi`) is what the test is set up for. **Observed transport** is
  Android's default-network report, recorded before and after every request. A LAN address alone
  never counts as Wi-Fi: Wi-Fi preconditions require the observed transport to be exactly `wifi`,
  so `wifi+vpn`, `cellular` and `none` all fail the precondition.
* A run is `passed` only when the app saw an ack with the **same ID** *and* the collector holds
  **exactly one** record for that ID with the matching run and step IDs.
* A configured endpoint that doesn't match the mode (loopback for Wi-Fi, LAN for USB) gives
  `execution_error`, and so does a host that the debug network config doesn't allow.
* **Cancel** always gives `inconclusive`.

| Test | Expected | Required evidence | Timeouts | Inconclusive when |
|---|---|---|---|---|
| `usb_connectivity`: USB connectivity check | Health ok; 1 message acked with the same ID | App attempt events; exactly 1 collector record, **peer on loopback** (adb reverse) | 5+5 s per request; run 30 s | Cancelled; no collector records for the run; record not on loopback |
| `wifi_connectivity`: direct Wi-Fi check | Transport exactly `wifi`; health ok; 1 message acked with the same ID | Transport before and after; 1 collector record whose **peer isn't loopback** | 5+5 s per request; run 30 s | Transport not exactly `wifi`; record arrived via loopback; cancelled; no collector records |
| `wifi_sequence`: bounded sequence | Every message acked with its own ID; transport stays `wifi` | One attempt per `seq`; 1 collector record per ID; no non-wifi `transport_changed` events | count 1–50, interval 200–5000 ms; run ≤ count×(interval+10 s)+20 s, max 10 min | Transport changes (the run stops); fewer attempts than the count; cancelled; no collector records |
| `app_restart`: restart then check | Baseline acked; a **new process** within 5 min resumes the run; post-restart message acked | ≥2 distinct `process_id`s; `run_resumed`; collector records for both messages | Restart window 5 min; Wi-Fi wait after restart 15 s | Baseline fails; not restarted in time; restart after the window; monotonic clock went backwards (reboot); no Wi-Fi after restart; cancelled |
| `wifi_reconnect`: user-assisted off/on | Baseline acked; Wi-Fi loss observed; Wi-Fi return observed; ≥1 of 3 recovery messages acked. The outage attempt is **informational** | `transport_changed` events; wait-step results; outage attempt outcome; collector records for baseline and recovery | Off wait 120 s; on wait 180 s; settle 5 s; 3 attempts 3 s apart | Baseline fails; user doesn't toggle in time; transport not `wifi` during recovery; cancelled |

`failed` for these tests means the preconditions held and the evidence contradicts the expected
behaviour, for example:
* no ack, or an ack carrying a different ID
* the collector stored the message but the app saw no ack
* the collector stored a duplicate record for one ID
* the app saw an ack but the collector has no record, while other records exist for the same run
* no recovery after Wi-Fi returned

How to restart the app for `app_restart`: swipe it away in Recents and reopen it, or from the Mac
run `adb -s $S shell am force-stop com.androidnetguard.app`, then `am start`. The app can't tell
which method was used.

The app never changes Wi-Fi or any other system setting. For `wifi_reconnect` you toggle Wi-Fi
yourself when the yellow prompt asks.

## Wireless observation

`wifi_observation` records ANDROID_WIFI_TELEMETRY and probe outcomes for 10–600 seconds.
Sampling intervals are 1–10 seconds; probe intervals are 2–30 seconds. A completed session
passes collection validation when records have the required source/field labels, at least
one valid RSSI value, and at least 50% of expected sampling slots. The 50% threshold is a
retrospective engineering criterion, not a validated research threshold. Failed probes and
signal drops are observations rather than a failed collection. Early cancellation, missing
valid RSSI, or insufficient coverage makes the session inconclusive. See EVIDENCE.md.

Cancellation behavior is evaluated separately: the interrupted session remains inconclusive
even when the cancellation mechanism passes its validation.

## Measurement terminology

Legacy fields named `freshness`, `samples_fresh`, and related keys compare consecutive
reported value tuples. `new_value` means the first or a changed tuple; `repeated` means an
identical tuple. Neither proves when the radio measured it, caching, or independence.
Callback age measures notification age only. Raw evidence and legacy field names are retained.
