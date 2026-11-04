# USB connectivity smoke test

**Date:** 2026-11-02 (local) / 2026-11-03 (UTC)
**Result:** PASS. The phone app sent a test message, the Mac collector acknowledged
the same request ID, and the collector log has exactly one record for that ID.

## What this test shows, and what it doesn't

This test shows that the debug build of the AndroidNetGuard app can send an HTTP POST
to the local collector over an `adb reverse` USB tunnel, and that the app reports
success only when the server sends back the same request ID.

It does **not** cover Wi-Fi, mobile data, VPN behaviour or isolation, TLS, or any
security property. ADB reverse sends traffic over the USB cable through the adb
server on the Mac, so the phone's own network stack is mostly bypassed. No AI model
was involved.

## Setup

| Item | Value |
|---|---|
| Phone | REVVL, model `TMAF035G`, serial `DEVICE_SERIAL`, Android 13 (API 33) |
| App | `com.androidnetguard.app` 0.1.0 (debug build, versionCode 1) |
| Collector | `collector/collector.py` (Python 3.15.1, stdlib only), bound to `127.0.0.1:8765` |
| Tunnel | `adb -s DEVICE_SERIAL reverse tcp:8765 tcp:8765` |
| Endpoint in app | `http://127.0.0.1:8765/v1/test-message` |
| Cleartext | Allowed **only in the debug build**, only for `127.0.0.1`/`localhost` (`app/src/debug/res/xml/network_security_config_debug.xml`). The release APK has no such config. |

## The reported request

What the phone showed after the tap:

```
SUCCESS: server acknowledged this request ID
request_id=742f82f2-c9e9-4edc-b65a-07434e7bafb4
http=200
elapsed_ms=8
server_time=2026-11-03T02:14:52.437Z
```

The matching collector record (`collector/data/messages.jsonl`, line 4, the only line
with this ID):

```json
{"server_time":"2026-11-03T02:14:52.437Z","remote_addr":"127.0.0.1","request_id":"742f82f2-c9e9-4edc-b65a-07434e7bafb4","message":"AndroidNetGuard USB connectivity smoke test (harmless test data)","app_version":"0.1.0","client_time":"2026-11-03T02:14:52.426953Z","test_label":"usb-smoke"}
```

Collector access log line:

```
2026-11-03T02:14:52.437Z 127.0.0.1 "POST /v1/test-message HTTP/1.1" 200 -
```

App log lines (`adb logcat -s ANG-UsbSmoke:I`; the phone's clock is local time, UTC-8):

```
11-02 18:14:52.426 I/ANG-UsbSmoke(13944): send request_id=742f82f2-c9e9-4edc-b65a-07434e7bafb4 endpoint=http://127.0.0.1:8765/v1/test-message
11-02 18:14:52.435 I/ANG-UsbSmoke(13944): ACK request_id=742f82f2-c9e9-4edc-b65a-07434e7bafb4 http=200 elapsed_ms=8 detail=server_time=2026-11-03T02:14:52.437Z
```

The request ID, the `server_time` and the HTTP status are the same on the phone screen,
in the app log, in the collector JSONL record and in the access log.

## All records in the log at the time of checking

| # | server_time (UTC) | Source | test_label | request_id |
|---|---|---|---|---|
| 1 | 02:14:31.943 | Mac `curl` check | `mac-curl` | `d789701e-dc9a-4be6-947e-cc101e843acc` |
| 2 | 02:14:49.648 | Phone app | `usb-smoke` | `da758359-bdb6-4b31-8868-3e8f8657974e` |
| 3 | 02:14:51.617 | Phone app | `usb-smoke` | `64521d8e-a220-40c8-b3c5-fe14f6bd81af` |
| 4 | 02:14:52.437 | Phone app | `usb-smoke` | `742f82f2-c9e9-4edc-b65a-07434e7bafb4` (the reported one) |

- **No duplicate request IDs.** All four IDs are unique.
- **Three phone requests, not one.** Records 2–4 each have a matching `send`/`ACK` pair
  in the app log (same process, PID 13944), about 1–3 s apart. That fits the button
  being tapped three times. The screen shows only the latest result, which is
  record 4. All three were acknowledged.
- **How phone and Mac requests are told apart:** `remote_addr` is `127.0.0.1` for
  *every* record, because adb reverse delivers phone traffic from the Mac's own adb
  server. Source IP therefore can't separate them. What does separate them: the
  phone requests have `test_label=usb-smoke`, `app_version` and `client_time`, and
  each has a matching line in the app log. The Mac request has `test_label=mac-curl`
  and no app fields.
- The access log also has a Mac `GET /health` (200) and a deliberately invalid
  Mac POST (422), which correctly wasn't saved.

## About `elapsed_ms=8`

This is **one observation** of a single request, measured on the phone from just
before connecting until the response was read, over a USB tunnel to localhost.
It's **not a benchmark**: other requests in the same session took 24 ms and 7 ms.
`client_time` and `server_time` come from different clocks, so their difference
isn't a valid latency measurement either.

## How to reproduce

From the project root on the Mac:

```sh
source ./env.sh

# 1. Collector unit tests (12 tests)
python3 -I -m unittest discover -s collector -v

# 2. Start the collector, bound to localhost only (keep this terminal open)
python3 -I collector/collector.py --host 127.0.0.1 --port 8765 \
    --output collector/data/messages.jsonl 2>> collector/data/collector-access.log

# --- in a second terminal (source ./env.sh again) ---
curl -sS http://127.0.0.1:8765/health

# 3. Confirm exactly one authorized device, model TMAF035G
adb devices -l
S=DEVICE_SERIAL
adb -s $S shell getprop ro.product.model

# 4. Build and update the app in place (no uninstall, data kept)
./gradlew assembleDebug
adb -s $S install -r app/build/outputs/apk/debug/app-debug.apk

# 5. USB tunnel for this device only
adb -s $S reverse tcp:8765 tcp:8765
adb -s $S reverse --list

# 6. Launch the app, then tap "Send test message" once on the phone
adb -s $S shell am start -W -n com.androidnetguard.app/.MainActivity

# 7. Correlate (replace <ID> with the request_id shown on the phone)
adb -s $S logcat -d -v time -s ANG-UsbSmoke:I
grep -c '"request_id":"<ID>"' collector/data/messages.jsonl   # expect 1
grep    '"request_id":"<ID>"' collector/data/messages.jsonl

# Cleanup when done
adb -s $S reverse --remove tcp:8765
# stop the collector with Ctrl-C
```

`collector/data/` is git-ignored. The logs stay local and aren't committed.
