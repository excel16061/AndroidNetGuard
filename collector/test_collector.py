"""Tests for collector.py. Run: python3 -I -m unittest discover -s collector -v"""

import http.client
import json
import socket
import sys
import tempfile
import time
import threading
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector  # noqa: E402

PATH = "/v1/test-message"


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "sub" / "messages.jsonl"
        self.srv = collector.CollectorServer(("127.0.0.1", 0), self.out, quiet=True)
        self.port = self.srv.server_port
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    # helpers
    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, json.loads(data) if data else None
        finally:
            conn.close()

    def post(self, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.request("POST", PATH, body, {"Content-Type": ctype})

    def records(self):
        if not self.out.exists():
            return []
        return [json.loads(l) for l in self.out.read_text(encoding="utf-8").splitlines()]

    def good(self, **kw):
        p = {"request_id": str(uuid.uuid4()), "message": "hello from test", "test_label": "unit"}
        p.update(kw)
        return p

    # tests
    def test_health(self):
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["server_time"].endswith("Z"))

    def test_unknown_path(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("POST", "/nope", b"{}", {"Content-Type": "application/json"})[0], 404)

    def test_accepts_and_echoes_request_id_and_persists(self):
        p = self.good()
        status, body = self.post(p)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["request_id"], p["request_id"])
        recs = self.records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["request_id"], p["request_id"])
        self.assertEqual(recs[0]["message"], p["message"])
        self.assertEqual(recs[0]["server_time"], body["server_time"])
        self.assertEqual(recs[0]["remote_addr"], "127.0.0.1")
        self.assertIsInstance(recs[0]["remote_port"], int)
        self.assertEqual(recs[0]["server_addr"], "127.0.0.1")
        self.assertEqual(recs[0]["server_port"], self.port)

    def test_appends_multiple(self):
        ids = []
        for _ in range(3):
            p = self.good()
            ids.append(p["request_id"])
            self.assertEqual(self.post(p)[0], 200)
        self.assertEqual([r["request_id"] for r in self.records()], ids)

    def test_concurrent_writes_are_line_intact(self):
        ids, errors = [], []

        def worker():
            p = self.good()
            ids.append(p["request_id"])
            try:
                if self.post(p)[0] != 200:
                    errors.append(p)
            except OSError as e:          # a reset connection must fail the test, not vanish
                errors.append((p, repr(e)))

        ts = [threading.Thread(target=worker) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertFalse(errors)
        self.assertCountEqual([r["request_id"] for r in self.records()], ids)

    def test_wrong_content_type(self):
        self.assertEqual(self.post(self.good(), ctype="text/plain")[0], 415)
        self.assertEqual(self.records(), [])

    def test_invalid_json(self):
        self.assertEqual(self.post(b"{not json")[0], 400)
        self.assertEqual(self.post(b"\xff\xfe")[0], 400)

    def test_validation_failures(self):
        cases = [
            [],                                                   # not an object
            {"message": "x"},                                     # missing id
            self.good(request_id="not-a-uuid"),
            self.good(request_id=str(uuid.uuid1())),              # not v4
            self.good(request_id=str(uuid.uuid4()).upper()),      # non-canonical
            self.good(request_id=123),
            self.good(message=""),
            self.good(message="   "),
            self.good(message="x" * (collector.MAX_MESSAGE_CHARS + 1)),
            self.good(message="bad\x00char"),
            self.good(message=5),
            self.good(extra="field"),
            self.good(test_label="x" * 65),
            self.good(test_label="<script>"),
        ]
        for c in cases:
            with self.subTest(c=c):
                self.assertEqual(self.post(c)[0], 422)
        self.assertEqual(self.records(), [])

    def test_oversized_body_rejected_without_persisting(self):
        big = json.dumps(self.good(message="x" * (collector.MAX_BODY_BYTES + 10))).encode()
        status, body = self.post(big)
        self.assertEqual(status, 413)
        self.assertEqual(self.records(), [])

    def test_missing_content_length(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall(b"POST /v1/test-message HTTP/1.1\r\nHost: x\r\n"
                      b"Content-Type: application/json\r\nConnection: close\r\n\r\n")
            resp = s.recv(4096).decode()
        finally:
            s.close()
        self.assertIn(" 411 ", resp.splitlines()[0])

    def test_chunked_rejected(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall(b"POST /v1/test-message HTTP/1.1\r\nHost: x\r\n"
                      b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n"
                      b"Connection: close\r\n\r\n0\r\n\r\n")
            resp = s.recv(4096).decode()
        finally:
            s.close()
        self.assertIn(" 411 ", resp.splitlines()[0])

    def test_transport_field(self):
        self.assertEqual(self.post(self.good(transport="wifi"))[0], 200)
        self.assertEqual(self.records()[0]["transport"], "wifi")
        self.assertEqual(self.post(self.good(transport="wifi;rm -rf"))[0], 422)

    def test_message_at_limit_and_unicode_ok(self):
        self.assertEqual(self.post(self.good(message="é" * collector.MAX_MESSAGE_CHARS))[0], 200)


if __name__ == "__main__":
    unittest.main()


class FieldAndSessionTest(unittest.TestCase):
    def test_run_fields_validated(self):
        base = {"request_id": str(uuid.uuid4()), "message": "m"}
        ok = dict(base, run_id=str(uuid.uuid4()), step_id=str(uuid.uuid4()), seq=3,
                  test_id="wifi_sequence", test_mode="wifi")
        self.assertEqual(collector.validate_message(ok)["seq"], 3)
        for bad in (dict(base, run_id="x"), dict(base, seq=-1), dict(base, seq=True),
                    dict(base, seq=collector.MAX_SEQ + 1), dict(base, test_id="a/b")):
            with self.subTest(bad=bad), self.assertRaises(collector.ValidationError):
                collector.validate_message(bad)

    def test_wildcard_bind_refused(self):
        import argparse
        for h in ("0.0.0.0", "::", "example.com"):
            with self.subTest(h=h), self.assertRaises(argparse.ArgumentTypeError):
                collector.check_bind_host(h)
        self.assertEqual(collector.check_bind_host("192.0.2.6"), "192.0.2.6")

    def test_new_session_dirs_are_distinct_and_have_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            w1, d1 = collector.new_session(Path(d), ["127.0.0.1"], 8765)
            w2, d2 = collector.new_session(Path(d), ["127.0.0.1"], 8765)
            self.assertNotEqual(d1, d2)
            meta = json.loads((d1 / "session.json").read_text())
            self.assertEqual(meta["session_id"], w1.session_id)
            self.assertIn("started_mono_ns", meta)

    def test_session_record_has_session_and_mono(self):
        with tempfile.TemporaryDirectory() as d:
            w, sd = collector.new_session(Path(d), ["127.0.0.1"], 0)
            srv = collector.CollectorServer(("127.0.0.1", 0), w)
            t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
                rid = str(uuid.uuid4())
                conn.request("POST", PATH, json.dumps({"request_id": rid, "message": "m"}),
                             {"Content-Type": "application/json"})
                with conn.getresponse() as response:
                    response.read()
                    self.assertEqual(response.status, 200)
                conn.close()
                rec = json.loads((sd / "messages.jsonl").read_text())
                self.assertEqual(rec["session_id"], w.session_id)
                self.assertIsInstance(rec["server_mono_ns"], int)
                self.assertIn("POST /v1/test-message", (sd / "access.log").read_text())
            finally:
                srv.shutdown(); srv.server_close()


class HardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w, self.d = collector.new_session(Path(self.tmp.name), ["127.0.0.1"], 0)

    def tearDown(self):
        self.tmp.cleanup()

    def serve(self, **kw):
        srv = collector.CollectorServer(("127.0.0.1", 0), self.w, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv

    def test_access_log_escapes_control_characters(self):
        srv = self.serve()
        s = socket.create_connection(("127.0.0.1", srv.server_port), timeout=5)
        s.sendall(b"GET /x\x1b[31m\rFAKE HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        s.recv(4096); s.close()
        time.sleep(0.2)
        log = (self.d / "access.log").read_text()
        self.assertNotIn("\x1b", log)
        self.assertIn("\\x1b", log)
        self.assertNotIn("\r", log)
        for line in log.strip().splitlines():   # every line is a genuine, timestamped log entry
            self.assertRegex(line, r"^\d{4}-\d\d-\d\dT[0-9:.]+Z 127\.0\.0\.1:\d+ -> ")
        self.assertNotIn("\nFAKE", log)

    def test_non_ascii_digit_content_length_rejected(self):
        srv = self.serve()
        s = socket.create_connection(("127.0.0.1", srv.server_port), timeout=5)
        s.sendall("POST /v1/test-message HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  "Content-Length: \u00b2\r\nConnection: close\r\n\r\n".encode("utf-8"))
        resp = s.recv(4096).decode(errors="replace"); s.close()
        self.assertIn(" 411 ", resp.splitlines()[0])

    def test_connection_cap_closes_excess_connections(self):
        srv = self.serve(max_connections=2)
        held = [socket.create_connection(("127.0.0.1", srv.server_port), timeout=5) for _ in range(2)]
        time.sleep(0.3)   # both accepted and parked in handler threads (no request sent)
        extra = socket.create_connection(("127.0.0.1", srv.server_port), timeout=5)
        extra.settimeout(3)
        try:
            data = extra.recv(1)          # server closes it immediately -> b""
        except ConnectionResetError:
            data = b""
        self.assertEqual(data, b"")
        extra.close()
        for h in held:
            h.close()
        time.sleep(0.3)
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
        conn.request("GET", "/health")
        with conn.getresponse() as response:
            response.read()
            self.assertEqual(response.status, 200)   # slots released
        conn.close()

    def test_session_storage_cap_returns_507_and_keeps_file(self):
        old = collector.MAX_SESSION_BYTES
        collector.MAX_SESSION_BYTES = 600
        self.addCleanup(setattr, collector, "MAX_SESSION_BYTES", old)
        srv = self.serve()
        codes = []
        for _ in range(4):
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
            conn.request("POST", PATH, json.dumps({"request_id": str(uuid.uuid4()), "message": "m"}),
                         {"Content-Type": "application/json"})
            with conn.getresponse() as response:
                response.read()
                codes.append(response.status)
            conn.close()
        self.assertIn(507, codes)
        self.assertLessEqual((self.d / "messages.jsonl").stat().st_size, 600)
        for line in (self.d / "messages.jsonl").read_text().splitlines():
            json.loads(line)   # every stored line intact
