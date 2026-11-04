"""Static definitions shown by the dashboard. Mirrors app core/TestDef.java and docs/TESTS.md."""

STATUS_DEFINITIONS = {
    "pending": "The run has started on the phone but no step has started yet in the imported evidence.",
    "running": "Steps have started but no run_finished event is in the imported evidence. "
               "Import again after the run ends; a stale import can also look like this.",
    "passed": "Every required step completed and all required evidence is present and consistent: "
              "the app received an acknowledgement carrying the same request ID AND the collector holds "
              "exactly one stored record for that ID with matching run and step IDs, under the test's "
              "preconditions (e.g. observed transport exactly 'wifi').",
    "failed": "The test ran with its preconditions satisfied and the evidence contradicts the expected "
              "behaviour, e.g. a request was not acknowledged, an ack carried a different ID, or the "
              "collector stored duplicates. For these connectivity/reliability checks a failure is a "
              "connectivity or reliability observation, never a security finding.",
    "inconclusive": "The evidence cannot support passed or failed: preconditions were not met (transport "
                    "not exactly 'wifi', traffic arrived via loopback), the user did not act in time, the "
                    "run was cancelled, or the server evidence needed for confirmation is not available. "
                    "A missing server record alone is never treated as proof of blocking.",
    "execution_error": "The harness itself could not execute the test correctly: invalid configuration "
                       "(endpoint/mode mismatch, cleartext not permitted for the host), evidence could not "
                       "be written, the app process ended mid-run, or app evidence is internally "
                       "inconsistent (duplicate request IDs).",
}

STATUS_ORDER = ["pending", "running", "passed", "failed", "inconclusive", "execution_error"]

# Test-specific meanings. STATUS_DEFINITIONS above is the connectivity/reliability set (kept for
# backward compatibility); observation sessions and the cancellation mechanism have their own.
OBSERVATION_STATUS_DEFINITIONS = {
    "pending": STATUS_DEFINITIONS["pending"],
    "running": STATUS_DEFINITIONS["running"],
    "passed": "Collection completed as configured and met the documented data-quality requirements: the session "
              "ran its full duration, every telemetry record is labelled ANDROID_WIFI_TELEMETRY with an explicit "
              "per-field status, at least one RSSI value is valid, and at least 50 % of the expected sampling slots "
              "produced a record (sample completeness; a retrospective rule added in analysis v7, not preregistered "
              "or validated; freshness is reported separately and does not count towards it). Failed probes, signal drops and outages during the session are valid "
              "OBSERVATIONS and do not lower this status.",
    "failed": "Not used for observation sessions: an observation has no expected network behaviour to contradict. "
              "Problems seen during collection are reported as observations and anomaly alerts instead.",
    "inconclusive": "Collection did not complete as configured (stopped early or cancelled by the user), produced no "
                    "valid RSSI value, or covered fewer than 50 % of the expected sampling slots.",
    "execution_error": "The harness could not collect correctly: no telemetry source, a record with an unexpected source "
                       "label, inconsistent evidence, or a step that never finished without a recorded Cancel tap.",
}
STATUS_DEFINITIONS_BY_KIND = {"connectivity": STATUS_DEFINITIONS, "reliability": STATUS_DEFINITIONS,
                              "observation": OBSERVATION_STATUS_DEFINITIONS}
KIND_LABELS = {"connectivity": "Connectivity / reliability checks", "observation": "Observation sessions"}

# Assessed SEPARATELY from the interrupted run's own status (which is normally "inconclusive").
CANCELLATION_VALIDATION_DEFINITIONS = {
    "passed": "A Cancel tap was recorded (cancel_requested), the runner recorded stopping (run_cancelled) within "
              "11 s (one bounded request plus 1 s), and no request started after the tap. Requests already in "
              "flight at the tap may still finish.",
    "failed": "A request started after the tap, the runner kept working without recording run_cancelled, or "
              "stopping took longer than the bound.",
    "not_exercised": "No Cancel tap was recorded during the run (or it arrived after the runner's final check and "
                     "started nothing new). Says nothing about the mechanism.",
}

# Dashboard overview: a later incomplete run must not erase an earlier verified check.
FEATURE_STATUS_DEFINITIONS = {
    "verified": "At least one run of this test reached its success status (passed; for cancellation, a passed "
                "mechanism check) under the rules above. The verifying run is linked and stays linked even if later "
                "runs are incomplete.",
    "not yet verified": "No run has reached its success status yet.",
    "latest run outcome": "Final status of the most recent run of this test, shown separately. It may be "
                          "inconclusive (e.g. user did not act, setup missing) without affecting 'verified'.",
}

CONNECTIVITY_TESTS = [
    {
        "id": "usb_connectivity", "mode": "usb", "title": "USB connectivity check",
        "expected": "Health ok; one message acknowledged with the same request ID.",
        "evidence": "App attempt events + exactly one collector record with that request ID, arriving on loopback.",
        "timeouts": "5 s connect + 5 s read per request; run 30 s.",
        "inconclusive": "Cancelled; no collector records for the run; record not on loopback.",
        "kind": "connectivity",
    },
    {
        "id": "wifi_connectivity", "mode": "wifi", "title": "Direct Wi-Fi connectivity check",
        "expected": "Observed transport exactly 'wifi'; health ok; message acknowledged with same ID.",
        "evidence": "App transport before/after + one collector record whose peer address is not loopback.",
        "timeouts": "5 s connect + 5 s read per request; run 30 s.",
        "inconclusive": "Transport not exactly 'wifi'; record via loopback; cancelled; no collector records.",
        "kind": "connectivity",
    },
    {
        "id": "wifi_sequence", "mode": "wifi", "title": "Bounded message sequence over stable Wi-Fi",
        "expected": "Every message acknowledged with its own ID; transport stays 'wifi'.",
        "evidence": "One app attempt per seq + exactly one collector record per request ID.",
        "timeouts": "count 1–50, interval 200–5000 ms, 10 s per request, run ≤ 10 min.",
        "inconclusive": "Transport change during run; cancelled; no collector records.",
        "kind": "connectivity",
    },
    {
        "id": "app_restart", "mode": "wifi", "title": "App restart, then connectivity check",
        "expected": "Baseline acknowledged; new app process within 5 min; post-restart message acknowledged.",
        "evidence": "Two distinct process IDs; run_resumed event; collector records for both messages.",
        "timeouts": "Restart window 5 min; 15 s Wi-Fi wait after restart.",
        "inconclusive": "Baseline fails; not restarted in time; device reboot; no Wi-Fi after restart; cancelled.",
        "kind": "reliability",
    },
    {
        "id": "wifi_reconnect", "mode": "wifi", "title": "User-assisted Wi-Fi disconnect/reconnect observation",
        "expected": "Baseline acknowledged; Wi-Fi loss and return observed; a message acknowledged within 3 "
                    "recovery attempts. Outage attempt is informational.",
        "evidence": "App transport-change events and wait-step results; collector records for baseline and "
                    "the acknowledged recovery message.",
        "timeouts": "Wi-Fi off wait 120 s; on wait 180 s; settle 5 s; 3 attempts 3 s apart.",
        "inconclusive": "Baseline fails; user does not toggle in time; transport not 'wifi' after return; cancelled.",
        "kind": "reliability",
    },
]

CONNECTIVITY_TESTS.append({
    "id": "wifi_observation", "mode": "wifi", "title": "Wireless observation session (ANDROID_WIFI_TELEMETRY)",
    "expected": "Bounded session records telemetry samples and probe outcomes. Signal changes and failed probes are "
                "observations; 'passed' means the observation completed with at least one valid RSSI sample.",
    "evidence": "telemetry_sample events with per-field status and freshness; capability/transport changes; probe and "
                "health attempts correlated with collector records by request ID.",
    "timeouts": "duration 10–600 s; sampling 1–10 s; probes 2–30 s; 5 s + 5 s per request.",
    "inconclusive": "Cancelled; no valid RSSI sample.",
    "kind": "observation",
})

TESTS_BY_ID = {t["id"]: t for t in CONNECTIVITY_TESTS}

NOT_IMPLEMENTED = [
    ("Security-policy tests",
     "No policy and no independent verification method are defined yet. Nothing here measures security."),
    ("VPN / isolation checks", "Not implemented. No VPN behaviour has been tested."),
    ("Channel State Information (CSI)",
     "Not available from Android APIs. An optional adapter for external CSI hardware exists; it shows "
     "'External CSI hardware not connected' until real frames arrive. No pose, breathing, heart-rate or "
     "through-wall sensing is implemented or claimed."),
    ("Model-guided test selection",
     "Not implemented. Planned later as a comparison against fixed and random test selection."),
    ("Model or security scores and aggregate effectiveness metrics",
     "Not implemented. Implemented visualisations are limited to per-run wireless plots (RSSI and request "
     "duration over phone-monotonic time, with event markers and alert shading) and the session comparison "
     "table; none of these is a score."),
]

LIMITATIONS = [
    "Connectivity and reliability observations only; no security property is tested or claimed.",
    "Observed transport is Android's report for the default network; it is not independently verified.",
    "USB-mode traffic travels over adb reverse (USB) and says nothing about Wi-Fi.",
    "Phone and Mac clocks are not synchronised; durations use each device's own monotonic clock and are "
    "never computed across devices.",
    "Wait-step outcomes (restart observed, Wi-Fi loss/return observed) are app-side observations.",
    "Single observations of elapsed time are not benchmarks.",
    "ANDROID_WIFI_TELEMETRY (RSSI, frequency, link speed) is platform-reported, coarse and possibly cached; "
    "it is not CSI. Repeated values are not counted as fresh samples.",
]
