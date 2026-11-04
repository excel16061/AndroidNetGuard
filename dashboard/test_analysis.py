"""Tests for dashboard/analysis.py. Run: python3 -I -m unittest discover -s dashboard -v"""

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis  # noqa: E402


def uid():
    return str(uuid.uuid4())


class Builder:
    """Builds synthetic app events + server records in the real on-disk layout."""

    def __init__(self, test_id="wifi_connectivity", mode="wifi", proc=None):
        self.run_id = uid()
        self.test_id = test_id
        self.proc = proc or uid()
        self.mono = 10_000_000_000
        self.events, self.server = [], []
        self.step_index = 0
        self.ev("run_started", test_mode=mode, endpoint="http://192.0.2.6:8765",
                params={"count": 3, "interval_ms": 200} if test_id == "wifi_sequence" else {},
                observed_transport="wifi")

    def ev(self, event, **kw):
        self.mono += 5_000_000
        e = {"schema": "ang.app.event/1", "event_id": uid(), "event": event, "wall_time": "2026-11-03T00:00:00Z",
             "mono_ns": self.mono, "process_id": self.proc, "app_version": "0.2.0",
             "run_id": self.run_id, "test_id": self.test_id}
        e.update(kw)
        self.events.append(e)
        return e

    def step(self, kind, status="passed", attempts=(), informational=False, reason="ok"):
        sid = uid()
        self.step_index += 1
        self.ev("step_started", step_id=sid, step_index=self.step_index, step_kind=kind, description=kind)
        rids = []
        for spec in attempts:
            rids.append(self.attempt(sid, **spec))
        self.ev("step_finished", step_id=sid, status=status, reason=reason, informational=informational)
        return sid, rids

    def attempt(self, sid, kind="message", seq=1, outcome="ack", before="wifi", after="wifi",
                server=1, remote="192.0.2.196", ack_id=None, server_run=None, rid=None):
        rid = rid or uid()
        self.ev("attempt_started", step_id=sid, request_id=rid, kind=kind, seq=seq,
                url="http://192.0.2.6:8765/v1/test-message", observed_transport=before)
        ack = (ack_id or rid) if outcome == "ack" else None
        self.ev("attempt_finished", step_id=sid, request_id=rid, outcome=outcome,
                http_status=200 if outcome == "ack" else -1, ack_request_id=ack, error=None,
                elapsed_ms=8, observed_transport_after=after)
        if kind == "message":
            for _ in range(server):
                self.server.append({"server_time": "x", "server_mono_ns": 5_000_000_000 + len(self.server),
                                    "remote_addr": remote, "remote_port": 50000, "server_addr": "192.0.2.6",
                                    "server_port": 8765, "request_id": rid, "message": "m",
                                    "run_id": server_run or self.run_id, "step_id": sid, "seq": seq,
                                    "transport": before})
        return rid

    def finish(self, status="passed", reason="ok", cancelled=False):
        if cancelled:
            self.ev("run_cancelled")
        self.ev("run_finished", status=status, status_scope="app_provisional", reason=reason, duration_ms=50)
        return self

    def write(self, root: Path, with_server=True, extra_events=()):
        d = root / "evidence" / "device" / f"imp_{uid()[:8]}"
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in list(self.events) + list(extra_events)))
        if with_server:
            s = root / "evidence" / "server" / f"sess_{uid()[:8]}"
            s.mkdir(parents=True)
            (s / "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.server))
        return self


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, b):
        _, runs = analysis.analyse(self.root)
        return runs[b.run_id]

    def connectivity(self, **msg):
        b = Builder()
        b.step("health", attempts=[{"kind": "health"}])
        b.step("message", attempts=[msg or {}])
        return b

    # --- correlation / passed ---------------------------------------------------
    def test_matched_ack_and_single_record_passes(self):
        b = self.connectivity().finish().write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "passed", r.reasons)
        msg = [a for a in r.attempts.values() if a.kind == "message"][0]
        self.assertEqual(msg.correlation, "matched")

    def test_health_has_no_server_correlation(self):
        b = self.connectivity().finish().write(self.root)
        health = [a for a in self.result(b).attempts.values() if a.kind == "health"][0]
        self.assertEqual(health.correlation, "health_app_only")

    # --- missing evidence -------------------------------------------------------
    def test_missing_all_server_evidence_is_inconclusive_not_failed(self):
        b = self.connectivity().finish().write(self.root, with_server=False)
        r = self.result(b)
        self.assertEqual(r.status, "inconclusive", r.reasons)

    def test_missing_one_record_when_run_has_others_is_failed(self):
        b = Builder("wifi_sequence")
        b.step("health", attempts=[{"kind": "health"}])
        b.step("message", attempts=[{"seq": 1}])
        b.step("message", attempts=[{"seq": 2, "server": 0}])
        b.step("message", attempts=[{"seq": 3}])
        b.finish().write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "failed", r.reasons)

    def test_not_acked_and_no_record_is_failed_not_blocked(self):
        b = self.connectivity(outcome="timeout", server=0).finish("failed", "timeout").write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "failed")
        self.assertFalse(any("block" in x for x in r.reasons))

    def test_missing_run_finished_is_running(self):
        b = Builder()
        b.step("health", attempts=[{"kind": "health"}])
        b.write(self.root)
        self.assertEqual(self.result(b).status, "running")

    def test_only_run_started_is_pending(self):
        b = Builder().write(self.root)
        self.assertEqual(self.result(b).status, "pending")

    def test_attempt_without_result_is_execution_error(self):
        b = Builder()
        sid = uid()
        b.ev("step_started", step_id=sid, step_index=1, step_kind="message", description="m")
        b.ev("attempt_started", step_id=sid, request_id=uid(), kind="message", seq=1, observed_transport="wifi")
        b.finish("execution_error", "app process ended").write(self.root)
        self.assertEqual(self.result(b).status, "execution_error")

    # --- duplicates -----------------------------------------------------------
    def test_duplicate_server_records_is_failed(self):
        b = self.connectivity(server=2).finish().write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "failed")
        self.assertTrue(any("duplicate" in x for x in r.reasons))

    def test_duplicate_app_request_id_is_execution_error(self):
        b = Builder()
        rid = uid()
        sid, _ = b.step("message", attempts=[{"rid": rid}])
        b.attempt(sid, rid=rid, seq=2)
        b.finish().write(self.root)
        self.assertEqual(self.result(b).status, "execution_error")

    def test_request_id_reused_across_runs_is_execution_error(self):
        rid = uid()
        b1 = Builder(); b1.step("message", attempts=[{"rid": rid}]); b1.finish().write(self.root)
        b2 = Builder(); b2.step("message", attempts=[{"rid": rid, "server": 0}]); b2.finish().write(self.root)
        _, runs = analysis.analyse(self.root)
        self.assertEqual(runs[b1.run_id].status, "execution_error")
        self.assertEqual(runs[b2.run_id].status, "execution_error")

    def test_duplicate_event_ids_across_imports_are_deduplicated(self):
        b = self.connectivity().finish()
        b.write(self.root)
        b.write(self.root, with_server=False)   # second identical import
        ev, runs = analysis.analyse(self.root)
        self.assertEqual(len(ev.app_events), len(b.events))
        self.assertEqual(runs[b.run_id].status, "passed")

    def test_conflicting_event_content_is_reported(self):
        b = self.connectivity().finish()
        b.write(self.root)
        altered = dict(b.events[0], wall_time="tampered")
        d = self.root / "evidence" / "device" / "zz_conflict"
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(json.dumps(altered) + "\n")
        ev, _ = analysis.analyse(self.root)
        self.assertTrue(any("conflicting" in i for i in ev.issues))

    def test_ack_mismatch_is_failed(self):
        b = self.connectivity(ack_id=uid()).finish().write(self.root)
        self.assertEqual(self.result(b).status, "failed")

    def test_record_with_other_run_id_is_failed(self):
        b = self.connectivity(server_run=uid())
        b.step("message", attempts=[{"seq": 2}])   # gives the run some server evidence
        b.finish().write(self.root)
        self.assertEqual(self.result(b).status, "failed")

    # --- cancellation / transport --------------------------------------------
    def test_cancelled_run_is_inconclusive_even_if_attempts_matched(self):
        b = self.connectivity().finish("inconclusive", "cancelled by user", cancelled=True).write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "inconclusive")
        self.assertTrue(any("cancelled" in x for x in r.reasons))

    def _cancel_run(self, extra_after_cancel=False, completed=True):
        b = Builder("wifi_sequence")
        b.step("health", attempts=[{"kind": "health"}])
        b.step("message", attempts=[{"seq": 1}])
        b.ev("cancel_requested", source="ui_cancel_button", run_in_progress=True)
        if extra_after_cancel:
            b.step("message", attempts=[{"seq": 2}])
        b.finish("inconclusive", "cancelled by user", cancelled=completed)
        return b

    def test_cancel_requested_then_completed_is_inconclusive_not_error(self):
        b = self._cancel_run().write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "inconclusive")
        cx = analysis.cancellation(r)
        self.assertTrue(cx["requested"] and cx["completed"])
        self.assertEqual(cx["started_after_request"], [])
        self.assertEqual(len(cx["finished_before_tap"]), 2)

    def test_request_started_after_cancel_tap_is_execution_error(self):
        b = self._cancel_run(extra_after_cancel=True).write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "execution_error")
        self.assertEqual(len(analysis.cancellation(r)["started_after_request"]), 1)

    def test_late_tap_after_last_check_is_benign_race_not_error(self):
        b = self._cancel_run(completed=False)          # tap recorded, nothing started after, no run_cancelled
        b.events[-1]["status"] = "passed"
        b.write(self.root)
        r = self.result(b)
        self.assertNotEqual(r.status, "execution_error")
        cx = analysis.cancellation(r)
        self.assertEqual(cx["validation"], "not_exercised")

    def test_runner_that_keeps_going_after_tap_fails_mechanism(self):
        b = self._cancel_run(extra_after_cancel=True, completed=False)
        b.write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "execution_error")
        self.assertEqual(analysis.cancellation(r)["validation"], "failed")

    def test_mechanism_passes_while_run_stays_inconclusive(self):
        b = self._cancel_run().write(self.root)
        r = self.result(b)
        cx = analysis.cancellation(r)
        self.assertEqual(cx["validation"], "passed")
        self.assertEqual(r.status, "inconclusive")          # the two assessments stay distinct
        self.assertGreaterEqual(cx["stop_latency_ms"], 0)

    def test_slow_stop_fails_mechanism_bound(self):
        b = self._cancel_run()
        cancelled = [e for e in b.events if e["event"] == "run_cancelled"][0]
        cancelled["mono_ns"] += (analysis.CANCEL_COMPLETION_BOUND_MS + 1000) * 1_000_000
        b.write(self.root)
        self.assertEqual(analysis.cancellation(self.result(b))["validation"], "failed")

    def test_timeline_is_ordered_by_phone_monotonic_clock(self):
        b = self._cancel_run()
        req = [e for e in b.events if e["event"] == "cancel_requested"][0]
        b.events.remove(req); b.events.append(req)          # written late (async), timestamped early
        b.write(self.root)
        app = [r for r in analysis.timeline(self.result(b)) if r["source"] == "app"]
        self.assertEqual([r["t_ms"] for r in app], sorted(r["t_ms"] for r in app))

    def test_ui_recreations_listed(self):
        b = self.connectivity()
        b.ev("activity_created", orientation="landscape", restored_state=True, run_in_progress=True)
        b.finish().write(self.root)
        rec = analysis.ui_recreations(self.result(b))
        self.assertEqual(rec[0]["orientation"], "landscape")

    def test_in_flight_request_at_tap_is_distinguished(self):
        b = Builder("wifi_sequence")
        sid = uid()
        b.ev("step_started", step_id=sid, step_index=1, step_kind="message", description="m")
        rid = uid()
        b.ev("attempt_started", step_id=sid, request_id=rid, kind="message", seq=1, observed_transport="wifi")
        b.ev("cancel_requested", source="ui_cancel_button", run_in_progress=True)
        b.ev("attempt_finished", step_id=sid, request_id=rid, outcome="cancelled", http_status=-1,
             ack_request_id=None, error="cancelled", elapsed_ms=3, observed_transport_after="wifi")
        b.ev("step_finished", step_id=sid, status="inconclusive", reason="cancelled", informational=False)
        b.finish("inconclusive", "cancelled by user", cancelled=True).write(self.root)
        r = self.result(b)
        cx = analysis.cancellation(r)
        self.assertEqual(len(cx["in_flight"]), 1)
        self.assertEqual(cx["started_after_request"], [])
        self.assertEqual(r.status, "inconclusive")

    def test_cancel_html_section_renders(self):
        import dashboard
        b = self._cancel_run().write(self.root)
        html_out = dashboard.run_body(self.result(b), "public", links=False)
        self.assertIn("Cancellation", html_out)
        self.assertIn("Started after tap", html_out)

    def test_lan_address_without_wifi_transport_is_inconclusive(self):
        b = self.connectivity(before="cellular", after="cellular").finish().write(self.root)
        self.assertEqual(self.result(b).status, "inconclusive")

    def test_wifi_record_via_loopback_is_inconclusive(self):
        b = self.connectivity(remote="127.0.0.1").finish().write(self.root)
        self.assertEqual(self.result(b).status, "inconclusive")

    def test_usb_record_must_be_loopback(self):
        b = Builder("usb_connectivity", "usb")
        b.step("health", attempts=[{"kind": "health"}])
        b.step("message", attempts=[{"remote": "127.0.0.1", "before": "none", "after": "none"}])
        b.finish().write(self.root)
        self.assertEqual(self.result(b).status, "passed")
        b2 = Builder("usb_connectivity", "usb")
        b2.step("message", attempts=[{"remote": "192.0.2.196"}])
        b2.finish().write(self.root)
        _, runs = analysis.analyse(self.root)
        self.assertEqual(runs[b2.run_id].status, "inconclusive")

    def test_transport_change_event_makes_sequence_inconclusive(self):
        b = Builder("wifi_sequence")
        b.step("health", attempts=[{"kind": "health"}])
        for i in (1, 2, 3):
            b.step("message", attempts=[{"seq": i}])
        b.ev("transport_changed", transport="cellular")
        b.finish().write(self.root)
        self.assertEqual(self.result(b).status, "inconclusive")

    def test_sequence_with_fewer_attempts_than_count_is_inconclusive(self):
        b = Builder("wifi_sequence")
        b.step("message", attempts=[{"seq": 1}])
        b.finish().write(self.root)
        self.assertEqual(self.result(b).status, "inconclusive")

    def test_app_execution_error_wins(self):
        b = self.connectivity().finish("execution_error", "evidence write failed").write(self.root)
        self.assertEqual(self.result(b).status, "execution_error")

    # --- restart / reconnect ----------------------------------------------------
    def test_restart_requires_two_processes(self):
        b = Builder("app_restart")
        b.step("baseline_message", attempts=[{}])
        b.step("wait_restart")
        b.step("post_restart_message", attempts=[{"seq": 2}])
        b.finish().write(self.root)
        self.assertEqual(self.result(b).status, "inconclusive")

    def test_restart_with_new_process_passes(self):
        b = Builder("app_restart")
        b.step("baseline_message", attempts=[{}])
        sid = uid()
        b.ev("step_started", step_id=sid, step_index=2, step_kind="wait_restart", description="w")
        b.proc = uid()
        b.ev("run_resumed", previous_process_id="x")
        b.ev("step_finished", step_id=sid, status="passed", reason="new process", informational=False)
        b.step_index = 2
        b.step("post_restart_message", attempts=[{"seq": 2}])
        b.finish().write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "passed", r.reasons)

    def reconnect(self, recovery):
        b = Builder("wifi_reconnect")
        b.step("baseline_message", attempts=[{}])
        b.step("wait_wifi_loss")
        b.step("outage_message", attempts=[{"seq": 2, "outcome": "connect_error", "server": 0,
                                            "before": "none", "after": "none"}], informational=True)
        b.step("wait_wifi_restore")
        b.step("settle")
        b.step("recovery_messages", status="passed", attempts=recovery)
        return b

    def test_reconnect_outage_failure_does_not_fail_run(self):
        b = self.reconnect([{"seq": 3}]).finish().write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "passed", r.reasons)

    def test_reconnect_recovery_counters_are_labelled_and_derived(self):
        # recovery attempt 2 succeeds; overall seqs are 3 (fail) and 4 (ok), written out of order
        b = self.reconnect([{"seq": 4}, {"seq": 3, "outcome": "timeout", "server": 0}])
        rec = [e for e in b.events if e["event"] == "attempt_started" and e.get("seq") in (3, 4)]
        s4 = [e for e in rec if e["seq"] == 4][0]; s3 = [e for e in rec if e["seq"] == 3][0]
        s4["mono_ns"], s3["mono_ns"] = s3["mono_ns"] + 1, s4["mono_ns"] - 1   # seq 3 recorded as started first
        b.finish().write(self.root)
        r = self.result(b)
        reason = [x for x in r.reasons if "recovery:" in x][0]
        self.assertIn("recovery attempt 2 of 2 recorded", reason)
        self.assertIn("overall message seq 4", reason)
        chk = [c for c in r.checks if c["check"].startswith("recovery numbering")][0]
        self.assertTrue(chk["ok"], chk)
        self.assertIn("recovery attempt 1 = overall seq 3", chk["detail"])

    def test_reconnect_recovery_numbering_inconsistency_is_flagged(self):
        b = self.reconnect([{"seq": 7}])          # app would have used seq 3 for the first recovery attempt
        b.finish().write(self.root)
        chk = [c for c in self.result(b).checks if c["check"].startswith("recovery numbering")][0]
        self.assertFalse(chk["ok"])

    def test_reconnect_without_recovery_is_failed_reliability(self):
        b = self.reconnect([{"seq": i, "outcome": "timeout", "server": 0} for i in (3, 4, 5)])
        b.finish("failed", "no recovery").write(self.root)
        r = self.result(b)
        self.assertEqual(r.status, "failed")
        self.assertTrue(any("reliability" in x for x in r.reasons))

    def test_reconnect_user_did_not_toggle_is_inconclusive(self):
        b = Builder("wifi_reconnect")
        b.step("baseline_message", attempts=[{}])
        b.step("wait_wifi_loss", status="inconclusive", reason="loss not observed")
        b.finish("inconclusive", "loss not observed").write(self.root)
        self.assertEqual(self.result(b).status, "inconclusive")

    # --- redaction / export -----------------------------------------------------
    def test_public_export_redacts_addresses_and_ids(self):
        b = self.connectivity().finish().write(self.root)
        run = self.result(b)
        pub = json.dumps(analysis.export_run(run, "public", "now"))
        priv = json.dumps(analysis.export_run(run, "private", "now"))
        for secret in ("192.0.2.6", "192.0.2.196", '"remote_port": 50000'):
            self.assertNotIn(secret, pub)
        self.assertIn("192.0.2.196", priv)
        self.assertIn(b.run_id, pub)   # random run IDs are kept

    def test_redact_keeps_clock_times_but_scrubs_ipv6_paths_and_secrets(self):
        r = analysis.redact({"a": "at 2026-11-03T18:53:35.290Z and 18:53:35",
                             "b": "via fe80::1c2d:3e4f%wlan0 and 2001:db8::1",
                             "c": "open /Users/someone/GitHub/x/evidence/a.jsonl failed",
                             "d": "device SER123ABC model TESTMODEL"}, ("SER123ABC", "TESTMODEL"))
        self.assertEqual(r["a"], "at 2026-11-03T18:53:35.290Z and 18:53:35")
        self.assertEqual(r["b"], "via [ip] and [ip]")
        self.assertEqual(r["c"], "open [path] failed")
        self.assertEqual(r["d"], "device [redacted] model [redacted]")

    def test_path_redaction_does_not_touch_relative_paths(self):
        r = analysis.redact({"a": "collector/data/messages.jsonl and /data/app/x/base.apk and /Users/x/y"})
        self.assertEqual(r["a"], "collector/data/messages.jsonl and [path] and [path]")

    def test_derived_report_is_new_dir_with_version_and_input_hashes(self):
        b = self.connectivity().finish().write(self.root)
        raw = sorted(self.root.glob("evidence/*/*/*.jsonl"))
        before = {f: f.read_bytes() for f in raw}
        d = analysis.write_derived_report(self.root, self.root / "evidence" / "derived")
        summ = json.loads((d / "summary.json").read_text())
        self.assertEqual(summ["analysis_version"], analysis.ANALYSIS_VERSION)
        self.assertEqual(len(summ["inputs"]), len(raw))
        self.assertTrue((d / "runs" / f"{b.run_id}.json").exists())
        self.assertEqual(before, {f: f.read_bytes() for f in raw})   # raw untouched

    def test_redact_scrubs_ips_in_free_text(self):
        self.assertEqual(analysis.redact({"error": "failed to connect to /192.0.2.6 (port 8765)"}),
                         {"error": "failed to connect to /[ip] (port 8765)"})

    def test_timeline_keeps_clock_domains_separate(self):
        b = self.connectivity().finish().write(self.root)
        rows = analysis.timeline(self.result(b))
        srv = [r for r in rows if r["source"] == "server"]
        app = [r for r in rows if r["source"] == "app"]
        self.assertEqual(srv[0]["t_ms"], 0.0)          # own origin
        self.assertEqual(app[0]["t_ms"], 0.0)
        self.assertNotEqual(srv[0]["clock"], app[0]["clock"])

    def test_legacy_records_load_read_only(self):
        legacy = self.root / "collector" / "data"
        legacy.mkdir(parents=True)
        f = legacy / "messages.jsonl"
        f.write_text(json.dumps({"request_id": uid(), "message": "old"}) + "\n")
        before = f.read_bytes()
        ev, _ = analysis.analyse(self.root)
        self.assertEqual(len(ev.legacy_records), 1)
        self.assertEqual(f.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
