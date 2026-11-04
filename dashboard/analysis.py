"""Evidence loading, correlation and final status classification (stdlib only).

Raw evidence (never modified here):
  evidence/server/<session>/messages.jsonl   collector records
  evidence/device/<import>/events.jsonl      app events pulled over adb
  collector/data/*.jsonl                      legacy smoke-test records (read-only)

Everything returned by this module is DERIVED and recomputed on demand.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))   # works under `python3 -I`
from catalog import (STATUS_DEFINITIONS, STATUS_DEFINITIONS_BY_KIND, TESTS_BY_ID,  # noqa: E402
                     CANCELLATION_VALIDATION_DEFINITIONS, FEATURE_STATUS_DEFINITIONS)

ANALYSIS_VERSION = "7"   # bump whenever derivation rules change; recorded in every derived output
# v4: reconnect recovery reason names the attempt number and the message seq separately. No status changes.
# v7: status meanings are test-specific (connectivity/reliability vs observation); observation sessions
#     also require >= 50 % sampling coverage; feature verification is tracked separately from the
#     latest run outcome. No run status changed (checked against the v6 report).
# v6: a step left open by a recorded user cancellation is "interrupted" (inconclusive), not
#     execution_error; a cancelled observation session is "stopped early", not "completed".
# v5: explicit counter labels ('recovery attempt n of m' vs 'overall message seq'), recovery attempts
#     ordered by recorded start time, and a numbering-consistency check. No status changes.
# v3: export redaction - absolute-path pattern anchored to token start (relative paths such as
#     collector/data/x.jsonl are no longer redacted). No run status changes.
CANCEL_COMPLETION_BOUND_MS = 11_000   # one bounded request (5 s + 5 s) plus 1 s slack

RANK = {"passed": 1, "failed": 2, "inconclusive": 3, "execution_error": 4}

REQUIRED_KINDS = {
    "usb_connectivity": ["message"],
    "wifi_connectivity": ["message"],
    "wifi_sequence": ["message"],
    "app_restart": ["baseline_message", "post_restart_message"],
    "wifi_reconnect": ["baseline_message"],   # recovery handled as any-of
}
WIFI_TESTS = {"wifi_connectivity", "wifi_sequence", "app_restart", "wifi_reconnect"}
STABLE_TRANSPORT_TESTS = {"wifi_connectivity", "wifi_sequence"}


def worst(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return b if RANK.get(b, 0) > RANK.get(a, 0) else a


def is_loopback(addr: str | None) -> bool:
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return addr == "localhost"


# --------------------------------------------------------------------------- loading
@dataclass
class Evidence:
    app_events: list = field(default_factory=list)          # deduplicated, ordered
    server_records: list = field(default_factory=list)
    legacy_records: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    issues: list = field(default_factory=list)              # evidence-level problems

    def server_index(self) -> dict:
        idx: dict = {}
        for r in self.server_records:
            idx.setdefault(r.get("request_id"), []).append(r)
        return idx


def _read_jsonl(path: Path, issues: list, label: str) -> list:
    out = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                issues.append(f"{label}: line {n} is not valid JSON")
                continue
            if isinstance(obj, dict):
                out.append(obj)
            else:
                issues.append(f"{label}: line {n} is not an object")
    return out


def load_evidence(root: Path) -> Evidence:
    ev = Evidence()
    server_dir = root / "evidence" / "server"
    for sd in sorted(p for p in server_dir.glob("*") if p.is_dir()) if server_dir.exists() else []:
        meta = {}
        if (sd / "session.json").exists():
            try:
                meta = json.loads((sd / "session.json").read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                ev.issues.append(f"server session {sd.name}: session.json invalid")
        meta["_dir"] = sd.name
        ev.sessions.append(meta)
        if (sd / "messages.jsonl").exists():
            for r in _read_jsonl(sd / "messages.jsonl", ev.issues, f"server {sd.name}"):
                r["_session_dir"] = sd.name
                ev.server_records.append(r)

    legacy = root / "collector" / "data"
    if legacy.exists():
        for f in sorted(legacy.glob("*.jsonl")):
            for r in _read_jsonl(f, ev.issues, f"legacy {f.name}"):
                r["_legacy_file"] = f.name
                ev.legacy_records.append(r)

    device_dir = root / "evidence" / "device"
    seen: dict = {}
    for d in sorted(p for p in device_dir.glob("*") if p.is_dir()) if device_dir.exists() else []:
        meta = {"_dir": d.name}
        if (d / "import.json").exists():
            try:
                meta.update(json.loads((d / "import.json").read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                ev.issues.append(f"device import {d.name}: import.json invalid")
        ev.imports.append(meta)
        if not (d / "events.jsonl").exists():
            continue
        for e in _read_jsonl(d / "events.jsonl", ev.issues, f"device {d.name}"):
            eid = e.get("event_id")
            if not eid:
                ev.issues.append(f"device {d.name}: event without event_id")
                continue
            if eid in seen:
                if seen[eid] != e:
                    ev.issues.append(f"conflicting content for event_id {eid}")
                continue
            seen[eid] = e
            ev.app_events.append(e)
    return ev


# --------------------------------------------------------------------------- runs
@dataclass
class Attempt:
    request_id: str
    run_id: str
    step_id: str
    kind: str
    seq: int | None
    started: dict
    finished: dict | None = None
    server: list = field(default_factory=list)
    correlation: str = ""

    @property
    def outcome(self):
        return self.finished.get("outcome") if self.finished else None

    @property
    def acked(self):
        return self.outcome == "ack" and self.finished.get("ack_request_id") == self.request_id

    @property
    def transports(self):
        b = self.started.get("observed_transport")
        a = self.finished.get("observed_transport_after") if self.finished else None
        return b, a


@dataclass
class Step:
    step_id: str
    index: int
    kind: str
    description: str
    started: dict
    finished: dict | None = None
    attempts: list = field(default_factory=list)

    @property
    def app_status(self):
        return self.finished.get("status") if self.finished else None


@dataclass
class Run:
    run_id: str
    events: list = field(default_factory=list)
    steps: dict = field(default_factory=dict)
    attempts: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    started: dict | None = None
    finished: dict | None = None
    status: str = "pending"
    reasons: list = field(default_factory=list)
    checks: list = field(default_factory=list)

    @property
    def test_id(self):
        for e in self.events:
            if e.get("test_id"):
                return e["test_id"]
        return None

    @property
    def processes(self):
        out = []
        for e in self.events:
            p = e.get("process_id")
            if p and p not in out:
                out.append(p)
        return out


def build_runs(ev: Evidence) -> dict:
    runs: dict = {}
    for e in ev.app_events:
        rid = e.get("run_id")
        if not rid:
            continue
        run = runs.setdefault(rid, Run(rid))
        run.events.append(e)
        t = e.get("event")
        if t == "run_started":
            if run.started:
                run.issues.append("duplicate run_started")
            run.started = e
        elif t == "run_finished":
            if run.finished and run.finished.get("status") != e.get("status"):
                run.issues.append("multiple run_finished events with different statuses")
            run.finished = run.finished or e
        elif t == "step_started":
            run.steps[e["step_id"]] = Step(e["step_id"], e.get("step_index", 0), e.get("step_kind", ""),
                                           e.get("description", ""), e)
        elif t == "step_finished":
            st = run.steps.get(e.get("step_id"))
            if st:
                st.finished = e
            else:
                run.issues.append(f"step_finished for unknown step {e.get('step_id')}")
        elif t == "attempt_started":
            rq = e.get("request_id")
            if rq in run.attempts:
                run.issues.append(f"duplicate app request_id {rq}")
                continue
            a = Attempt(rq, rid, e.get("step_id"), e.get("kind"), e.get("seq"), e)
            run.attempts[rq] = a
            st = run.steps.get(e.get("step_id"))
            if st:
                st.attempts.append(a)
        elif t == "attempt_finished":
            a = run.attempts.get(e.get("request_id"))
            if a is None:
                run.issues.append(f"attempt_finished for unknown request {e.get('request_id')}")
            elif a.finished is not None:
                run.issues.append(f"duplicate attempt_finished for {a.request_id}")
            else:
                a.finished = e
    # request IDs must be unique across ALL runs too
    owners: dict = {}
    for run in runs.values():
        for rq in run.attempts:
            owners.setdefault(rq, []).append(run.run_id)
    for rq, rids in owners.items():
        if len(rids) > 1:
            for rid in rids:
                others = ", ".join(r for r in rids if r != rid)
                runs[rid].issues.append(f"request_id {rq} also used by run(s) {others}")
    return runs


# --------------------------------------------------------------------------- correlation
def correlate(a: Attempt, index: dict) -> str:
    recs = index.get(a.request_id, [])
    a.server = recs
    if a.kind == "health":
        a.correlation = "health_app_only"
    elif a.finished is None:
        a.correlation = "no_app_result"
    elif len(recs) > 1:
        a.correlation = "duplicate_server_records"
    elif len(recs) == 1 and (recs[0].get("run_id") != a.run_id or recs[0].get("step_id") != a.step_id):
        a.correlation = "run_or_step_mismatch"
    elif a.acked and len(recs) == 1:
        a.correlation = "matched"
    elif a.acked:
        a.correlation = "missing_server_record"
    elif len(recs) == 1:
        a.correlation = "delivered_not_acked"
    else:
        a.correlation = "not_delivered"
    return a.correlation


# --------------------------------------------------------------------------- classification
def classify(run: Run, index: dict) -> Run:
    """Final status from raw app + server evidence. Mutates and returns run."""
    run.reasons, run.checks = [], []
    status = None

    def mark(s, why):
        nonlocal status
        if RANK.get(s, 0) > RANK.get(status, 0):
            status = s
        run.reasons.append(f"{s}: {why}")

    def check(name, ok, detail=""):
        run.checks.append({"check": name, "ok": bool(ok), "detail": detail})

    for a in run.attempts.values():
        correlate(a, index)

    if run.started is None:
        run.status = "execution_error"
        run.reasons = ["execution_error: events for this run exist but run_started is missing"]
        return run
    if run.finished is None:
        run.status = "running" if run.steps else "pending"
        run.reasons = [f"{run.status}: no run_finished event in the imported evidence"]
        return run

    test = run.test_id
    if test not in TESTS_BY_ID:
        mark("execution_error", f"unknown test_id {test!r}")
    for issue in run.issues:
        mark("execution_error", f"evidence inconsistency: {issue}")

    app_status = run.finished.get("status")
    check("app run_finished present", True, f"app-side status {app_status}: {run.finished.get('reason')}")
    if app_status == "execution_error":
        mark("execution_error", f"app reported: {run.finished.get('reason')}")
    cx = cancellation(run)
    if cx["requested"] or cx["completed"]:
        check("cancel_requested recorded (UI tap)", cx["requested"],
              f"+{cx['requested_ms']} ms" if cx["requested"] else "no tap event (older build or non-UI cancel)")
        check("run_cancelled recorded (runner stopped)", cx["completed"],
              f"+{cx['completed_ms']} ms" if cx["completed"] else "")
        check("no new request started after cancel_requested", not cx["started_after_request"],
              ", ".join(cx["started_after_request"]) or f"in flight at tap: {len(cx['in_flight'])}")
    if cx["completed"]:
        mark("inconclusive", "run was cancelled by the user")
    if cx["started_after_request"]:
        mark("execution_error", f"{len(cx['started_after_request'])} request(s) started after Cancel was tapped")
    # A tap that lands after the runner's final cancellation check (and starts nothing new)
    # is a benign race, not a harness error: the run simply ended normally.

    server_for_run = any(r.get("run_id") == run.run_id for recs in index.values() for r in recs)
    check("collector records exist for this run", server_for_run)

    steps = sorted(run.steps.values(), key=lambda s: s.index)
    # A step left open by a RECORDED user cancellation is "interrupted", not a harness error:
    # app <= 0.4.1 does not write step_finished for the step active when Cancel stops the run.
    req = next((e for e in run.events if e.get("event") == "cancel_requested"), None)
    done = next((e for e in run.events if e.get("event") == "run_cancelled"), None)
    last_started = max(steps, key=lambda x: x.started["mono_ns"]) if steps else None
    for st in steps:
        if st.finished is None:
            interrupted = (req is not None and done is not None and st is last_started
                           and st.started["mono_ns"] < req["mono_ns"])
            if interrupted:
                check(f"step {st.index} ({st.kind}) interrupted by cancellation", True,
                      "no step_finished recorded; the step was active when Cancel was tapped")
                mark("inconclusive", f"step {st.index} ({st.kind}) interrupted by user cancellation")
            else:
                mark("execution_error", f"step {st.index} ({st.kind}) never finished")
        elif st.app_status == "inconclusive":
            mark("inconclusive", f"step {st.index} ({st.kind}): {st.finished.get('reason')}")
        elif st.app_status == "execution_error":
            mark("execution_error", f"step {st.index} ({st.kind}): {st.finished.get('reason')}")

    # health steps (app-only evidence by design)
    for st in steps:
        if st.kind == "health" and st.app_status == "failed":
            mark("failed", f"health check failed: {st.finished.get('reason')}")

    def evaluate(a: Attempt, label: str, require_wifi: bool):
        b, af = a.transports
        if require_wifi and (b != "wifi" or af != "wifi"):
            mark("inconclusive", f"{label}: observed transport {b!r}→{af!r}, not exactly 'wifi'")
            return
        c = a.correlation
        if c == "matched":
            rec = a.server[0]
            if test == "usb_connectivity" and not is_loopback(rec.get("remote_addr")):
                mark("inconclusive", f"{label}: USB-mode record did not arrive on loopback")
            elif test in WIFI_TESTS and is_loopback(rec.get("remote_addr")):
                mark("inconclusive", f"{label}: record arrived via loopback (adb reverse?), not direct Wi-Fi")
            else:
                mark("passed", f"{label}: ack with same ID and one matching collector record")
        elif c == "missing_server_record":
            if server_for_run:
                mark("failed", f"{label}: app got an ack but the collector has no stored record")
            else:
                mark("inconclusive", f"{label}: app ack, but no collector records for this run are available")
        elif c in ("duplicate_server_records", "run_or_step_mismatch"):
            mark("failed", f"{label}: {c.replace('_', ' ')}")
        elif c == "delivered_not_acked":
            mark("failed", f"{label}: collector stored it but the app saw {a.outcome}")
        elif c == "not_delivered":
            if a.outcome == "cancelled":
                mark("inconclusive", f"{label}: cancelled")
            elif a.outcome == "cleartext_not_permitted":
                mark("execution_error", f"{label}: cleartext not permitted for this host")
            else:
                mark("failed", f"{label}: not delivered ({a.outcome}: {a.finished.get('error')})")
        elif c == "no_app_result":
            mark("execution_error", f"{label}: attempt has no recorded result")

    if test == "wifi_observation":
        tele = [e for e in run.events if e.get("event") == "telemetry_sample"]
        ok = [e for e in tele if (e.get("rssi_dbm") or {}).get("status") == "ok"]
        check("telemetry samples recorded (ANDROID_WIFI_TELEMETRY)", bool(tele), f"{len(tele)} samples, {len(ok)} with RSSI")
        check("all telemetry labelled ANDROID_WIFI_TELEMETRY", all(e.get("source") == "ANDROID_WIFI_TELEMETRY" for e in tele))
        if any(e.get("source") != "ANDROID_WIFI_TELEMETRY" for e in tele):
            mark("execution_error", "telemetry record with an unexpected source label")
        p = run.started.get("params") or {}
        expected = (p.get("duration_s") or 0) * 1000 // max(1, p.get("sample_interval_ms") or 1)
        coverage = len(tele) / expected if expected else None
        if done is None:
            check("sampling coverage >= 50 % of expected slots", coverage is not None and coverage >= 0.5,
                  f"{len(tele)} of {expected} expected" if expected else "no duration/interval recorded")
        if ok and done is not None:
            mark("inconclusive", f"observation stopped early by the user after {len(tele)} samples ({len(ok)} with RSSI)")
        elif ok and (coverage is None or coverage < 0.5):
            mark("inconclusive", f"sampling coverage too low: {len(tele)} of {expected} expected samples")
        elif ok:
            mark("passed", f"observation completed: {len(tele)} samples, {len(ok)} with RSSI"
                           + (f", {coverage:.0%} sampling coverage" if coverage is not None else ""))
        else:
            mark("inconclusive", "no valid RSSI sample in the session")
        if app_status == "inconclusive":
            mark("inconclusive", f"app reported: {run.finished.get('reason')}")
        run.status = status
        return run

    required = REQUIRED_KINDS.get(test, [])
    req_wifi = test in WIFI_TESTS
    seen_required = set()
    for st in steps:
        if st.kind in required:
            seen_required.add(st.kind)
            for a in st.attempts:
                evaluate(a, f"step {st.index} {st.kind}" + (f" #{a.seq}" if a.seq else ""), req_wifi)

    # test-specific evidence
    if test in STABLE_TRANSPORT_TESTS:
        changes = [e for e in run.events if e.get("event") == "transport_changed" and e.get("transport") != "wifi"]
        check("no non-wifi transport change during run", not changes,
              ", ".join(e.get("transport", "?") for e in changes))
        if changes:
            mark("inconclusive", "transport changed during the run")
    if test == "wifi_sequence":
        want = (run.started.get("params") or {}).get("count")
        msgs = [a for st in steps if st.kind == "message" for a in st.attempts]
        check("sequence attempts recorded", want is not None, f"{len(msgs)} of {want}")
        if want is not None and len(msgs) < want and app_status not in ("inconclusive", "execution_error"):
            mark("inconclusive", f"only {len(msgs)} of {want} messages attempted")
    if test == "app_restart":
        resumed = [e for e in run.events if e.get("event") == "run_resumed"]
        procs = run.processes
        check("new process observed (≥2 process IDs, run_resumed)", len(procs) >= 2 and bool(resumed),
              f"{len(procs)} process IDs")
        if not (len(procs) >= 2 and resumed):
            mark("inconclusive", "no app restart evidence (single process / no run_resumed)")
    if test == "wifi_reconnect":
        rec_steps = [st for st in steps if st.kind == "recovery_messages"]
        loss = any(st.kind == "wait_wifi_loss" and st.app_status == "passed" for st in steps)
        back = any(st.kind == "wait_wifi_restore" and st.app_status == "passed" for st in steps)
        check("Wi-Fi loss observed (app)", loss)
        check("Wi-Fi return observed (app)", back)
        if rec_steps:
            # Two distinct counters, both derived from recorded attempts (never hardcoded):
            #   recovery attempt n = position within the recovery_messages step, ordered by recorded start time
            #   overall message seq = the seq the app recorded across the whole run (baseline 1, outage 2, ...)
            atts = sorted(rec_steps[0].attempts, key=lambda a: a.started["mono_ns"])
            base_seq = max([a.seq for st in steps if st.kind in ("baseline_message", "outage_message")
                            for a in st.attempts if a.seq] or [0])
            consistent = all(a.seq == base_seq + n for n, a in enumerate(atts, 1))
            check("recovery numbering consistent (overall seq = last pre-recovery seq + recovery attempt n)",
                  consistent, ", ".join(f"recovery attempt {n} = overall seq {a.seq}" for n, a in enumerate(atts, 1)))
            ok = [a for a in atts if a.correlation == "matched" and a.transports == ("wifi", "wifi")]
            if ok:
                n = atts.index(ok[0]) + 1
                mark("passed", f"recovery: recovery attempt {n} of {len(atts)} recorded "
                               f"(max {3}) matched; overall message seq {ok[0].seq} in this run")
            elif any(a.correlation == "missing_server_record" for a in atts) and not server_for_run:
                mark("inconclusive", "recovery acked but collector records unavailable")
            elif atts and all(a.transports == ("wifi", "wifi") for a in atts):
                mark("failed", "no matched recovery message after Wi-Fi returned (reliability observation)")
            elif atts:
                mark("inconclusive", "transport not 'wifi' during recovery attempts")
        for st in steps:
            if st.kind == "outage_message":
                for a in st.attempts:
                    check("outage attempt (informational)", True, f"{a.outcome}; collector records: {len(a.server)}")

    missing = [k for k in required if k not in seen_required]
    if missing and status not in ("inconclusive", "execution_error"):
        mark("inconclusive", f"required step(s) not reached: {', '.join(missing)}")
    if app_status == "inconclusive" and status in (None, "passed", "failed"):
        mark("inconclusive", f"app reported: {run.finished.get('reason')}")
    if app_status == "failed" and status in (None, "passed"):
        mark("failed", f"app reported: {run.finished.get('reason')}")
    if status is None:
        mark("inconclusive", "no required evidence was evaluated")
    run.status = status
    return run


def analyse(root: Path):
    ev = load_evidence(root)
    index = ev.server_index()
    runs = build_runs(ev)
    for r in runs.values():
        classify(r, index)
    return ev, runs


# --------------------------------------------------------------------------- cancellation
def cancellation(run: Run) -> dict:
    """Separates the UI tap (cancel_requested) from completion (run_cancelled), and classifies
    each request relative to the tap, using the phone monotonic clock only."""
    origin = run.started.get("mono_ns") if run.started else None
    req = next((e for e in run.events if e.get("event") == "cancel_requested"), None)
    done = next((e for e in run.events if e.get("event") == "run_cancelled"), None)

    def rel(e):
        return round((e["mono_ns"] - origin) / 1e6, 1) if e and origin is not None else None

    out = {"requested": req is not None, "requested_ms": rel(req), "completed": done is not None,
           "completed_ms": rel(done), "completed_before_requested": False,
           "finished_before_tap": [], "in_flight": [], "started_after_request": [],
           "stop_latency_ms": None, "validation": "not_exercised", "validation_reason": "no Cancel tap recorded"}
    if req is None:
        if done is not None:
            out["validation_reason"] = "run_cancelled without a recorded tap (older build or non-UI cancel)"
        return out
    if done is not None and done["mono_ns"] < req["mono_ns"]:
        out["completed_before_requested"] = True
    t = req["mono_ns"]
    for a in run.attempts.values():
        s = a.started["mono_ns"]
        f = a.finished["mono_ns"] if a.finished else None
        label = f"{a.kind}#{a.seq} {a.request_id[:8]} → {a.outcome}"
        if s > t:
            out["started_after_request"].append(label)
        elif f is None or f > t:
            out["in_flight"].append(label)
        else:
            out["finished_before_tap"].append(label)
    # Separate verdict on the cancellation MECHANISM (independent of the run's own status).
    if done is not None:
        out["stop_latency_ms"] = round((done["mono_ns"] - req["mono_ns"]) / 1e6, 1)
    if out["started_after_request"]:
        out["validation"], out["validation_reason"] = "failed", "new request(s) started after the tap"
    elif done is None:
        later = [e for e in run.events if e["mono_ns"] > req["mono_ns"] and e.get("event") in ("step_started", "attempt_started")]
        if later:
            out["validation"], out["validation_reason"] = "failed", "runner kept working after the tap and never stopped"
        else:
            out["validation"], out["validation_reason"] = ("not_exercised",
                "tap arrived after the runner's last cancellation check; the run ended normally")
    elif out["stop_latency_ms"] is not None and out["stop_latency_ms"] < 0:
        out["validation"], out["validation_reason"] = "failed", "run_cancelled precedes the tap (clock/evidence inconsistency)"
    elif out["stop_latency_ms"] > CANCEL_COMPLETION_BOUND_MS:
        out["validation"], out["validation_reason"] = ("failed",
            f"stop took {out['stop_latency_ms']} ms (> {CANCEL_COMPLETION_BOUND_MS} ms bound)")
    else:
        out["validation"], out["validation_reason"] = ("passed",
            f"stopped {out['stop_latency_ms']} ms after the tap; {len(out['in_flight'])} request(s) in flight at the tap; "
            "no new request started")
    return out


def ui_recreations(run: Run) -> list:
    origin = run.started.get("mono_ns") if run.started else None
    return [{"t_ms": round((e["mono_ns"] - origin) / 1e6, 1) if origin else None,
             "orientation": e.get("orientation"), "restored_state": e.get("restored_state"),
             "process_id": e.get("process_id")}
            for e in run.events if e.get("event") == "activity_created"]


# --------------------------------------------------------------------------- timeline
def timeline(run: Run) -> list:
    """Rows ordered by evidence order. Each clock domain has its own origin; never cross-subtracted."""
    rows = []
    origin = run.started.get("mono_ns") if run.started else None
    for e in sorted(run.events, key=lambda x: x.get("mono_ns") or 0):
        rel = None
        if origin is not None and isinstance(e.get("mono_ns"), int):
            rel = round((e["mono_ns"] - origin) / 1e6, 1)
        rows.append({"source": "app", "clock": "phone monotonic (ms since run_started)", "t_ms": rel,
                     "event": e.get("event"), "step_id": e.get("step_id"), "request_id": e.get("request_id"),
                     "detail": _event_detail(e), "wall_time": e.get("wall_time")})
    srv = sorted((r for a in run.attempts.values() for r in a.server if isinstance(r.get("server_mono_ns"), int)),
                 key=lambda r: r["server_mono_ns"])
    s0 = srv[0]["server_mono_ns"] if srv else None
    for r in srv:
        rows.append({"source": "server", "clock": "Mac monotonic (ms since first record of this run)",
                     "t_ms": round((r["server_mono_ns"] - s0) / 1e6, 1), "event": "record_stored",
                     "step_id": r.get("step_id"), "request_id": r.get("request_id"),
                     "detail": f"seq={r.get('seq')} transport={r.get('transport')}",
                     "wall_time": r.get("server_time")})
    return rows


def _event_detail(e: dict) -> str:
    t = e.get("event")
    if t == "attempt_started":
        return f"{e.get('kind')} seq={e.get('seq')} transport={e.get('observed_transport')}"
    if t == "attempt_finished":
        return (f"{e.get('outcome')} http={e.get('http_status')} {e.get('elapsed_ms')} ms "
                f"after={e.get('observed_transport_after')}" + (f" err={e.get('error')}" if e.get("error") else ""))
    if t in ("step_started",):
        return f"#{e.get('step_index')} {e.get('step_kind')}: {e.get('description')}"
    if t in ("step_finished", "run_finished"):
        return f"{e.get('status')}: {e.get('reason')}"
    if t == "transport_changed":
        return f"→ {e.get('transport')}"
    if t == "prompt_shown":
        return e.get("text", "")
    if t == "cancel_requested":
        return f"Cancel tapped ({e.get('source')}); progress was: {e.get('progress_text')}"
    if t == "run_cancelled":
        return "runner stopped (cancellation completed)"
    if t == "activity_created":
        return f"UI recreated: {e.get('orientation')} restored_state={e.get('restored_state')}"
    if t == "run_resumed":
        return f"previous process {e.get('previous_process_id')}"
    return ""


# --------------------------------------------------------------------------- export / redaction
REDACT_KEYS = {"remote_addr", "remote_port", "server_addr", "server_port", "endpoint", "url",
               "serial", "model", "device", "_session_dir", "_legacy_file", "pid", "hosts",
               "server_remote_addr"}
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_CANDIDATE = re.compile(r"(?<![0-9A-Za-z:])[0-9A-Fa-f:]*:[0-9A-Fa-f:]*:[0-9A-Fa-f:]*(?:%[\w.]+)?(?![0-9A-Za-z:])")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
_PATH = re.compile(r"(?<![\w.\-/])/(?:Users|home|data|private|var|tmp|storage|sdcard|opt|Volumes)/[^\s\"'<>]+")


def _ipv6_sub(m):
    token = m.group(0).split("%")[0]
    try:
        ipaddress.IPv6Address(token)
        return "[ip]"
    except ValueError:
        return m.group(0)   # e.g. clock times like 18:53:35 are not addresses


def redact(obj, secrets: tuple = ()):
    """Deep copy with device identifiers, local addresses, local paths and given secrets removed."""
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k in REDACT_KEYS else redact(v, secrets)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        s = obj
        for sec in secrets:
            if sec and len(sec) >= 4:
                s = s.replace(sec, "[redacted]")
        s = _EMAIL.sub("[email]", s)
        s = _MAC.sub("[mac]", s)
        s = _PATH.sub("[path]", s)
        s = _IPV4.sub("[ip]", s)
        return _IPV6_CANDIDATE.sub(_ipv6_sub, s)
    return obj


def known_secrets(ev: "Evidence") -> tuple:
    """Device serials/models recorded in private import metadata, scrubbed from public exports."""
    out = set()
    for m in ev.imports:
        for k in ("serial", "model"):
            if isinstance(m.get(k), str):
                out.add(m[k])
    return tuple(sorted(out, key=len, reverse=True))


def _kind(test_id) -> str:
    k = (TESTS_BY_ID.get(test_id) or {}).get("kind", "connectivity")
    return k if k in STATUS_DEFINITIONS_BY_KIND else "connectivity"


def feature_status(runs: dict) -> dict:
    """Per test: latest run outcome AND whether any run verified it (kept separate on purpose)."""
    out = {}
    by_test = {}
    for r in runs.values():
        if r.started:
            by_test.setdefault(r.test_id, []).append(r)
    for test, rs in by_test.items():
        rs.sort(key=lambda r: r.started.get("wall_time", ""))
        ok = [r for r in rs if r.status == "passed"]
        out[test] = {"latest": {"run_id": rs[-1].run_id, "status": rs[-1].status,
                                "started": rs[-1].started.get("wall_time")},
                     "verified": bool(ok),
                     "verified_by": {"run_id": ok[-1].run_id, "started": ok[-1].started.get("wall_time")} if ok else None,
                     "passing_runs": len(ok), "runs": len(rs)}
    # Mechanism checks assessed inside other runs.
    canc = [r for r in runs.values() if r.started and cancellation(r)["requested"]]
    canc.sort(key=lambda r: r.started.get("wall_time", ""))
    okc = [r for r in canc if cancellation(r)["validation"] == "passed"]
    out["ui_cancellation"] = {
        "latest": {"run_id": canc[-1].run_id, "status": cancellation(canc[-1])["validation"],
                   "started": canc[-1].started.get("wall_time")} if canc else None,
        "verified": bool(okc),
        "verified_by": {"run_id": okc[-1].run_id, "started": okc[-1].started.get("wall_time")} if okc else None,
        "passing_runs": len(okc), "runs": len(canc)}
    rot = [r for r in runs.values() if r.started and any(e.get("event") == "activity_created" and e.get("restored_state")
                                                         and e.get("run_in_progress") for e in r.events)]
    rot.sort(key=lambda r: r.started.get("wall_time", ""))
    okr = [r for r in rot if r.status == "passed"]
    out["rotation_during_run"] = {
        "latest": {"run_id": rot[-1].run_id, "status": rot[-1].status, "started": rot[-1].started.get("wall_time")} if rot else None,
        "verified": bool(okr),
        "verified_by": {"run_id": okr[0].run_id, "started": okr[0].started.get("wall_time")} if okr else None,
        "passing_runs": len(okr), "runs": len(rot)}
    return out


def _wireless(run: Run):
    if run.test_id != "wifi_observation" or not run.started:
        return None
    import wireless
    server_for_run = any(len(a.server) for a in run.attempts.values())
    return wireless.summarize(run, server_for_run)


def run_summary(run: Run) -> dict:
    steps = []
    for st in sorted(run.steps.values(), key=lambda s: s.index):
        steps.append({
            "step_id": st.step_id, "index": st.index, "kind": st.kind, "description": st.description,
            "app_status": st.app_status, "app_reason": st.finished.get("reason") if st.finished else None,
            "informational": bool(st.finished and st.finished.get("informational")),
            "attempts": [{
                "request_id": a.request_id, "kind": a.kind, "seq": a.seq, "outcome": a.outcome,
                "acked_same_id": a.acked, "elapsed_ms": a.finished.get("elapsed_ms") if a.finished else None,
                "observed_transport_before": a.transports[0], "observed_transport_after": a.transports[1],
                "error": a.finished.get("error") if a.finished else None,
                "server_record_count": len(a.server), "correlation": a.correlation,
                "server_remote_addr": a.server[0].get("remote_addr") if len(a.server) == 1 else None,
                "server_addr": a.server[0].get("server_addr") if len(a.server) == 1 else None,
            } for a in st.attempts],
        })
    return {
        "run_id": run.run_id, "test_id": run.test_id,
        "test_kind": TESTS_BY_ID.get(run.test_id, {}).get("kind"),
        "configured_mode": run.started.get("test_mode") if run.started else None,
        "endpoint": run.started.get("endpoint") if run.started else None,
        "params": run.started.get("params") if run.started else None,
        "started_wall_time_phone": run.started.get("wall_time") if run.started else None,
        "duration_ms_phone_monotonic": run.finished.get("duration_ms") if run.finished else None,
        "app_status_provisional": run.finished.get("status") if run.finished else None,
        "final_status": run.status, "status_kind": _kind(run.test_id),
        "status_meaning": STATUS_DEFINITIONS_BY_KIND[_kind(run.test_id)].get(run.status),
        "reasons": run.reasons, "checks": run.checks, "issues": run.issues,
        "analysis_version": ANALYSIS_VERSION,
        "process_ids": run.processes, "cancellation": cancellation(run) if run.started else None,
        "ui_recreations": ui_recreations(run),
        "wireless": _wireless(run),
        "steps": steps,
    }


def export_run(run: Run, scope: str, generated_at: str, secrets: tuple = ()) -> dict:
    from catalog import LIMITATIONS
    doc = {
        "schema": "ang.export/1", "scope": scope, "generated_at": generated_at,
        "analysis_version": ANALYSIS_VERSION,
        "note": "derived is recomputed from raw; raw is copied verbatim"
                + (" except redacted device identifiers and local addresses" if scope == "public" else ""),
        "derived": run_summary(run),
        "raw": {"app_events": copy.deepcopy(run.events),
                "server_records": [copy.deepcopy(r) for a in run.attempts.values() for r in a.server]},
        "timeline": timeline(run),
        "status_definitions": STATUS_DEFINITIONS_BY_KIND[_kind(run.test_id)],
        "status_definitions_by_kind": STATUS_DEFINITIONS_BY_KIND,
        "cancellation_validation_definitions": CANCELLATION_VALIDATION_DEFINITIONS,
        "limitations": LIMITATIONS,
    }
    return redact(doc, secrets) if scope == "public" else doc


# --------------------------------------------------------------------------- derived report (CLI)
def write_derived_report(root: Path, out_dir: Path) -> Path:
    """Writes a NEW derived report directory; raw evidence is only read."""
    import hashlib
    from datetime import datetime, timezone
    ev, runs = analyse(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = out_dir / f"{stamp}_analysis-v{ANALYSIS_VERSION}"
    d.mkdir(parents=True, exist_ok=False)
    inputs = []
    for f in sorted((root / "evidence").glob("*/*/*.jsonl")) + sorted((root / "collector" / "data").glob("*.jsonl")):
        inputs.append({"path": str(f.relative_to(root)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                       "bytes": f.stat().st_size})
    summary = {"schema": "ang.derived-report/1", "analysis_version": ANALYSIS_VERSION,
               "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "inputs": inputs, "evidence_issues": ev.issues,
               "runs": [{k: run_summary(r)[k] for k in ("run_id", "test_id", "final_status", "app_status_provisional",
                                                         "started_wall_time_phone", "reasons")}
                        | {"cancellation_validation": (run_summary(r)["cancellation"] or {}).get("validation"),
                           "ui_recreations": len(ui_recreations(r))}
                        for r in sorted(runs.values(), key=lambda r: (r.started or {}).get("wall_time", ""))]}
    (d / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    rd = d / "runs"
    rd.mkdir()
    for r in runs.values():
        (rd / f"{r.run_id}.json").write_text(json.dumps(run_summary(r), indent=2) + "\n")
    return d


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parent.parent
    print(write_derived_report(root, root / "evidence" / "derived"))
