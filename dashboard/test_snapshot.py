"""Tests for the sanitized saved-result snapshot (dashboard/snapshot.py)."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot  # noqa: E402
from test_analysis import Builder  # noqa: E402
from test_wireless import observation  # noqa: E402


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "proj"
        self.out = Path(self.tmp.name) / "reports"
        b = Builder()
        b.step("health", attempts=[{"kind": "health"}])
        b.step("message", attempts=[{}])
        b.finish("passed", "ok: device SERIALQ7 at 192.0.2.6 via /Users/someone/x and fe80::1 mail me@example.org")
        b.write(self.root)
        imp = sorted((self.root / "evidence" / "device").iterdir())[0]
        (imp / "import.json").write_text(json.dumps({"import_id": imp.name, "serial": "SERIALQ7", "model": "MODELZZ9"}))
        self.obs = observation([-50, -51, -50, -52], probes=[{"seq": 1}]).finish().write(self.root)
        self.b = b

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_is_sanitized_labelled_and_self_contained(self):
        out = snapshot.build(self.root, self.out)
        all_text = "".join(p.read_text(errors="replace") for p in out.rglob("*") if p.is_file())
        for secret in ("SERIALQ7", "MODELZZ9", "192.0.2.6", "192.0.2.196", "/Users/someone", "fe80::1",
                       "me@example.org", str(self.root)):
            self.assertNotIn(secret, all_text, secret)
        for page in ("index.html", "wireless.html", f"runs/{self.b.run_id}.html", f"runs/{self.obs.run_id}.html"):
            t = (out / page).read_text()
            self.assertIn("SAVED RESULTS SNAPSHOT", t)
            self.assertNotIn("<script", t.lower())
            self.assertNotIn("<style>", t)                      # stylesheet is a local asset
        self.assertEqual(snapshot.check_static(out), [])         # relative links only, no live/local addresses
        self.assertNotIn("127.0.0.1:8770", all_text)
        self.assertNotIn("localhost", all_text)
        self.assertNotIn("Import from connected phone", (out / "index.html").read_text())
        self.assertIn("RSSI (ANDROID_WIFI_TELEMETRY", (out / f"runs/{self.obs.run_id}.html").read_text())

    def test_manifest_and_checksums(self):
        out = snapshot.build(self.root, self.out)
        m = json.loads((out / "manifest.json").read_text())
        for k in ("exported_at", "analysis_version", "wireless_analysis_version", "source", "input_fingerprints", "files"):
            self.assertIn(k, m)
        self.assertEqual(m["label"], "SAVED RESULTS (not live)")
        self.assertEqual(m["run_count"], 2)
        for line in (out / "SHA256SUMS").read_text().splitlines():
            digest, rel = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((out / rel).read_bytes()).hexdigest(), digest, rel)
        self.assertIn("manifest.json", (out / "SHA256SUMS").read_text())
        data = json.loads((out / "data.json").read_text())
        self.assertEqual(data["kind"], "saved_result_snapshot")

    def test_raw_evidence_untouched_and_new_dir_each_time(self):
        raw = {p: p.read_bytes() for p in self.root.rglob("*.jsonl")}
        a = snapshot.build(self.root, self.out, stamp="20260101T000000Z")
        with self.assertRaises(FileExistsError):
            snapshot.build(self.root, self.out, stamp="20260101T000000Z")   # never overwrites a snapshot
        self.assertEqual(raw, {p: p.read_bytes() for p in self.root.rglob("*.jsonl")})
        self.assertTrue(a.exists())

    def test_leak_scan_aborts_and_removes_partial_output(self):
        # The HTML scrub alone is not the only defence (run pages are redacted upstream too), so
        # simulate a failure of ALL redaction and require the independent leak scan to stop it.
        with mock.patch.object(snapshot.analysis, "redact", lambda obj, secrets=(): obj):
            with self.assertRaises(snapshot.LeakError):
                snapshot.build(self.root, self.out, stamp="20260102T000000Z")
        self.assertFalse((self.out / "20260102T000000Z_snapshot").exists())


if __name__ == "__main__":
    unittest.main()


class StaticCheckTest(unittest.TestCase):
    def test_static_check_flags_live_dashboard_references(self):
        with tempfile.TemporaryDirectory() as d:
            o = Path(d)
            (o / "index.html").write_text('<p>open <a href="runs.html">r</a> or http://127.0.0.1:8770/</p>')
            (o / "runs.html").write_text("ok")
            probs = snapshot.check_static(o)
            self.assertTrue(any("live/local address" in x for x in probs), probs)
