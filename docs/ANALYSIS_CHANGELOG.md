# Analysis changelog

Final statuses are **derived** on the Mac from raw evidence by `dashboard/analysis.py` (`ANALYSIS_VERSION`)
and `dashboard/wireless.py` (`WIRELESS_ANALYSIS_VERSION`). Raw evidence (`evidence/server`,
`evidence/device`, `collector/data`) is never modified. Each derived report is written to a **new**
directory, and superseded reports are kept.

## Retained derived reports (private, `evidence/derived/`)

| Directory | Analysis | Notes |
|---|---|---|
| `20261103T215832Z_analysis-v2/` | v2 | First saved derived report (17 runs) |
| `20261104T011554Z_analysis-v4/` | v4 | Wireless v3 |
| `20261104T022858Z_analysis-v5/` | v5 | |
| `20261104T024947Z_analysis-v6/` | v6 | First to include the UI cancellation run `50edfe31…` |
| `20261104T030119Z_analysis-v7/` | v7 (wireless v3) | Superseded: 28 runs, no status change vs v6 |
| `20261104T031401Z_analysis-v7/` | v7 (wireless v4) | **Current**: completeness and freshness reported separately; no status change |

v1 and v3 were in use only between saved reports. Their status outputs are recorded in VALIDATION.md.

## Analysis versions

| Version | Change | Status changes |
|---|---|---|
| v1 | Initial correlation and classification rules | — |
| v2 | Separate cancellation-mechanism verdict; late-tap race is benign; monotonic timeline ordering; damaged lines preserved; stronger public redaction | none (checked against v1 statuses) |
| v3 | Path redaction anchored to the start of a token (relative paths are no longer redacted) | none |
| v4 | Reconnect recovery reason names attempt number and message seq separately | none |
| v5 | Explicit counters ("recovery attempt n of m" vs "overall message seq"), ordered by recorded start time, plus a numbering-consistency check | none; only `4332f30e…` reason text changed |
| **v6** | **A step left open by a recorded user cancellation is "interrupted" (inconclusive), not execution_error. A cancelled observation is "stopped early", not "completed".** | `50edfe31…` (first analysed under v5 in the live dashboard): `execution_error` → `inconclusive`. No other run changed |
| v7 | Status meanings are test-specific (connectivity/reliability vs observation). Observation sessions also need ≥ 50 % sample completeness, **a retrospective rule** (see below). Feature verification is tracked separately from the latest run outcome | none (checked against the v6 report after the fact) |

### The 50 % sampling-coverage threshold (retrospective)

* **When:** introduced in analysis v7 on 2026-11-04 at about 03:00 UTC, during the documentation
  consistency review. That was **after** every observation session in this project had been
  recorded (the last one, the cancellation check `50edfe31…`, started at 02:45 UTC). It was not
  defined before data collection.
* **Why:** to make "collection completed" an explicit, checkable condition. Before v7, an
  observation session counted as passed with a single valid RSSI value, so a session whose loop
  had silently produced only a few records could have looked complete. 50 % (at least half of the
  expected sampling slots, duration ÷ interval, produced a record) was chosen as a simple,
  conservative majority cut-off. **It's a judgement call, not a value derived from data.**
* **What it is not:** it isn't preregistered and isn't empirically validated. Its effect on
  existing evidence was checked only after the fact: all five completed sessions recorded 100 %
  of their slots, so no status changed. That also means the current evidence **can't show whether
  50 % is a good threshold**. It says nothing about research effectiveness.
* **Scope:** the rule concerns **sample completeness only**: records per expected slot. **Telemetry
  freshness** (fresh vs repeated values) is reported separately and doesn't count towards it.
  A session can be complete and still have mostly repeated values. Session 3 recorded
  90/90 slots, with 32 fresh and 58 repeated.

### Why v6 changed the cancellation run's status

In run `50edfe31…` (build 0.4.1), the user tapped Cancel during the observation step. The app
recorded `cancel_requested` (+155 028.8 ms) and `run_cancelled` (+155 033.3 ms), but app ≤ 0.4.1
**doesn't write `step_finished` for the step it interrupts**. Under v5, any step without
`step_finished` meant `execution_error` ("step 2 (observe) never finished"), so a correct,
user-requested stop was reported as a harness failure.

v6 classifies a step as **interrupted** only when all of the following hold:
1. a `cancel_requested` event exists;
2. `run_cancelled` exists;
3. the open step is the last step started;
4. it started before the tap.

Under those conditions the run is `inconclusive`. An open step **without** a recorded tap is still
`execution_error`. Both cases are unit-tested. The separate cancellation-mechanism verdict (passed:
stopped 4.5 ms after the tap, 0 in flight, 0 started after) didn't change. The app-side gap is a
documented limitation; the app wasn't modified, so the release source equals validated build 0.4.1.

## Wireless analysis versions

| Version | Change | Effect |
|---|---|---|
| v1 | Baseline detector, failure attribution, SVG charts | — |
| v2 | `NET_CAPABILITY_VALIDATED` loss reported as `internet_validation_lost` (not a LAN signal) | alert wording only |
| v3 | Failures inside Wi-Fi-lost intervals (telemetry `onLost`) attributed to `phone_not_on_wifi` | session 3: 11 probes re-labelled from `collector_unreachable_on_wifi` |
| v4 | Sample completeness (recorded / expected slots) reported separately from telemetry freshness (fresh / repeated) | display and export fields only |
