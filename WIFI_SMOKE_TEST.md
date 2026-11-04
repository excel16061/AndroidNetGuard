# Direct Wi-Fi connectivity smoke test

**Date:** 2026-11-03 (UTC)
**Result:** PASSED. The dashboard derived this from both the app evidence and the
collector evidence. The phone sent a test message over its Wi-Fi interface straight
to the Mac collector's LAN address. No adb reverse or forward mapping was active.
The collector stored exactly one record for the request ID, and the app received an
ack carrying that same ID.

## What this shows, and what it doesn't

It shows that the debug build (0.2.0) can reach a collector bound to the Mac's Wi-Fi
LAN address on the local network, and that the peer address the collector saw was the
phone's Wi-Fi address, not loopback.

It does **not** show anything about VPN behaviour, network isolation, firewall policy,
or any other security property. No security-policy test exists yet. No AI model was
involved.

## How the run was carried out (it differs from the original plan)

The original plan was for you to type the endpoint and tap **Send test message**
in app 0.1.0. No phone request reached that collector (0.1.0, bound to
`192.0.2.6:8765`) within the 10-minute wait. Its log, `collector/data/wifi-collector-access.log`,
holds only the startup line and the Mac's own `/health` check. That collector was then
stopped. The log is kept unchanged, and `collector/data/wifi-messages.jsonl` was never
created.

The direct Wi-Fi check was then run with app **0.2.0**, using the `wifi_connectivity`
test. It was started from the Mac with the debug-only adb trigger (`trigger=adb_intent`
is recorded in the run), not by a screen tap. The collector **0.2.0** wrote to a new
session directory.

## Network facts (read-only checks just before the run)

| Item | Value | How it was obtained |
|---|---|---|
| Mac Wi-Fi interface | `en0` | `networksetup -listallhardwareports` (Wi-Fi → en0), the default route's interface |
| Mac LAN address | `192.0.2.6/24` | `ipconfig getifaddr en0` |
| Phone Wi-Fi interface / address | `wlan0` `192.0.2.196/24` | `adb shell ip -4 -o addr show` |
| Common gateway | `192.0.2.1` | Mac default route; phone `wlan0` table route |
| Phone Wi-Fi state | enabled and connected (SSID not recorded) | `adb shell cmd wifi status` (SSID redacted) |
| adb reverse / forward | none / none | `adb -s DEVICE_SERIAL reverse --list`, `forward --list` |
| macOS Application Firewall | off (state 0). Read only, not changed by this work | `socketfilterfw --getglobalstate` |
| Collector listeners | `192.0.2.6:8765` and `127.0.0.1:8765`, no wildcard bind | `lsof -iTCP:8765 -sTCP:LISTEN` |

No macOS firewall or local-network permission prompt appeared during the test.

## Debug-only cleartext exception

`app/src/debug/res/xml/network_security_config_debug.xml` permits cleartext HTTP
only to `127.0.0.1`, `localhost` and `192.0.2.6`. Its `base-config` blocks
cleartext everywhere else. The release APK contains no network-security config and no
`usesCleartextTraffic` attribute, so the targetSdk 36 default (cleartext blocked) applies.
This was checked with `aapt2 dump xmltree` and `unzip -l`.

## The verified request

| Field | Value |
|---|---|
| run_id | `cf710e48-9342-47c4-aa44-f67d16d811e5` |
| health request_id (app-side only) | `859fe13b-08b0-47c7-877b-6b8e8a372bef` |
| message request_id | `03010d19-daba-4be1-94b3-031fe8845de8` |
| step_id | `c1816727-aa89-4843-9afd-34309a0ea741` |
| configured mode | `wifi`, endpoint `http://192.0.2.6:8765` |
| observed transport (Android default network) | `wifi` before and after both requests |
| app outcome | `ack`, HTTP 200, ack_request_id identical to request_id |
| collector record count for this ID | **1** |
| observed peer (collector side) | `192.0.2.196:53096` → `192.0.2.6:8765` |

Collector record (`evidence/server/20261103T185250Z_b7b04955/messages.jsonl`):

```json
{"server_time":"2026-11-03T18:53:35.290Z","server_mono_ns":110117301112166,"remote_addr":"192.0.2.196","remote_port":53096,"server_addr":"192.0.2.6","server_port":8765,"session_id":"b7b04955-a992-4853-ab56-5232230587a7","request_id":"03010d19-daba-4be1-94b3-031fe8845de8","message":"AndroidNetGuard connectivity check (harmless test data)","app_version":"0.2.0","client_time":"2026-11-03T18:53:35.234677Z","run_id":"cf710e48-9342-47c4-aa44-f67d16d811e5","seq":1,"step_id":"c1816727-aa89-4843-9afd-34309a0ea741","test_id":"wifi_connectivity","test_label":"wifi_connectivity","test_mode":"wifi","transport":"wifi"}
```

Collector access log (same session):

```
2026-11-03T18:53:35.223Z 192.0.2.196:53094 -> 192.0.2.6:8765 "GET /health HTTP/1.1" 200 -
2026-11-03T18:53:35.291Z 192.0.2.196:53096 -> 192.0.2.6:8765 "POST /v1/test-message HTTP/1.1" 200 -
```

App evidence (excerpt from `evidence/device/20261103T185401Z_29e4ed6b/events.jsonl`, pulled with `run-as`):

```
attempt_started  health  859fe13b-…  observed_transport=wifi
attempt_finished health  859fe13b-…  health_ok HTTP 200  elapsed_ms=422  after=wifi
attempt_started  message 03010d19-…  observed_transport=wifi
attempt_finished message 03010d19-…  ack HTTP 200 ack_request_id=03010d19-…  elapsed_ms=50  after=wifi
run_finished     passed (app-side, provisional)
```

The dashboard's final derivation was `passed`: "step 2 message #1: ack with same ID and one
matching collector record". Its preconditions were observed transport exactly `wifi` and a
peer address that isn't loopback.

## Limitations

- "Observed transport" is what Android reports for the default network. Nothing
  independent confirms it. The collector-side peer address `192.0.2.196`, the phone's
  `wlan0` address, is consistent with Wi-Fi but doesn't prove it by itself.
- The elapsed times (422 ms for the first health request, 50 ms for the message) are
  **single observations** measured on the phone's monotonic clock, not benchmarks.
  Phone and Mac wall clocks aren't synchronised, and their timestamps are never
  subtracted from each other.
- The cleartext exception names one DHCP-assigned address. If the Mac's address
  changes, the debug config must be updated and the app rebuilt.
- No VPN, isolation, firewall or other security claim is made.

## Start and stop commands

```sh
cd AndroidNetGuard && source ./env.sh
S=DEVICE_SERIAL
ipconfig getifaddr en0                         # confirm the Mac LAN address (must match the debug config)
adb -s $S reverse --list                       # must print nothing for a direct Wi-Fi test

# start the collector (new session dir under evidence/server/)
python3 -I collector/collector.py --host 192.0.2.6 --host 127.0.0.1 --port 8765 &
# start the dashboard (localhost only)
python3 -I dashboard/dashboard.py --port 8770 --serial $S &

# run the test: tap Tests → "Direct Wi-Fi connectivity check" → Start, or (debug build only):
adb -s $S shell am start -n com.androidnetguard.app/.MainActivity \
    --es ang.start_test wifi_connectivity --es ang.wifi_endpoint http://192.0.2.6:8765

# import the evidence and view it: open http://127.0.0.1:8770/ and click "Import from connected phone"

# stop
kill %1 %2        # or Ctrl-C in each terminal
```
