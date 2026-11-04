# Validation record

This file lists what was actually run on 2026-11-03 (UTC), with results. Device: REVVL `TMAF035G`,
Android 13 (API 33). Mac: `en0` 192.0.2.6. App 0.2.0 (debug). Collector 0.2.0, session
`20261103T185250Z_b7b04955`. All statuses below are the dashboard's **final** derived statuses
unless marked otherwise.

## Automated tests

| Suite | Command | Result |
|---|---|---|
| Collector (validation, size limits, sessions, binds) | `python3 -I -m unittest discover -s collector` | 17/17 OK |
| Dashboard and analysis (correlation, missing evidence, duplicates, cancellation, classification, redaction, Host/CSRF guards, fake-adb import) | `python3 -I -m unittest discover -s dashboard` | 41/41 OK; the suite was run 8 times in a row to rule out order effects |
| Android core (JVM: runner, cancellation, restart/reboot, bounds, endpoint validation) | `./gradlew testDebugUnitTest` | 26/26 OK |
| Builds | `./gradlew assembleDebug assembleRelease --warning-mode all` | success, no warnings |
| Release cleartext check | `aapt2 dump xmltree`, `unzip -l` on the release APK | no network-security config, no `usesCleartextTraffic` |

Defects the tests found and that were fixed:
* the collector accepted an uppercase UUID (USB milestone);
* the analysis flagged only one of two runs that reused a request ID, and which one depended on
  directory order;
* a JVM test really waited the 5-minute restart window, which made every Gradle build take about
  5 minutes. The wait now goes through the injectable `Clock`.

## Real-device runs

| Test | run_id | Trigger | Final | Evidence |
|---|---|---|---|---|
| `wifi_connectivity` | `cf710e48-9342-47c4-aa44-f67d16d811e5` | adb intent | **passed** | 1/1 matched; peer `192.0.2.196` → `192.0.2.6`; no adb mapping. See [WIFI_SMOKE_TEST.md](WIFI_SMOKE_TEST.md) |
| `wifi_sequence` (10 × 500 ms) | `880188e0-91db-4a92-83e3-abfdd66b1faa` | adb intent | **passed** | 10/10 matched, all from `192.0.2.196` |
| `app_restart` | `e8822050-8394-4e56-91e6-46caed073652` | adb intent; restart with `am force-stop` + `am start` | **passed** | 2/2 matched; 2 process IDs; `run_resumed` |
| `usb_connectivity` | `5a42a6a2-f5cb-4464-b9c2-8b493ad47f19` | adb intent; reverse added before the run and removed after | **passed** | 1/1 matched; peer `127.0.0.1` |
| `wifi_sequence` (50 × 5000 ms) | `a68ba1ae-2114-4f20-b2b8-937c169ca361` | adb intent | **passed** | 50/50 matched. It was meant to exercise rotation and cancellation, but **no rotation or cancel happened** (no `activity_created` or `run_cancelled` events during the run) |
| `wifi_reconnect` | `f00ad06f-d23b-43c0-acd9-9463c2fe495e` | adb intent | **inconclusive** | Baseline matched; "Wi-Fi loss not observed within 2 minutes". No `transport_changed` event; Wi-Fi was not toggled |

Other checks:
* The dashboard is reachable at `127.0.0.1:8770` and not at `192.0.2.6:8770`. The collector
  returns 404 for dashboard paths.
* The **public** JSON and HTML exports of a real run contain no `192.168.*` addresses, serial or
  model. The private exports keep the addresses. Serial and model appear only in the private
  `import.json`.
* Every app update used `adb install -r`. `firstInstallTime` stayed `2026-11-02 18:09:52`,
  nothing was uninstalled, and no data was cleared.

## Not yet verified on the device

* **Screen rotation during a run**: state preservation is designed in (the `Engine` lives in the
  `Application`, the tab is saved in instance state, the EditTexts have IDs). An `activity_created`
  event records each recreation. This has not been observed on the device yet.
* **Cancel from the UI on the device**: covered by JVM tests, not yet tapped on the phone.
* **Wi-Fi disconnect/reconnect**: needs a person to toggle Wi-Fi. The single attempt ended
  inconclusive because no toggle happened.
* **The fixed build** (restart wait through `Clock`) was installed after the device runs above.
  Its device behaviour is intended to be identical but hasn't been re-run.
* **Health checks** are app-side only. The collector doesn't receive their request IDs.

## Preserved evidence

The USB and legacy files are unchanged. SHA-256 at the end of this work:

```
1f35d2d380af18576af0a3bb7dc2b7a0938bf3377d67958f50783add4bd71dac  USB_SMOKE_TEST.md
038a1b25dc7f33ae06d84fb380fe9d782be9fbd6c69474d500b2e607fb090933  collector/data/messages.jsonl
d74140726320e4992b8dfdfb64e05a628f4b8f9cdaae1116f8fcb411c2a6dd42  collector/data/collector-access.log
e3eca7cc94160a765c8e24b1e67ddaa95f6c5b21b8d5c7798e63bf7d7a197191  collector/data/wifi-collector-access.log
```

The first three hashes match the ones recorded before this work started. `wifi-collector-access.log`
was not hashed beforehand, but its content (2 lines) is the same as when it was first read.

## Reproduce the pending manual checks

```sh
source ./env.sh; S=DEVICE_SERIAL; P=com.androidnetguard.app
# rotation + cancel: start a long sequence, rotate the phone, then tap Cancel on the Tests screen
adb -s $S shell am start -n $P/.MainActivity --es ang.start_test wifi_sequence --ei ang.count 50 --ei ang.interval_ms 5000
# reconnect: start it, then toggle Wi-Fi off/on in Quick Settings when the yellow prompt asks
adb -s $S shell am start -n $P/.MainActivity --es ang.start_test wifi_reconnect
# then: http://127.0.0.1:8770/ → Import from connected phone
```

---

## Update 2026-11-04: final device validation and wireless observability

The complete, current table of runs, builds and verdicts is in
[docs/VALIDATION_SUMMARY.md](docs/VALIDATION_SUMMARY.md), produced with analysis v4 and wireless
analysis v3. This section only records what changed since the entries above.

* **Rotation during an active run: verified** (`fa3014fa-d33a-4a35-bbb3-57c28471a04d`). The
  earlier "not yet verified" note above is superseded.
* **Wi-Fi disconnect/reconnect: verified on 0.4.1** (`4332f30e-ac6f-40c5-8de3-037bf899f7bc`):
  loss observed +6.5 s, return +17.9 s, recovered on attempt 1.
* **Correction:** the note above that run `f00ad06f…` was inconclusive "because no toggle happened"
  is withdrawn. Builds 0.2.0–0.4.0 never observed a loss of Wi-Fi (the `onLost` defect), so that
  run shows only that the app did not observe a loss.
* **Cancellation from the UI: still unverified.** Five attempts recorded no `cancel_requested`
  inside the run window, because the taps came after the runs had ended or while the app wasn't
  in the foreground.
* **Wireless observation sessions** (ANDROID_WIFI_TELEMETRY, not CSI): stationary, moved farther,
  disconnect/reconnect, plus a smoke check and one unplanned UI-started session. All passed as
  observations. See the summary for RSSI, probe and alert figures.
* **New public artefacts:** `reports/public/20261104T011525Z_snapshot/` (sanitized, checksummed)
  and screenshots in `docs/images/`.

---

## Build 0.4.1: Wi-Fi reconnect result (run `4332f30e-ac6f-40c5-8de3-037bf899f7bc`)

Started from adb at 2026-11-04T01:13:27Z (phone wall clock). Build 0.4.1, debug APK SHA-256
`b4e70d2fb558fe05c8b0c1f6f9c7b83ca94608e2d3ad28995ab08eda5998fde3`, installed in place. Collector
session `20261103T220611Z_b85549d3`. No adb reverse or forward mapping was active.

| Phone-monotonic ms | Event | Evidence |
|---|---|---|
| +7 → +41 | baseline message, overall seq 1 | ack with same ID; 1 collector record (`matched`) |
| +47 | prompt "Turn Wi-Fi OFF now" | `prompt_shown` |
| **+6548** | **Wi-Fi loss observed** | `transport_changed → none` (the first transport change ever recorded; earlier builds had the `onLost` defect) |
| +6558 → +6589 | outage message, overall seq 2 (informational) | `connect_error` after 21 ms, transport `none→none`, no collector record (`not_delivered`) |
| +6609 | prompt "Turn Wi-Fi back ON now" | `prompt_shown` |
| **+17901** | **Wi-Fi return observed** | `transport_changed → wifi` |
| +17911 → +22919 | settle | 5 s |
| +22948 → +23074 | **recovery attempt 1**, overall seq 3 | ack with same ID; 1 collector record (`matched`) |
| +23092 | `run_finished` | app-side `passed`: "recovered on attempt 1" |

**Final derived status: passed** (analysis v5). Derivation: "recovery attempt 1 of 1 recorded (max
3) matched; overall message seq 3 in this run". Check: "recovery numbering consistent:
recovery attempt 1 = overall seq 3".

**The "attempt #3" vs "attempt 1" discrepancy.** These were two different counters, both
correct. `seq` is the overall message sequence across the run (baseline 1, outage 2, recovery
3–5); the app numbers recovery attempts within the recovery step (seq = 2 + n). Analysis v3 showed
the overall seq as "attempt #3", and only the live dashboard displayed that wording. v4 and v5
derive both numbers from the recorded attempts, label them explicitly, and check that they agree.
No raw evidence was modified, and no status changed between v3, v4 and v5.

## Final checklist (as of 2026-11-04)

| Feature | Status | Evidence / note |
|---|---|---|
| USB delivery via adb reverse | ✅ verified | `5a42a6a2…`; `USB_SMOKE_TEST.md` |
| Direct Wi-Fi delivery (no adb mapping) | ✅ verified | `cf710e48…`; `WIFI_SMOKE_TEST.md` |
| Bounded message sequences (≤ 50) | ✅ verified | e.g. `fa3014fa…` 50/50 matched, no duplicates |
| App restart, adb-driven | ✅ verified | `e8822050…` (2 processes) |
| App restart, user-performed | ✅ verified | `60de5d0a…` (2 processes) |
| Rotation during an active run | ✅ verified | `fa3014fa…` (landscape +79.1 s, portrait +108.5 s); also during observation sessions |
| Wi-Fi loss/return observed + recovery | ✅ verified on 0.4.1 | `4332f30e…` |
| Wi-Fi loss observed on 0.2.0–0.4.0 | ❌ defect (fixed in 0.4.1) | 0 `transport_changed` events before 0.4.1 |
| **Cancellation from the UI on the device** | ⚠️ **unverified** | 5 attempts, no `cancel_requested` within a run window (taps after run end / app not in foreground). JVM and analysis tests pass |
| Wireless telemetry (RSSI, frequency, link, capabilities) | ✅ verified | sessions `bef3792c…`, `22516f79…`, `29016b2c…`, `cde75240…` |
| Baseline alerts (degradation, link drop, disruption) | ✅ produced on real data | session 2 and session 3 alerts; thresholds are rule-of-thumb and untuned |
| Failure attribution using telemetry | ✅ verified | session 3: 11 × `phone_not_on_wifi` |
| Collector health checks after failed probes | ✅ verified | session 3: 11 follow-up checks, all `connect_error` during the outage |
| Saved sanitized report (offline, checksummed) | ✅ verified | `reports/public/20261104T022858Z_snapshot/` |
| Release build network restrictions | ✅ verified | no NSC, no cleartext flag, not debuggable |
| External CSI hardware | ⛔ not connected | adapter parses synthetic fixtures only; no compatibility claimed |
| Security-policy tests | ⛔ not implemented | — |
| Model-guided test selection | ⛔ not implemented | — |
| Operator account of session 2 movement | ⏳ pending from the user | measurements only so far |

---

## Cancellation from the UI: verified on build 0.4.1 (run `50edfe31-5158-4e03-b2f4-de8fefd73e32`)

The user started a Wireless observation session from the phone UI (`trigger=ui`, duration 600 s,
sampling 2000 ms, probes 5000 ms, label `cancel_check`) at 2026-11-04T02:45:16Z (phone wall clock),
waited, and tapped **Cancel**. Evidence is in device import `20261104T024811Z_fe14194c` and collector
session `20261103T220611Z_b85549d3`. Times below are phone-monotonic, measured from `run_started`.

| ms | Event | Detail |
|---|---|---|
| +150 104 → +150 213 | last probe before the tap (overall seq 31) | ack, collector record matched; finished **4 816 ms before** the tap |
| +154 092 | last telemetry sample (78th) | 937 ms before the tap |
| **+155 028.8** | **`cancel_requested`** | source `ui_cancel_button`, `run_in_progress=true`, `token_present=true`, `already_cancelled=false`; progress at tap "78 samples (78 with RSSI), probes 31/31 acknowledged" |
| **+155 033.3** | **`run_cancelled`** (`cancellation_completed`) | **stop latency 4.5 ms** |
| +155 040.3 | `run_finished` | app-side `inconclusive`: "cancelled by user" |

* **Requests in flight at the tap: 0.** **Requests or telemetry samples started after the tap: 0.**
  The only events after the tap are `run_cancelled` and `run_finished`. The collector holds 31
  records (max seq 31), one per acknowledged probe.
* **Cancellation mechanism verdict: passed** (analysis v6): "stopped 4.5 ms after the tap; 0
  requests in flight at the tap; no new request started".
* **Interrupted session's own verdict: inconclusive.** The observation was stopped early by the
  user after 78 samples. This is recorded separately from the mechanism verdict.

**Evidence gap found by this check (known limitation, app ≤ 0.4.1).** When Cancel interrupts a
step, the app writes no `step_finished` for that step. Analysis v5 therefore classified this run
as `execution_error` ("step 2 (observe) never finished"). Analysis v6 treats a step that was active
when a *recorded* Cancel tap occurred as **interrupted** (inconclusive) and still treats an open
step without a recorded tap as `execution_error`. Both cases are unit-tested. No raw evidence was
modified, and no other run's status changed between v5 and v6 (`evidence/derived/20261104T024947Z_analysis-v6/`).
Fixing the app to close the open step is future work. It wasn't changed here, so the release
source stays identical to the device-validated build 0.4.1.

**Checklist update:** "Cancellation from the UI on the device" moves from ⚠️ unverified to
✅ **verified on 0.4.1** (mechanism), with the open-step evidence gap noted above.
Previous unsuccessful attempts (`a68ba1ae…`, `f8258b4b…`, `09ebef02…`, `4b0932c0…`, `dece843d…`)
remain recorded as cases where no tap was captured inside the run window.

---

## Documentation consistency review (analysis v7, 2026-11-04)

* **Status meanings are now test-specific.** Connectivity/reliability checks need their required
  steps plus correlated evidence. Observation sessions need collection completion plus the
  documented data-quality rules: failed probes are valid observations, and sampling coverage
  must be ≥ 50 %. The cancellation mechanism has its own passed/failed/not_exercised verdict,
  separate from the interrupted session's `inconclusive`.
* **The overview shows "feature verified" separately from "latest run outcome".** Examples:
  `usb_connectivity` is verified by `5a42a6a2…` while the latest run `37b7922a…` is inconclusive;
  `wifi_observation` is verified by `cde75240…` while the latest run `50edfe31…` is inconclusive.
* **The stale "charts not implemented" catalog entry is replaced.** The per-run wireless plots
  are implemented; model or security scores remain unimplemented.
* **No status changed between v6 and v7** (28 runs). Derived report:
  `evidence/derived/20261104T030119Z_analysis-v7/`. Superseded derived reports v2, v4, v5 and v6 are
  kept. The v6 cancellation reclassification is explained in `docs/ANALYSIS_CHANGELOG.md`.
* `127.0.0.1:8770` links in the Markdown docs are live, machine-local instructions. They're not
  portable reports. The shareable report is the snapshot listed in `reports/public/INDEX.md`.
  The snapshot check now rejects any localhost or loopback reference.

---

## Clarification: the 50 % sample-completeness rule (2026-11-04)

The ≥ 50 % sampling-coverage requirement for observation sessions was **introduced retrospectively**
in analysis v7, at about 03:00 UTC on 2026-11-04, after every observation session had been
recorded. It makes "collection completed" explicit; before v7 a single valid RSSI value was
enough. 50 % was chosen as a simple majority cut-off. It is **not preregistered, not empirically
validated, and not evidence of research effectiveness**. It was checked against existing evidence
only after the fact: all five completed sessions recorded 100 % of their slots, so no status
changed, and that evidence can't show whether 50 % is a good value. Sample completeness
(recorded / expected slots) is now reported **separately** from telemetry freshness (fresh /
repeated). The dashboard (wireless analysis v4) has distinct tiles and columns for the two.
Details: `docs/ANALYSIS_CHANGELOG.md`, `docs/WIRELESS_OBSERVABILITY.md`.
