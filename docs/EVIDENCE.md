# Evidence model

## Raw vs derived

| Kind | Location | Written by | Mutated? |
|---|---|---|---|
| Raw collector records | `evidence/server/<UTC>_<session8>/messages.jsonl`, `access.log`, `session.json` | collector | append-only; each collector start gets a new directory |
| Raw app events | phone: `files/evidence/events.jsonl` (app-private) | app | append-only, synced to disk |
| Raw app imports | `evidence/device/<UTC>_<id>/events.jsonl` + `import.json` | dashboard import | new directory per import; byte-for-byte copy; identical snapshots are skipped by SHA-256 |
| Legacy smoke-test logs | `collector/data/*` | collector 0.1.0 | never touched; loaded read-only and not linked to runs |
| **Derived** | computed in memory by `dashboard/analysis.py`; exports downloaded on demand | dashboard | never written into raw directories |

Imports are snapshots of an append-only log. The analysis deduplicates events by `event_id`, and
an event ID that appears with different content is reported as an evidence issue.

## Identifiers

* `run_id`, `step_id`, `request_id`, `event_id`: random UUIDv4s generated on the phone.
* `process_id`: a random UUID for each app process. It distinguishes the processes before and
  after a restart.
* `session_id`: a random UUID for each collector start.
* Request IDs must be unique within a run **and** across runs. Any reuse makes every run involved
  an `execution_error`.

## Attempts vs received messages

Every request is recorded as an **attempt** on the phone (`attempt_started` and `attempt_finished`)
whether or not it arrived. The collector records only what it **received and stored**.
Correlation is by `request_id`:

| Correlation | Meaning |
|---|---|
| `matched` | app ack with the same ID, plus exactly 1 collector record with the same run and step |
| `missing_server_record` | app ack, but no collector record |
| `delivered_not_acked` | collector stored it, but the app saw no valid ack |
| `not_delivered` | no ack and no record |
| `duplicate_server_records` | more than 1 collector record for the ID |
| `run_or_step_mismatch` | the record names a different run or step |
| `no_app_result` | `attempt_started` without `attempt_finished` (the process died) |
| `health_app_only` | health checks carry no ID to the collector; app-side evidence only |

A missing collector record **alone** is never treated as proof of blocking. If the run has no
collector records at all, the result is `inconclusive`, since the collector logs may simply not
be loaded.

## Status rules (final, derived on the Mac)

Precedence when combining: `execution_error` > `inconclusive` > `failed` > `passed`.

* `pending`: `run_started` but no step yet. `running`: steps exist, but there's no `run_finished`
  in the imported evidence.
* `execution_error`: the app reported one (invalid config, cleartext not permitted, evidence
  write failure, process ended mid-run); a step that never finished; an attempt with no result;
  duplicate IDs; or an unknown test.
* `inconclusive`: cancelled; a precondition not met (transport not exactly `wifi`, record via
  loopback for Wi-Fi or not via loopback for USB); a user action not taken in time; required
  steps not reached; fewer sequence attempts than requested; or no collector records for the run.
* `failed`: the preconditions held and the evidence contradicts the expected behaviour.
* `passed`: every required attempt is `matched` under the preconditions, plus the test-specific
  evidence (two processes for a restart; loss, return and recovery for a reconnect).

The app shows **provisional** statuses computed from app evidence only. The dashboard status is
the final one.

## Clocks

* Phone durations use `SystemClock.elapsedRealtimeNanos()`. It's monotonic, shared by all
  processes, and resets at reboot; a reboot is detected in `app_restart`.
* Collector records carry `server_mono_ns` (`time.monotonic_ns()`).
* Timelines show each source relative to its **own** origin. Phone and Mac timestamps are never
  subtracted from each other. Wall-clock times are shown only as that device's own reading.

## Public vs private exports

* `GET /runs/<id>/export.json?scope=public|private` returns `derived`, `raw`, `timeline`,
  `status_definitions` and `limitations`.
* `GET /runs/<id>/report.html?scope=public|private` is a readable standalone report.
* The **public** scope is the default. It replaces `remote_addr`, `remote_port`, `server_addr`,
  `server_port`, `endpoint`, `url`, `serial`, `model` and similar keys with `[redacted]`, and
  scrubs IPv4, IPv6 and MAC patterns from free text. Random UUIDs are kept.
* The **private** scope and the raw directories keep the original values. They are git-ignored;
  don't share them.


## Status rules are test-specific (analysis v7)

The same six status names are used throughout, but **what earns each status depends on the kind of run**.

| Kind | `passed` means | Notes |
|---|---|---|
| **Connectivity / reliability** (`usb_connectivity`, `wifi_connectivity`, `wifi_sequence`, `app_restart`, `wifi_reconnect`) | every required step completed and each required message is an ack with the same ID **and** exactly one correlated collector record, under the test's preconditions | `failed` = preconditions held and the evidence contradicts the expected behaviour |
| **Observation** (`wifi_observation`) | collection completed for the configured duration and met the data-quality requirements: all telemetry labelled `ANDROID_WIFI_TELEMETRY` with per-field statuses, ≥ 1 valid RSSI value, ≥ 50 % sample completeness (a retrospective v7 rule, neither preregistered nor validated; see ANALYSIS_CHANGELOG). Telemetry freshness is reported separately and isn't part of this rule | failed probes, signal drops and outages are **valid observations** and don't lower the status. `failed` isn't used. Stopped early, no valid RSSI or low coverage → `inconclusive` |
| **Cancellation mechanism** (assessed inside any run with a recorded tap) | separate verdict `passed` / `failed` / `not_exercised` | independent of the interrupted run's own status, which is normally `inconclusive` |

The dashboard overview shows **feature verified** (some run reached its success status; the
verifying run stays linked) separately from **latest run outcome**, so a later incomplete run
doesn't erase an earlier verified check. Version history: [ANALYSIS_CHANGELOG.md](ANALYSIS_CHANGELOG.md).

## Interpreting value changes

First/changed and repeated counts compare consecutive reported value tuples. They do
not establish radio measurement timestamps, caching, or independence. Legacy field
names and historical exports are retained for reproducibility. Sample completeness
measures recorded slots, separately from changes in their values.
