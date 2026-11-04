# Validation summary

Every figure comes from saved evidence. Final statuses come from the dashboard's analysis **v7**
(wireless analysis **v4**), derived report `evidence/derived/20261104T031401Z_analysis-v7/`
(private; 20 input files with SHA-256 hashes listed in `summary.json`, no evidence issues). The
public, sanitized copy is `reports/public/20261104T022858Z_snapshot/` (the earlier `…011525Z` snapshot is superseded; see `reports/public/INDEX.md`).

Device: REVVL 6 Pro (TMAF035G), Android 13 / API 33, one home Wi-Fi network (5 GHz, 5220 MHz).
Collector and dashboard ran on a MacBook Pro.

## Builds

| App version | versionCode | Debug APK SHA-256 | Runs |
|---|---|---|---|
| 0.1.0 | 1 | (not recorded) | USB smoke test (`USB_SMOKE_TEST.md`) |
| 0.2.0 | 2 | three builds shared this versionName. The installed APK was hash-checked as `31240325…74862f8` at 19:12 UTC, so runs from 19:13 onward used it; earlier 0.2.0 runs used preceding builds whose hashes weren't recorded. A later 0.2.0 build (`00a7a554…`, adding `cancel_requested`) ran no tests | 16 |
| 0.3.0 | 3 | `30318163…bc1a73` | 4 |
| 0.4.0 | 4 | `54851f6d…89c822f` | 6 |
| 0.4.1 | 5 | `b4e70d2f…998fde3` | 2 |

Each update used `adb install -r`. The app was never uninstalled (`firstInstallTime` stayed
2026-11-02 18:09:52), and before each update the phone's evidence log was checked byte for byte
against the version that followed it.

## Collector sessions

`20261103T185250Z_b7b04955`, `20261103T191238Z_73c1ed85`, `20261103T204806Z_1c19d4c9`,
`20261103T220611Z_b85549d3` (collector 0.2.0 / 0.2.0 / 0.2.1 / 0.2.1 with the backlog fix). Legacy
smoke-test logs are in `collector/data/` and haven't been modified.

## Connectivity and reliability runs

"Msgs" counts message attempts. "Ack" counts acknowledgements carrying the same request ID.
"Matched" counts acks backed by exactly one collector record with the same run and step.

| Started (phone UTC) | run_id | Test | Build | Trigger | Final | Msgs / ack / matched | Notes |
|---|---|---|---|---|---|---|---|
| 11-03 18:53 | `cf710e48-9342-47c4-aa44-f67d16d811e5` | wifi_connectivity | 0.2.0 | adb | **passed** | 1/1/1 | Direct Wi-Fi (`WIFI_SMOKE_TEST.md`) |
| 11-03 18:54 | `880188e0-91db-4a92-83e3-abfdd66b1faa` | wifi_sequence ×10 | 0.2.0 | adb | **passed** | 10/10/10 | |
| 11-03 18:54 | `e8822050-8394-4e56-91e6-46caed073652` | app_restart | 0.2.0 | adb | **passed** | 2/2/2 | Restart by `am force-stop`; 2 processes |
| 11-03 18:54 | `5a42a6a2-f5cb-4464-b9c2-8b493ad47f19` | usb_connectivity | 0.2.0 | adb | **passed** | 1/1/1 | Loopback peer via adb reverse |
| 11-03 19:01 | `a68ba1ae-2114-4f20-b2b8-937c169ca361` | wifi_sequence ×50 | 0.2.0 | adb | **passed** | 50/50/50 | Intended rotation/cancel didn't happen |
| 11-03 19:05 | `f00ad06f-d23b-43c0-acd9-9463c2fe495e` | wifi_reconnect | 0.2.0 | adb | **inconclusive** | 1/1/1 | "Wi-Fi loss not observed". **May be caused by the transport defect fixed in 0.4.1**, so it doesn't prove Wi-Fi wasn't toggled |
| 11-03 19:13 | `4b15bc09-3c7e-4e9d-b037-54877dcfcbe3` | wifi_sequence ×10 | 0.2.0 | adb | **passed** | 10/10/10 | Current-build baseline |
| 11-03 19:13 | `56937e1f-f494-4e9d-9ca2-3d32a7eac7fe` | wifi_sequence ×50 | 0.2.0 | adb | **passed** | 50/50/50 | Rotation attempt: display stayed locked, rotation unverified |
| 11-03 19:18 | `13e0ef41-a246-4050-bbd2-9676e714beff` | wifi_sequence ×50 | 0.2.0 | adb | **passed** | 50/50/50 | Rotation happened after the run ended |
| 11-03 19:32 | `28a3abf7-5e26-4db0-a80d-a04057944c50` | wifi_sequence ×10 | 0.2.0 | ui | **passed** | 10/10/10 | Started from the phone, in landscape |
| 11-03 19:35 | `60de5d0a-fc38-4413-9fe7-83f14921210f` | app_restart | 0.2.0 | ui | **passed** | 2/2/2 | User-performed restart, 2 processes |
| 11-03 19:36 | `b3f0083e-c5ab-46e2-9f31-5bf5e38dcb04` | usb_connectivity | 0.2.0 | ui | **inconclusive** | 0/0/0 | No adb reverse active; loopback refused |
| 11-03 19:36 | `89005626-772e-4b54-9a36-53571b15a85c` | wifi_sequence ×10 | 0.2.0 | ui | **passed** | 10/10/10 | |
| 11-03 19:36 | `fa3014fa-d33a-4a35-bbb3-57c28471a04d` | wifi_sequence ×50 | 0.2.0 | adb | **passed** | 50/50/50 | **Rotation verified**: landscape +79.1 s, portrait +108.5 s, same process, contiguous seq, gaps 5040–5236 ms |
| 11-03 19:41 | `f8258b4b-ceb6-439d-94b8-7766b6d537a1` | wifi_sequence ×50 | 0.2.0 | adb | **passed** | 50/50/50 | Cancel tap missed |
| 11-03 20:12 | `09ebef02-804e-4054-a99d-b84edea13b29` | wifi_sequence ×50 | 0.2.0 | adb | **passed** | 50/50/50 | Cancel tap missed |
| 11-03 20:48 | `3b408934-58b5-472d-9a0c-79e510297796` | wifi_sequence ×5 | 0.3.0 | adb | **passed** | 5/5/5 | Final-build baseline |
| 11-03 22:10 | `4b0932c0-059c-47ce-9256-a6f58b7ef0e0` | wifi_sequence ×50 | 0.3.0 | adb | **passed** | 50/50/50 | No `cancel_requested`; the app wasn't in the foreground |
| 11-03 22:52 | `37b7922a-d7cc-411b-ab71-c383e31a35e6` | usb_connectivity | 0.3.0 | ui | **inconclusive** | 0/0/0 | No adb reverse active |
| 11-03 22:52 | `bc3dfc2f-0bd2-4d85-a656-7d9672d942c0` | app_restart | 0.3.0 | ui | **inconclusive** | 1/1/1 | Reopened after the 5-minute window |
| 11-03 23:22 | `dece843d-295c-41e0-b23d-629839273436` | app_restart | 0.4.0 | ui | **inconclusive** | 1/1/1 | Not restarted within 5 minutes; Cancel came after the run ended |
| 11-04 02:45 | `50edfe31-5158-4e03-b2f4-de8fefd73e32` | wifi_observation (600 s, `cancel_check`) | 0.4.1 | ui | **inconclusive** (stopped early) | 31/31/31 | **Cancellation mechanism passed**: tap +155 028.8 ms, stopped +4.5 ms, 0 in flight, 0 after |
| 11-04 01:13 | `4332f30e-ac6f-40c5-8de3-037bf899f7bc` | wifi_reconnect | 0.4.1 | adb | **passed** | 3/2/2 | **Loss observed +6.5 s, return +17.9 s; recovery attempt 1 (overall seq 3) matched.** Outage attempt `connect_error` (informational) |

## Wireless observation sessions (ANDROID_WIFI_TELEMETRY, build 0.4.0)

| Session | run_id | Sample completeness (recorded / expected) | Telemetry freshness (fresh / repeated) | RSSI median [min, max] | Probes ack / collector | Health ok | Alerts (wireless v3) |
|---|---|---|---|---|---|---|---|
| smoke_check (20 s) | `bef3792c-5868-4c9c-9881-35da2c43254f` | 10 / 10 (100 %) | 4 / 6 | −52 [−52, −51] | 4/4 · 4 | 2/2 | none |
| 1 stationary (120 s) | `22516f79-dcd1-4994-9918-f2e3a4c9a717` | 60 / 60 (100 %) | 31 / 29 | −52 [−54, −49] | 24/24 · 24 | 2/2 | internet_validation_lost at 35.8 s (back at 67.2 s), while LAN probes were still acknowledged |
| 2 moved_farther (180 s) | `29016b2c-dad9-4db4-a796-1ae4e2768263` | 90 / 90 (100 %) | 51 / 39 | −49.5 [−75, −42] | 36/36 · 36 | 2/2 | signal_degradation 92.2–158.2 s; link_speed_drop 112.2–176.2 s (link down to 58 Mbps) |
| unplanned, started from UI (180 s) | `14e3657e-dbfb-4286-8e37-49fdcb7b8e3b` | 90 / 90 (100 %) | 54 / 36 | −49.5 [−71, −40] | 36/36 · 36 | 2/2 | signal_degradation 34.1–118.1 s |
| 3 disconnect_reconnect (180 s) | `cde75240-ae69-4223-9aa9-f26b4bef4da3` | 90 / 90 (100 %); RSSI unavailable in 25 records | 32 / 58 | −48 [−54, −46] | 25/36 · 25 | 2/13 | Wi-Fi lost 39.2 s (onLost) → back 90.2 s; 11 consecutive probe failures, all attributed `phone_not_on_wifi` |

Sample completeness and telemetry freshness are separate measures. The ≥ 50 % completeness rule was
added retrospectively in analysis v7, after these sessions had been recorded, and isn't preregistered or
validated (see `docs/ANALYSIS_CHANGELOG.md`). The **sampling cadence held at 1.99–2.01 s** in every session, including while USB was unplugged
(session 2) and during the outage (session 3). The Activity was recreated during these sessions
(1, 3, 13 and 1 times; orientation changes, state restored, one process each), and sampling stayed
contiguous. The 13 recreations happened in the unplanned run while the phone was being moved.
Where the phone was taken in sessions 2 and the unplanned run is **the operator's account, not yet
provided**. The measurements don't establish why the signal changed.

No SSID, BSSID or MAC values appear in the evidence. The platform reported
`getApplicableRedactions() = 7`.

## Feature verified vs latest run outcome

A later incomplete run doesn't erase an earlier verified check (the dashboard overview shows both).

| Feature | Verified? | Verifying run | Latest run | Latest outcome |
|---|---|---|---|---|
| usb_connectivity | ✅ | `5a42a6a2…` | `37b7922a…` | inconclusive (no adb reverse active) |
| wifi_connectivity | ✅ | `cf710e48…` | `cf710e48…` | passed |
| wifi_sequence | ✅ | `4b0932c0…` (latest of 12 passing) | `4b0932c0…` | passed |
| app_restart | ✅ | `60de5d0a…` (latest of 2 passing) | `dece843d…` | inconclusive (not restarted within 5 min) |
| wifi_reconnect | ✅ (0.4.1) | `4332f30e…` | `4332f30e…` | passed |
| wifi_observation | ✅ | `cde75240…` (latest of 5 passing) | `50edfe31…` | inconclusive (stopped early by the cancellation check, as intended) |
| UI cancellation (mechanism) | ✅ (0.4.1) | `50edfe31…` | `50edfe31…` | mechanism passed |
| Rotation during a run | ✅ | `fa3014fa…` (first of 5 passing runs with an in-run recreation) | `50edfe31…` | inconclusive (stopped early by the cancellation check) |

Status meanings are test-specific (connectivity/reliability vs observation vs cancellation
mechanism); see `docs/EVIDENCE.md`. Analysis history: `docs/ANALYSIS_CHANGELOG.md`.

## Checks that are verified, unverified or failed

| Check | Status | Evidence |
|---|---|---|
| USB delivery (adb reverse) | verified | `5a42a6a2…`, `USB_SMOKE_TEST.md` |
| Direct Wi-Fi delivery (no adb mapping) | verified | `cf710e48…`, peer = phone Wi-Fi address |
| Bounded sequences up to 50 messages | verified | many runs, 0 duplicates, contiguous seq |
| App restart, adb and user-performed | verified | `e8822050…`, `60de5d0a…` |
| Rotation during an active run | verified | `fa3014fa…` (and recreations during observation sessions) |
| Cancellation from the UI on the device | **verified on 0.4.1** | `50edfe31…`: tap recorded, stopped 4.5 ms later, 0 in flight, 0 started after. Known gap: the interrupted step has no `step_finished` (analysis v6 handles it). Five earlier attempts captured no tap |
| Wi-Fi loss/return observed by the app | verified **on 0.4.1** | `4332f30e…`. 0.2.0–0.4.0 had a defect (below) |
| Wireless telemetry (RSSI, frequency, link, capabilities) | verified | four sessions above |
| External CSI hardware | not connected | dashboard shows "External CSI hardware not connected" |

## Defects found on the real device

1. **Transport not updated when Wi-Fi was lost (0.2.0–0.4.0).** `AndroidNetView.onLost()` called
   `getActiveNetwork()` inside the callback, which Android's reference warns against. The evidence
   contains **0** `transport_changed` events before 0.4.1. Fixed in 0.4.1 and verified by
   `4332f30e…`. Affected derived labels in session 3 were re-attributed by wireless analysis v3
   using the independent Wi-Fi telemetry callback. Run `f00ad06f…` can't be re-interpreted (it
   has no telemetry), so it stays inconclusive.
2. **Collector listen backlog of 16** reset 3 of 200 burst connections in a stress test. Fixed
   (backlog 128, concurrency still capped at 32).

## Automated tests (latest run)

| Suite | Result |
|---|---|
| Android JVM (`./gradlew testDebugUnitTest`) | 43/43 |
| Collector (`python3 -I -m unittest discover -s collector`) | 21/21 |
| Dashboard, analysis, wireless, CSI, snapshot (`python3 -I -m unittest discover -s dashboard`) | 89/89 |
| Builds (`assembleDebug assembleRelease --warning-mode all`) | success, no warnings. Release APK has no network-security config, no `usesCleartextTraffic`, isn't debuggable |

## Interpreting value changes

First/changed and repeated counts compare consecutive reported value tuples. They do
not establish radio measurement timestamps, caching, or independence. Legacy field
names and historical exports are retained for reproducibility. Sample completeness
measures recorded slots, separately from changes in their values.
