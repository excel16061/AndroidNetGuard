# Research overview

## Motivation and research question

Mobile network tests often report a single verdict ("works", "blocked", "secure") without
recording what was actually observed, by whom, and under which conditions. Before asking
whether some test-selection strategy finds problems faster, we need a harness whose results
can be checked independently.

**Working research question (not yet answered):**
*For on-device network-behaviour testing, can model-guided test selection reach the same
evidence-backed conclusions with fewer test executions than fixed or random selection, when
every verdict has to be supported by independently correlated client and server evidence?*

The project so far builds and validates the **evidence and execution layer** that such a
comparison needs. It contains no model, no security-policy test and no comparison results.

## Implemented engineering contribution

1. **An evidence-producing Android test runner.** A pure-Java core (`app/.../core/`, unit-tested
   on the JVM) runs five bounded connectivity and reliability tests. It emits append-only events
   with random IDs for each run, step, request, event and process, and timestamps them with the
   phone's monotonic clock.
2. **A collector that is separate from the analysis.** The LAN-facing collector stores only
   what it receives (validated, size-bounded, per-session directories). It has no control
   endpoints.
3. **Final statuses derived from two sources.** A localhost-only dashboard imports the app's
   evidence over adb and correlates each request ID with the collector's records. It computes
   one of six statuses with documented rules and never accepts the phone's own verdict alone.
4. **Explicit treatment of uncertainty.** `inconclusive` and `execution_error` are first-class
   results. Configured mode is recorded separately from the transport the phone reports.
   Cancellation, restarts, rotations and user-assisted actions are recorded as events, with a
   separate check on the cancellation mechanism itself.
5. **Wireless observability (ANDROID_WIFI_TELEMETRY).** An opt-in session records the Wi-Fi
   telemetry Android exposes, with an explicit status for every value and repeated (possibly
   cached) readings marked. A transparent rule-based baseline produces observations and anomaly
   alerts. There's also an optional, pinned adapter for external CSI hardware, with none connected.
6. **Shareable outputs that keep the evidence intact.** Raw evidence is never modified.
   Derived reports carry an analysis version and hashes of their inputs, and public exports
   remove device identifiers, addresses, paths and tokens.

## Threat model (for the harness, not for the phone)

The question here is what could make the *evidence* wrong or harmful. The phone's security
isn't under test.

| Concern | Mitigation implemented |
|---|---|
| A false "pass" from the client alone | Pass requires an ack with the same ID **and** exactly one matching collector record |
| Traffic using a different path than assumed (e.g. USB instead of Wi-Fi) | Wi-Fi tests require an observed transport of exactly `wifi` and a peer address that isn't loopback; USB tests require a loopback peer |
| Missing records read as "blocked" | A missing record alone never counts as blocking; with no collector evidence the result is `inconclusive` |
| Duplicate or reused IDs, truncated or damaged logs | Detected and reported; damaged lines are preserved, not dropped |
| Clock confusion | Durations come only from one device's monotonic clock; phone and Mac times are never subtracted |
| LAN clients abusing the collector | Strict validation, 4 KB body limit, connection cap, storage cap, escaped logs, no wildcard bind |
| Other websites driving the dashboard | Bound to 127.0.0.1; Host-header check; CSRF token and same-origin check on import; strict CSP; HTML escaping |
| Data leaking through shared reports | Public exports redact addresses, ports, endpoints, serials, model, paths and tokens; private originals stay git-ignored |
| Test hooks weakening production | Cleartext and the adb trigger exist only in the debug build; checked in the release APK |

Out of scope: a malicious phone OS, a compromised Mac, or a hostile local network that forges
both sides. The harness assumes the operator controls both devices.

## Evidence rules (summary; full rules in `docs/EVIDENCE.md`)

* Every run, step, request and event has a random UUID. Each app process has its own ID.
* Attempts are recorded whether or not they succeed; the collector records only what it receives.
* Raw evidence is append-only. Imports are byte-for-byte copies in new directories.
* Status precedence: `execution_error` > `inconclusive` > `failed` > `passed`.
* `pending` and `running` describe incomplete evidence, not results.
* Connectivity loss or recovery is a reliability observation, never a security finding.

## Evaluation done so far

This was engineering validation of the harness, not an experiment. Real-device runs are listed in
`docs/VALIDATION_SUMMARY.md`. Unit tests cover the classification rules, including the
negative cases (ID mismatch, duplicates, missing evidence, transport change, cancellation).

## Proposed comparison (future work, not implemented)

| Arm | Selection policy |
|---|---|
| Fixed | A predetermined order over the test catalogue and parameters |
| Random | Uniform sampling over the same catalogue and parameter bounds, with a seed |
| Model-guided | A model proposes the next test from the evidence collected so far |

Requirements before running it:
1. **Define real policies and an independent oracle.** For example, a per-app network policy
   whose ground truth is set on the collector or router side. Without them, "finding a problem"
   has no meaning.
2. **Fix a budget.** Number of executions or wall-clock time, identical across arms.
3. **Choose outcome measures.** Executions until the first evidence-backed `failed` or `passed`
   conclusion for each seeded condition, the rate of `inconclusive` results, and harness
   `execution_error`s.
4. **Plan repetition and analysis.** Several seeds per arm and a pre-registered analysis plan.
   The model gets the same evidence the dashboard sees and nothing more.
5. **Assess threats to validity.** Network variability, a single device model, and operator
   actions for the user-assisted tests.

## Limitations

* One phone (Android 13, API 33) and one home Wi-Fi network. The results don't generalise.
* Observed transport is Android's own report and isn't independently verified.
* User-assisted tests depend on the operator acting within the time windows.
* The cleartext HTTP allow-list is debug-only; the collector has no TLS.
* There are no security-policy tests, no model, and no comparison results.
