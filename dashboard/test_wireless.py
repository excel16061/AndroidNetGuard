"""Tests for wireless observation analysis, dashboard rendering and the CSI adapter."""

import json
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis  # noqa: E402
import csi  # noqa: E402
import wireless  # noqa: E402
from test_analysis import Builder, uid  # noqa: E402


def tm(rssi, status="ok", fresh="new_value", link=433, freq=5180, transport="wifi"):
    return {"source": "ANDROID_WIFI_TELEMETRY", "telemetry_schema": "ang.wifi_telemetry/1",
            "rssi_dbm": {"value": rssi if status == "ok" else None, "unit": "dBm", "status": status},
            "frequency_mhz": {"value": freq if status == "ok" else None, "unit": "MHz", "status": "ok" if status == "ok" else status},
            "link_speed_mbps": {"value": link if status == "ok" else None, "unit": "Mbps", "status": "ok" if status == "ok" else status},
            "tx_link_speed_mbps": {"value": None, "status": "unknown"}, "rx_link_speed_mbps": {"value": None, "status": "unknown"},
            "wifi_standard": {"value": 5, "status": "ok", "label": "802.11ac"},
            "ssid": {"value": None, "status": "not_collected"}, "bssid": {"value": None, "status": "not_collected"},
            "freshness": fresh, "callback": {"status": "ok", "age_ms": 100}, "observed_transport": transport}


def observation(rssis, probes=(), label="stationary", events_after=None, duration_s=None):
    b = Builder("wifi_observation")
    # duration consistent with the number of samples (2 s sampling) unless a test overrides it
    b.events[0]["params"] = {"duration_s": duration_s if duration_s is not None else max(2, 2 * len(rssis)),
                             "sample_interval_ms": 2000, "probe_interval_ms": 5000, "session_label": label}
    sid = uid()
    b.ev("step_started", step_id=sid, step_index=1, step_kind="observe", description="o")
    for i, r in enumerate(rssis, 1):
        spec = r if isinstance(r, dict) else tm(r)
        b.ev("telemetry_sample", step_id=sid, seq=i, **spec)
        if events_after and i in events_after:
            b.ev(**events_after[i])
    for p in probes:
        b.attempt(sid, **p)
    b.ev("step_finished", step_id=sid, status="passed", reason="x", informational=True)
    return b


class WirelessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_of(self, b):
        _, runs = analysis.analyse(self.root)
        return runs[b.run_id]

    def test_stable_session_passes_without_alerts(self):
        b = observation([-50, -51, -50, -52, -51, -50, -51, -52]).finish("passed", "ok").write(self.root)
        r = self.run_of(b)
        self.assertEqual(r.status, "passed", r.reasons)
        w = analysis._wireless(r)
        self.assertEqual(w["detector"]["baseline"]["rssi_dbm"], -51)
        self.assertEqual(w["detector"]["alerts"], [])

    def test_degradation_alert_uses_fresh_samples_and_is_called_alert(self):
        b = observation([-50, -50, -51, -50, -50, -55, -63, -64, -66, -65]).finish().write(self.root)
        w = analysis._wireless(self.run_of(b))
        kinds = [a["kind"] for a in w["detector"]["alerts"]]
        self.assertIn("signal_degradation", kinds)
        self.assertNotIn("attack", json.dumps(w).lower())

    def test_repeated_values_are_excluded_from_baseline_and_detection(self):
        seq = [-50, -50, -50] + [tm(-80, fresh="repeated")] * 6
        b = observation(seq).finish().write(self.root)
        w = analysis._wireless(self.run_of(b))
        self.assertEqual(w["samples_repeated"], 6)
        self.assertEqual(w["samples_fresh"], 3)
        self.assertEqual([a for a in w["detector"]["alerts"] if a["kind"] == "signal_degradation"], [])

    def test_insufficient_fresh_samples_gives_no_baseline(self):
        b = observation([-50, -51]).finish().write(self.root)
        w = analysis._wireless(self.run_of(b))
        self.assertIsNone(w["detector"]["baseline"])
        self.assertEqual(w["detector"]["observations"][0]["kind"], "insufficient_data")

    def test_weak_signal_observation_thresholds(self):
        b = observation([-78, -79, -80, -79, -88, -89, -90]).finish().write(self.root)
        kinds = [o["kind"] for o in analysis._wireless(self.run_of(b))["detector"]["observations"]]
        self.assertIn("weak_signal", kinds)
        self.assertIn("very_weak_signal", kinds)

    def test_missing_values_are_reported_not_plotted_as_numbers(self):
        b = observation([-50, -51, tm(None, "unavailable"), tm(None, "permission_denied"), -50]).finish().write(self.root)
        w = analysis._wireless(self.run_of(b))
        self.assertEqual(w["rssi_status_counts"], {"ok": 3, "unavailable": 1, "permission_denied": 1})
        miss = [o for o in w["detector"]["observations"] if o["kind"] == "missing_rssi"][0]
        self.assertIn("permission_denied: 1", miss["detail"])
        svg = wireless.rssi_chart(w)
        self.assertIn("RSSI permission_denied", svg)
        self.assertEqual(svg.count("<polyline"), 2)          # line broken at the gap, never interpolated

    def test_no_rssi_at_all_is_inconclusive(self):
        b = observation([tm(None, "unavailable")] * 4).finish("inconclusive", "no valid RSSI").write(self.root)
        self.assertEqual(self.run_of(b).status, "inconclusive")

    def test_disruption_alerts_from_transport_capabilities_and_probe_streak(self):
        probes = [{"seq": 1}, {"seq": 2, "outcome": "connect_error", "server": 0},
                  {"seq": 3, "outcome": "timeout", "server": 0}, {"seq": 4}]
        b = observation([-50, -50, -50, -51], probes=probes,
                        events_after={2: {"event": "transport_changed", "transport": "none"},
                                      3: {"event": "wifi_capabilities_changed", "wifi_network_lost": True}})
        b.finish().write(self.root)
        w = analysis._wireless(self.run_of(b))
        details = " | ".join(a["detail"] for a in w["detector"]["alerts"] if a["kind"] == "connectivity_disruption")
        self.assertIn("transport changed to 'none'", details)
        self.assertIn("Wi-Fi network lost", details)
        self.assertIn("2 consecutive probes", details)

    def test_failure_attribution_distinguishes_evidence(self):
        a = wireless.attribute_failure
        base = {"ok": False, "server_records": 0, "http_status": -1, "transport_before": "wifi",
                "transport_after": "wifi", "follow_up_health": "health_ok"}
        self.assertIn("message_failed_while_collector_reachable", a(base, True))
        self.assertIn("collector_unreachable_on_wifi", a(dict(base, follow_up_health="connect_error"), True))
        self.assertIn("not distinguishable", a(dict(base, follow_up_health="connect_error"), True))
        self.assertIn("phone_not_on_wifi", a(dict(base, transport_before="none"), True))
        self.assertIn("server_responded_with_error (HTTP 500)", a(dict(base, http_status=500), True))
        self.assertIn("delivered_not_acknowledged", a(dict(base, server_records=1), True))
        self.assertIn("missing_server_evidence", a(base, False))
        self.assertEqual(a(dict(base, ok=True), True), "acknowledged")

    def test_unexpected_source_label_is_execution_error(self):
        bad = tm(-50); bad["source"] = "REAL_CSI"
        b = observation([-50, bad, -50]).finish().write(self.root)
        self.assertEqual(self.run_of(b).status, "execution_error")

    def test_cancelled_observation_is_inconclusive(self):
        b = observation([-50, -50, -50]).finish("inconclusive", "cancelled by user", cancelled=True).write(self.root)
        self.assertEqual(self.run_of(b).status, "inconclusive")

    def test_charts_have_empty_states(self):
        empty = {"series": {"samples": [], "probes": [], "health_checks": [], "transport_changes": [],
                            "capability_changes": []}, "duration_s": None, "detector": {"alerts": [], "baseline": None}}
        self.assertIn("No telemetry samples", wireless.rssi_chart(empty))
        self.assertIn("No requests", wireless.duration_chart(empty))

    def test_public_export_of_observation_is_sanitized(self):
        b = observation([-50, -51, -52], probes=[{"seq": 1}]).finish().write(self.root)
        doc = json.dumps(analysis.export_run(self.run_of(b), "public", "now", ("SERIALX",)))
        self.assertNotIn("192.0.2.196", doc)
        self.assertIn("ANDROID_WIFI_TELEMETRY", doc)
        self.assertIn('"wireless"', doc)

    def test_wireless_page_empty_state_and_csi_not_connected(self):
        import dashboard
        ev, runs = analysis.analyse(self.root)
        html_out = dashboard.render_wireless(ev, runs, self.root).decode()
        self.assertIn("No wireless observation sessions", html_out)
        self.assertIn("External CSI hardware not connected", html_out)


# ----------------------------------------------------------------------------- CSI adapter
def synthetic_frame(n_ant=1, n_sc=4, seq=7, freq=2437, rssi=-45, noise=-92, ppdu=0, flags=0, magic=csi.MAGIC,
                    iq=None, extra=b""):
    """SYNTHETIC_FIXTURE builder: hand-made bytes in the ADR-018 layout. Not real data."""
    hdr = struct.pack("<IBBHIIbbBB", magic, 3, n_ant, n_sc, freq, seq, rssi, noise, ppdu, flags)
    vals = iq if iq is not None else [((k % 7) - 3) for k in range(n_ant * n_sc * 2)]
    return hdr + struct.pack(f"<{len(vals)}b", *vals) + extra


def parse(data, kind=csi.SYNTHETIC_FIXTURE):
    return csi.parse_frame(data, kind, "fixture:test", 1, 0, "t")


class CsiTest(unittest.TestCase):
    def test_valid_fixture_parses_with_units_and_explicit_label(self):
        r = parse(synthetic_frame(n_ant=2, n_sc=3, flags=0b100001))
        self.assertEqual(r["source_kind"], "SYNTHETIC_FIXTURE")
        self.assertEqual(r["schema"], "ang.csi/1")
        self.assertEqual((r["n_antennas"], r["n_subcarriers"], r["frame_seq"]), (2, 3, 7))
        self.assertEqual(len(r["iq"]), 2)
        self.assertEqual(len(r["iq"][0]), 3)
        self.assertEqual(r["units"]["rssi_dbm"], "dBm")
        self.assertTrue(r["flags"]["bw40"] and r["flags"]["first_word_sanitized"])
        self.assertEqual(r["device_timestamp"]["status"], "not_provided_by_format")
        self.assertIn("5728b90949e60e3f908d996997d453147c1794e0", r["adapter"])

    def test_source_kind_must_be_explicit_and_known(self):
        for bad in (None, "", "ANDROID_WIFI_TELEMETRY", "csi"):
            with self.assertRaises(csi.FrameError):
                csi.parse_frame(synthetic_frame(), bad, "x", 1, 0, "t")

    def test_malformed_frames_rejected_with_specific_codes(self):
        cases = {
            "insufficient_data": [b"\x01\x00\x11\xc5" + b"\x00" * 10, synthetic_frame(n_sc=4)[:-1]],
            "invalid_magic": [synthetic_frame(magic=0xDEADBEEF)],
            "non_csi_packet": [synthetic_frame(magic=0xC5110004)],
            "invalid_antenna_count": [synthetic_frame(n_ant=0, n_sc=0), synthetic_frame(n_ant=5, n_sc=1)],
            "invalid_subcarrier_count": [synthetic_frame(n_ant=2, n_sc=513, iq=[])],
        }
        for code, frames in cases.items():
            for f in frames:
                with self.subTest(code=code), self.assertRaises(csi.FrameError) as cm:
                    parse(f)
                self.assertEqual(cm.exception.code, code)

    def test_warnings_for_trailing_bytes_unknown_frequency_and_range(self):
        r = parse(synthetic_frame(freq=0, rssi=5, extra=b"\x00\x00"))
        self.assertIn("trailing_bytes:2", r["validation_warnings"])
        self.assertIn("frequency_unknown", r["validation_warnings"])
        self.assertIn("rssi_out_of_range", r["validation_warnings"])
        self.assertIsNone(r["frequency_mhz"])

    def test_sequence_gap_and_repeat_tracked(self):
        t = csi.SeqTracker()
        self.assertEqual(t.check(parse(synthetic_frame(seq=10))), [])
        self.assertEqual(t.check(parse(synthetic_frame(seq=11))), [])
        self.assertEqual(t.check(parse(synthetic_frame(seq=14))), ["seq_gap:2"])
        self.assertEqual(t.check(parse(synthetic_frame(seq=14))), ["seq_repeat"])
        self.assertEqual(t.check(parse(synthetic_frame(seq=0xFFFFFFFF))) and [], [])
        self.assertEqual(t.check(parse(synthetic_frame(seq=0))), [])     # u32 wrap is continuous

    def test_status_counts_only_real_frames_and_never_fixtures(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(csi.status(Path(d))["real_frames"], 0)
            sd = Path(d) / "evidence" / "csi" / "s1"
            sd.mkdir(parents=True)
            fx = parse(synthetic_frame())
            (sd / "frames.jsonl").write_text(json.dumps(fx) + "\n" + json.dumps({"record": "rejected"}) + "\n")
            st = csi.status(Path(d))
            self.assertEqual(st["real_frames"], 0)          # a fixture is never counted as real
            self.assertEqual(st["non_real_records"], 1)
            self.assertEqual(st["rejected"], 1)

    def test_receiver_refuses_wildcard_and_records_labelled_frames(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                csi.receive("0.0.0.0", 0, 5, Path(d))
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]; s.close()
            out = {}
            t = threading.Thread(target=lambda: out.setdefault("d", csi.receive(
                "127.0.0.1", port, 10, Path(d), source_kind=csi.SYNTHETIC_FIXTURE, stop_after=3)))
            t.start(); time.sleep(0.3)
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for f in (synthetic_frame(seq=1), b"garbage", synthetic_frame(seq=3)):
                tx.sendto(f, ("127.0.0.1", port)); time.sleep(0.05)
            tx.close(); t.join(5)
            recs = [json.loads(l) for l in (out["d"] / "frames.jsonl").read_text().splitlines()]
            self.assertEqual([r.get("record") for r in recs], ["frame", "rejected", "frame"])
            self.assertTrue(all(r["source_kind"] == "SYNTHETIC_FIXTURE" for r in recs))
            self.assertIn("seq_gap:1", recs[2]["validation_warnings"])
            self.assertEqual(csi.status(Path(d))["real_frames"], 0)   # receiver dir is under d/, not evidence/


if __name__ == "__main__":
    unittest.main()


class ValidationWordingTest(unittest.TestCase):
    def test_validated_loss_is_internet_validation_not_disruption_of_lan(self):
        series = {"samples": [], "probes": [{"t_s": 5.0, "ok": True}], "health_checks": [],
                  "transport_changes": [], "capability_changes": [{"t_s": 1.0, "validated": False, "lost": False,
                                                                   "transports": "wifi", "signal_strength_dbm": -46}]}
        a = wireless.detect(series)["alerts"][0]
        self.assertEqual(a["kind"], "internet_validation_lost")
        self.assertIn("not a LAN reachability signal", a["detail"])
        self.assertIn("still acknowledged", a["detail"])


class WifiLostAttributionTest(unittest.TestCase):
    def test_failures_inside_telemetry_lost_interval_are_phone_not_on_wifi(self):
        series = {"capability_changes": [{"t_s": 39.2, "lost": True, "validated": None, "transports": None},
                                         {"t_s": 90.2, "lost": False, "validated": False, "transports": "wifi"}]}
        lost = wireless.wifi_lost_intervals(series)
        self.assertEqual(lost, [[39.2, 90.2]])
        p = {"ok": False, "server_records": 0, "http_status": -1, "transport_before": "wifi",
             "transport_after": "wifi", "follow_up_health": "connect_error", "t_s": 50.0}
        a = wireless.attribute_failure(p, True, lost)
        self.assertIn("phone_not_on_wifi", a)
        self.assertIn("0.4.0 defect", a)
        self.assertIn("collector_unreachable_on_wifi",
                      wireless.attribute_failure(dict(p, t_s=95.0), True, lost))   # outside the interval

    def test_open_ended_lost_interval(self):
        self.assertEqual(wireless.wifi_lost_intervals(
            {"capability_changes": [{"t_s": 5.0, "lost": True, "transports": None}]}), [[5.0, None]])


class CancelledObservationTest(unittest.TestCase):
    """Mirrors device run 50edfe31 (0.4.1): cancel during the observe step leaves that step open."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, with_cancel_request=True):
        b = Builder("wifi_observation")
        b.events[0]["params"] = {"duration_s": 600, "sample_interval_ms": 2000, "probe_interval_ms": 5000,
                                 "session_label": "cancel_check"}
        b.step("health", attempts=[{"kind": "health"}], informational=True)
        sid = uid()
        b.ev("step_started", step_id=sid, step_index=2, step_kind="observe", description="o")
        for i in (1, 2, 3):
            b.ev("telemetry_sample", step_id=sid, seq=i, **tm(-50))
        b.attempt(sid, seq=1)
        if with_cancel_request:
            b.ev("cancel_requested", source="ui_cancel_button", run_in_progress=True)
        b.ev("run_cancelled", meaning="cancellation_completed")
        b.ev("run_finished", status="inconclusive", status_scope="app_provisional", reason="cancelled by user")
        return b.write(self.root)

    def test_open_step_after_recorded_cancel_is_interrupted_not_execution_error(self):
        b = self.build()
        _, runs = analysis.analyse(self.root)
        r = runs[b.run_id]
        self.assertEqual(r.status, "inconclusive", r.reasons)
        self.assertTrue(any("interrupted by user cancellation" in x for x in r.reasons))
        self.assertTrue(any("stopped early" in x for x in r.reasons))
        self.assertFalse(any("observation completed" in x for x in r.reasons))
        cx = analysis.cancellation(r)
        self.assertEqual(cx["validation"], "passed")          # mechanism verdict stays separate

    def test_open_step_without_a_recorded_tap_is_still_execution_error(self):
        b = self.build(with_cancel_request=False)
        _, runs = analysis.analyse(self.root)
        self.assertEqual(runs[b.run_id].status, "execution_error")


class StatusSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_observation_with_low_sampling_coverage_is_inconclusive(self):
        b = observation([-50, -51, -50, -52], duration_s=60).finish().write(self.root)   # 4 of 30 slots
        _, runs = analysis.analyse(self.root)
        r = runs[b.run_id]
        self.assertEqual(r.status, "inconclusive")
        self.assertTrue(any("sampling coverage too low" in x for x in r.reasons))

    def test_observation_with_failed_probes_still_passes(self):
        probes = [{"seq": 1}, {"seq": 2, "outcome": "connect_error", "server": 0}]
        b = observation([-50, -51, -50, -52], probes=probes).finish().write(self.root)
        _, runs = analysis.analyse(self.root)
        self.assertEqual(runs[b.run_id].status, "passed")

    def test_status_meaning_is_test_specific(self):
        b = observation([-50, -51, -50]).finish().write(self.root)
        c = Builder(); c.step("message", attempts=[{}]); c.finish().write(self.root)
        _, runs = analysis.analyse(self.root)
        so, sc = analysis.run_summary(runs[b.run_id]), analysis.run_summary(runs[c.run_id])
        self.assertEqual(so["status_kind"], "observation")
        self.assertIn("valid OBSERVATIONS", so["status_meaning"])
        self.assertEqual(sc["status_kind"], "connectivity")
        self.assertIn("exactly one stored record", sc["status_meaning"])
        exp = analysis.export_run(runs[b.run_id], "public", "now")
        self.assertIn("cancellation_validation_definitions", exp)
        self.assertIn("observation", exp["status_definitions_by_kind"])

    def test_later_incomplete_run_does_not_erase_verified_feature(self):
        good = Builder("usb_connectivity", "usb")
        good.step("message", attempts=[{"remote": "127.0.0.1"}]); good.finish().write(self.root)
        late = Builder("usb_connectivity", "usb")
        late.events[0]["wall_time"] = "2099-01-01T00:00:00Z"            # strictly later
        late.step("health", status="failed", attempts=[{"kind": "health", "outcome": "connect_error"}])
        late.finish("failed", "loopback refused").write(self.root)
        _, runs = analysis.analyse(self.root)
        f = analysis.feature_status(runs)["usb_connectivity"]
        self.assertTrue(f["verified"])
        self.assertEqual(f["verified_by"]["run_id"], good.run_id)
        self.assertEqual(f["latest"]["run_id"], late.run_id)
        self.assertNotEqual(f["latest"]["status"], "passed")


class CompletenessVsFreshnessTest(unittest.TestCase):
    def test_completeness_and_freshness_are_separate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seq = [-50, tm(-50, fresh="repeated"), tm(-50, fresh="repeated"), -51]
            b = observation(seq, duration_s=16).finish().write(root)      # 8 expected slots, 4 recorded
            _, runs = analysis.analyse(root)
            w = analysis._wireless(runs[b.run_id])
            self.assertEqual(w["sample_completeness"], {"recorded": 4, "expected": 8, "ratio": 0.5,
                                                        "note": w["sample_completeness"]["note"]})
            self.assertEqual((w["telemetry_freshness"]["fresh"], w["telemetry_freshness"]["repeated"]), (2, 2))
            html_tiles = wireless.tiles(w)
            self.assertIn("Sample completeness", html_tiles)
            self.assertIn("Reported value changes", html_tiles)
            self.assertIn("measurement age unknown", html_tiles)
            self.assertEqual(runs[b.run_id].status, "passed")   # 50 % coverage meets the rule; repeats don't count against it
