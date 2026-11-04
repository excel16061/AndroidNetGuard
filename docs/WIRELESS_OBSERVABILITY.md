# Wireless observability (ANDROID_WIFI_TELEMETRY)

The phone app has an **opt-in** session type, `wifi_observation`, that records what Android's
public Wi-Fi APIs report about the current connection. These records are labelled
`ANDROID_WIFI_TELEMETRY`. They are **not** channel state information (CSI): Android exposes no CSI
to apps. Nothing here supports pose, breathing, heart-rate or through-wall sensing, and none of that
is implemented or claimed.

## What is collected, and how

| Field | API (official Android reference) | Unit | Notes |
|---|---|---|---|
| RSSI | `WifiInfo.getRssi()` | dBm | Values ≤ −127 (AOSP invalid sentinel) or ≥ 0 are recorded as `invalid`, with no value |
| Frequency | `WifiInfo.getFrequency()` | MHz | ≤ 0 → `unavailable` |
| Link speed | `WifiInfo.getLinkSpeed()` | Mbps | `LINK_SPEED_UNKNOWN = -1` → `unknown` |
| Tx / Rx link speed | `getTxLinkSpeedMbps()` / `getRxLinkSpeedMbps()` (API 29+) | Mbps | Below API 29 → `not_supported_api` |
| Wi-Fi standard | `getWifiStandard()` (API 30+) | — | `WIFI_STANDARD_UNKNOWN` → `unknown` |
| Callback signal strength, validated flag, transports | `NetworkCallback.onCapabilitiesChanged` → `NetworkCapabilities.getSignalStrength()`, `hasCapability(NET_CAPABILITY_VALIDATED)`, `getTransportInfo()` (API 29+) | dBm | Event-driven. Each sample records the age of the last callback |
| SSID / BSSID | **not read** | — | Recorded as `not_collected` |

The values come from two sources:
1. **Polling:** `WifiManager.getConnectionInfo()`. It's deprecated in API 31, but the reference
   says apps "can continue using this API". It needs the normal (install-time)
   `ACCESS_WIFI_STATE` permission.
2. **Listening:** a listen-only `registerNetworkCallback` for Wi-Fi networks, registered **only
   for the duration of a session**. It never requests or binds a network. As the reference
   advises, the callback reads the `NetworkCapabilities` argument it's given and doesn't call
   synchronous `ConnectivityManager` methods.

**Permissions:** `INTERNET`, `ACCESS_NETWORK_STATE` (already present), and `ACCESS_WIFI_STATE`
(added). No location or `NEARBY_WIFI_DEVICES` permission is requested, and
`FLAG_INCLUDE_LOCATION_INFO` isn't used. Per the `WifiInfo` and `FLAG_INCLUDE_LOCATION_INFO`
references, location-sensitive fields stay redacted by the platform on Android 12+: SSID becomes
`<unknown ssid>`, BSSID and MAC become `02:00:00:00:00:00`, and the network ID becomes −1. On
the test phone, `getApplicableRedactions()` reported `7`.

## Explicit value statuses

Every measured field is stored as `{value, unit, status}`. A value appears **only** with status
`ok`; every other status leaves `value: null`:

`ok` · `unknown` (API sentinel) · `unavailable` (not connected / not provided) · `invalid`
(physically implausible) · `permission_denied` (a `SecurityException` from the poll) ·
`not_supported_api` · `not_collected` (deliberately not read).

## Sample completeness vs telemetry freshness

These are two separate measures, reported in separate columns and tiles:

* **Sample completeness:** records per expected sampling slot (duration ÷ interval). It answers
  "did the session collect when it was supposed to?" Observation sessions need ≥ 50 % to count
  as completed. That threshold was added **retrospectively** in analysis v7, after all sessions had
  been recorded. It's a judgement call, not preregistered or empirically validated, and the current
  evidence (every completed session at 100 %) can't show whether it's a good value. See
  `docs/ANALYSIS_CHANGELOG.md`.
* **Telemetry freshness:** fresh vs repeated values. It answers "did the platform report new
  values?" Repeated values may be cached and are excluded from baselines. Freshness has no
  pass/fail threshold.

## Timing and sampling limits

* Every record carries `mono_ns`, the phone's `SystemClock.elapsedRealtimeNanos()`. Charts use
  seconds since `run_started` on that clock.
* The Mac collector's records are linked to probes **by request ID**. Phone and Mac clocks are
  never subtracted from each other.
* **A repeated value is not a fresh sample.** Android doesn't say how often the values it returns
  are refreshed. A poll that returns exactly the same fields as the previous poll is marked
  `freshness: "repeated"` and is left out of baselines and detection. On the test phone, 6 of 10
  polls in the smoke session were repeats.
* Callback data isn't delivered on every RSSI change. Its age is recorded, and values older than
  30 s are marked `stale`.
* The loop is single-threaded. While a probe request is blocked (up to 5 s connect plus 5 s
  read), sampling pauses. Missed sampling slots are skipped, not back-filled, and show as gaps in
  `mono_ns`.
* Bounds: duration 10–600 s, sampling every 1–10 s, probes every 2–30 s. The session can be
  cancelled, and the NetworkCallback is always unregistered afterwards (unit-tested).

## Collector health checks and failure attribution

There's a health check at the start and end of each session, and **after every failed probe**.
Each failed probe gets the most specific label the evidence supports:

| Evidence | Label |
|---|---|
| Collector stored the message | `delivered_not_acknowledged` |
| No collector records loaded for this run | `missing_server_evidence` |
| HTTP error status received | `server_responded_with_error (HTTP n)` |
| Phone not on Wi-Fi before or after | `phone_not_on_wifi` |
| Follow-up health check succeeded | `message_failed_while_collector_reachable` |
| Follow-up health check also failed, phone on Wi-Fi | `collector_unreachable_on_wifi` — collector down **or** LAN path failure, *not distinguishable from this evidence* |

## Baseline analysis (`dashboard/wireless.py`, wireless analysis v2)

These are transparent rules, not a trained model. Outputs are **observations** or **anomaly
alerts**. They don't name a cause and aren't attack detections.

| Rule | Threshold | Rationale |
|---|---|---|
| Baseline | median RSSI of the first 5 **fresh** samples (minimum 3, otherwise "insufficient data") | A median resists single outliers; fresh-only avoids counting cached repeats |
| Rolling window | median of the last 3 fresh samples | Smooths ±1–2 dB jitter while still reacting within a few samples |
| Signal degradation alert | rolling median ≤ baseline − 10 dB | 10 dB is a tenfold power drop, clearly larger than the jitter seen on a stationary phone |
| Weak / very weak observation | rolling median < −75 / < −85 dBm | Common rule-of-thumb bands for Wi-Fi signal quality, not validated for this device and meant to be tuned |
| Link-speed drop alert | rolling median link speed ≤ 50 % of baseline | Link rate adapts to signal conditions; halving is a coarse, explainable trigger |
| Connectivity disruption alert | transport leaves `wifi`; Wi-Fi `onLost`; ≥ 2 consecutive failed probes | Each is directly observed; two probes avoid alerting on a single dropped request |
| Internet-validation-lost alert (v2) | `NET_CAPABILITY_VALIDATED` absent on the Wi-Fi network | Android documents this capability as *internet* validation, so it is reported separately from LAN reachability. In stationary session 1 it dropped for about 31 s while every LAN probe was still acknowledged |

Every derived output records the parameters, and they're listed on the `/wireless` page.

## Dashboard

* **Run page** of a `wifi_observation` run:
  * stat tiles;
  * RSSI chart: fresh samples filled, repeats hollow, missing values as `×` with their status,
    and no interpolation across gaps;
  * baseline line and alert shading;
  * transport and capability event markers;
  * request-duration chart, with failed requests shown as ✕ plus their attribution;
  * alert table, probe-outcome table, and a full telemetry table view.
* **`/wireless`** compares sessions (label, duration, fresh/repeated counts, RSSI median, min and
  max, frequency, link speed, probes, health checks, transport changes, alerts), shows the
  detector parameters, and shows the external CSI status. Empty states are shown until data exists.
* Charts are server-rendered SVG, because the page's CSP allows no JavaScript. Hover uses native
  SVG `<title>` tooltips on enlarged hit targets. Each chart has one data series in the reference
  blue, and request outcomes use the fixed status colours together with symbols and labels.
  (The palette validator script from the charting guidance needs Node.js, which isn't installed;
  with a single data series no categorical colour pair needed checking.)
* Exports: the public JSON/HTML include the wireless summary. Addresses, serials, paths and
  tokens are redacted. Raw evidence stays private in `evidence/`.

## Device validation (REVVL 6 Pro, Android 13, build 0.4.0)

| Session | run_id | Samples (fresh/repeated) | RSSI median [min, max] dBm | Probes ack/total | Alerts |
|---|---|---|---|---|---|
| smoke_check | `bef3792c…` | 10 (4/6) | −52 [−52, −51] | 4/4 | none |
| stationary | `22516f79…` | 60 (31/29) | −52 [−54, −49] | 24/24 | internet_validation_lost 35.8–67.2 s (LAN probes still ok) |
| moved_farther | `29016b2c…` | 90 (51/39) | −49.5 [−75, −42] | 36/36 | signal_degradation 92–158 s; link_speed_drop 112–176 s |
| disconnect_reconnect | `cde75240…` | 90 (32/58), 25 unavailable | −48 [−54, −46] | 25/36 | Wi-Fi lost 39.2→90.2 s; 11 consecutive failures → `phone_not_on_wifi` |

Observed limits:
* About half of all polls repeated the previous value (counted as `repeated`).
* Callback data aged in 2 s steps while nothing changed.
* The default-network transport field didn't update on Wi-Fi loss in 0.4.0. That was fixed in 0.4.1
  and verified with `wifi_reconnect` run `4332f30e…`.

Session 2 movement details are the operator's account and are recorded separately from the
measurements. Charts: `docs/images/snapshot-*-charts.png`.

## Reproducible demonstration

> **Live vs portable.** `http://127.0.0.1:8770/…` links only work on the Mac while `dashboard.py` is running; they show private, unredacted data. The portable, shareable report is the saved snapshot listed in [`reports/public/INDEX.md`](../reports/public/INDEX.md) (open its `index.html` directly).

```sh
source ./env.sh; S=<adb serial>; P=com.androidnetguard.app
python3 -I collector/collector.py --host <Mac LAN IP> --host 127.0.0.1 --port 8765 &
python3 -I dashboard/dashboard.py --port 8770 --serial $S &
# start a session: phone UI (Tests → Wireless observation session) or, debug build only:
adb -s $S shell am start -n $P/.MainActivity --es ang.start_test wifi_observation \
    --ei ang.duration_s 120 --ei ang.sample_interval_ms 2000 --ei ang.probe_interval_ms 5000 --es ang.label stationary
# afterwards: http://127.0.0.1:8770/ → Import from connected phone → http://127.0.0.1:8770/wireless
```

## Limitations

* RSSI, link speed and frequency are coarse, platform-reported values that may be cached. They
  describe the phone's link to its access point and nothing about the environment.
* The results come from one phone (REVVL 6 Pro, Android 13) on one home network.
* The baseline thresholds are rule-of-thumb starting points, not tuned or validated.
* Failure attribution can't separate "collector down" from "LAN path broken" when both look the
  same from the phone.

## Interpreting value changes

First/changed and repeated counts compare consecutive reported value tuples. They do
not establish radio measurement timestamps, caching, or independence. Legacy field
names and historical exports are retained for reproducibility. Sample completeness
measures recorded slots, separately from changes in their values.
