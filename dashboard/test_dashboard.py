"""HTTP-level tests for dashboard.py using a temp evidence root and a fake adb."""

import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dashboard  # noqa: E402
from test_analysis import Builder  # noqa: E402

FAKE_ADB = r"""#!/bin/sh
# Fake adb for tests. Emits the file named by $FAKE_EVENTS for run-as cat.
case "$*" in
  "devices") printf 'List of devices attached\nSER123\tdevice\n\n' ;;
  *"exec-out run-as com.androidnetguard.app cat files/evidence/events.jsonl"*) cat "$FAKE_EVENTS" ;;
  *"getprop ro.product.model"*) echo TESTMODEL ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.b = Builder()
        self.b.step("health", attempts=[{"kind": "health"}])
        self.b.step("message", attempts=[{}])
        self.b.finish().write(self.root)
        self.srv = dashboard.Dashboard(0, self.root, None)
        self.port = self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_rejects_foreign_host_header(self):
        self.assertEqual(self.req("GET", "/", host="evil.example:80")[0], 403)
        self.assertEqual(self.req("GET", "/", host="192.0.2.6:8770")[0], 403)

    def test_index_shows_run_catalog_and_not_implemented(self):
        status, h, body = self.req("GET", "/")
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertIn(self.b.run_id, text)
        self.assertIn("Security-policy tests", text)
        self.assertIn("Not implemented", text)
        for s in ("pending", "running", "passed", "failed", "inconclusive", "execution_error"):
            self.assertIn(f"s-{s}", text)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])

    def test_run_detail_and_unknown_run(self):
        self.assertEqual(self.req("GET", f"/runs/{self.b.run_id}")[0], 200)
        self.assertEqual(self.req("GET", "/runs/00000000-0000-4000-8000-000000000000")[0], 404)
        self.assertEqual(self.req("GET", "/runs/../../etc/passwd")[0], 404)

    def test_public_export_default_is_redacted_download(self):
        status, h, body = self.req("GET", f"/runs/{self.b.run_id}/export.json")
        self.assertEqual(status, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        doc = json.loads(body)
        self.assertEqual(doc["scope"], "public")
        self.assertNotIn("192.0.2.196", body.decode())
        self.assertEqual(doc["derived"]["final_status"], "passed")
        self.assertIn("raw", doc)
        self.assertIn("derived", doc)
        priv = self.req("GET", f"/runs/{self.b.run_id}/export.json?scope=private")[2].decode()
        self.assertIn("192.0.2.196", priv)
        self.assertEqual(self.req("GET", f"/runs/{self.b.run_id}/export.json?scope=x")[0], 400)

    def test_html_report_public_redacted(self):
        status, _, body = self.req("GET", f"/runs/{self.b.run_id}/report.html")
        self.assertEqual(status, 200)
        self.assertNotIn("192.0.2.196", body.decode())
        self.assertIn("redacted", body.decode())

    def test_import_requires_token_and_same_origin(self):
        self.assertEqual(self.req("POST", "/import", b"token=wrong",
                                  {"Content-Type": "application/x-www-form-urlencoded"})[0], 403)
        self.assertEqual(self.req("POST", "/import", f"token={self.srv.token}".encode(),
                                  {"Origin": "http://evil.example"})[0], 403)

    def _fake_adb(self, events_text):
        adb = self.root / "fakeadb"
        adb.write_text(FAKE_ADB)
        adb.chmod(adb.stat().st_mode | stat.S_IEXEC)
        ev = self.root / "device_events.jsonl"
        ev.write_text(events_text)
        os.environ["FAKE_EVENTS"] = str(ev)
        return str(adb)

    def test_import_creates_new_dir_and_skips_identical(self):
        b2 = Builder(); b2.step("message", attempts=[{}]); b2.finish()
        adb = self._fake_adb("".join(json.dumps(e) + "\n" for e in b2.events))
        r = dashboard.import_from_device(self.root, adb=adb)
        self.assertEqual(r["status"], "imported")
        meta = json.loads((self.root / "evidence" / "device" / r["detail"] / "import.json").read_text())
        self.assertEqual(meta["serial"], "SER123")
        self.assertEqual(meta["events"], len(b2.events))
        self.assertEqual(dashboard.import_from_device(self.root, adb=adb)["status"], "unchanged")

    def test_import_preserves_partially_damaged_evidence_and_records_it(self):
        b2 = Builder(); b2.step("message", attempts=[{}]); b2.finish()
        text = "".join(json.dumps(e) + "\n" for e in b2.events) + '{"event":"attempt_sta'   # crash mid-write
        adb = self._fake_adb(text)
        r = dashboard.import_from_device(self.root, adb=adb)
        meta = json.loads((self.root / "evidence" / "device" / r["detail"] / "import.json").read_text())
        self.assertEqual(meta["invalid_lines"], [len(b2.events) + 1])
        self.assertFalse(meta["ends_with_newline"])
        raw = (self.root / "evidence" / "device" / r["detail"] / "events.jsonl").read_text()
        self.assertEqual(raw, text)                      # byte-for-byte preserved
        import analysis
        ev, runs = analysis.analyse(self.root)
        self.assertTrue(any("not valid JSON" in i for i in ev.issues))
        self.assertIn(b2.run_id, runs)

    def test_import_rejects_output_that_is_not_evidence(self):
        adb = self._fake_adb("run-as: package not debuggable\nsomething else\n")
        before = sorted((self.root / "evidence" / "device").iterdir())
        with self.assertRaises(dashboard.ImportError_):
            dashboard.import_from_device(self.root, adb=adb)
        self.assertEqual(before, sorted((self.root / "evidence" / "device").iterdir()))

    def test_public_export_scrubs_serial_and_token(self):
        meta_dir = sorted((self.root / "evidence" / "device").iterdir())[0]
        (meta_dir / "import.json").write_text(json.dumps({"serial": "SERIALX99", "model": "MODELZ"}))
        ev_file = meta_dir / "events.jsonl"
        lines = ev_file.read_text().splitlines()
        e = json.loads(lines[-1]); e["reason"] = f"device SERIALX99 token {self.srv.token}"
        lines[-1] = json.dumps(e)
        ev_file.write_text("\n".join(lines) + "\n")
        body = self.req("GET", f"/runs/{self.b.run_id}/export.json")[2].decode()
        self.assertNotIn("SERIALX99", body)
        self.assertNotIn(self.srv.token, body)
        rep = self.req("GET", f"/runs/{self.b.run_id}/report.html")[2].decode()
        self.assertNotIn("SERIALX99", rep)

    def test_html_escapes_untrusted_evidence_text(self):
        meta_dir = sorted((self.root / "evidence" / "device").iterdir())[0]
        ev_file = meta_dir / "events.jsonl"
        lines = ev_file.read_text().splitlines()
        e = json.loads(lines[-1]); e["reason"] = "<script>alert(1)</script>"
        lines[-1] = json.dumps(e)
        ev_file.write_text("\n".join(lines) + "\n")
        body = self.req("GET", f"/runs/{self.b.run_id}")[2].decode()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_import_refuses_unknown_serial(self):
        adb = self._fake_adb("")
        with self.assertRaises(dashboard.ImportError_):
            dashboard.import_from_device(self.root, serial="OTHER", adb=adb)


if __name__ == "__main__":
    unittest.main()
