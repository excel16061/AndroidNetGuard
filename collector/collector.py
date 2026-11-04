#!/usr/bin/env python3
"""AndroidNetGuard test-message collector (Python standard library only).

This is the ONLY process that may face the LAN. It has no control or dashboard
endpoints: it only answers health checks and stores validated test messages.

Endpoints
  GET  /health            -> {"status": "ok", ...}
  POST /v1/test-message   -> validates a small JSON test message, appends it
                             to a JSONL file with server timestamps, and
                             acknowledges with the same request_id.

Storage modes
  --evidence-dir DIR (default: <project>/evidence/server)
        Each collector start creates a NEW session directory
        DIR/<UTC timestamp>_<session id>/ containing session.json,
        messages.jsonl and access.log. Existing files are never rewritten.
  --output FILE
        Legacy single-file mode used by the USB/Wi-Fi smoke tests.

Binding
  --host may be given several times (e.g. 127.0.0.1 for the adb-reverse USB
  path and the Mac's Wi-Fi address for the direct LAN path). Wildcard
  addresses (0.0.0.0, ::) are refused: bind to specific addresses only.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE = "androidnetguard-collector"
VERSION = "0.2.1"

MAX_BODY_BYTES = 4096
MAX_MESSAGE_CHARS = 256
MAX_SHORT_FIELD_CHARS = 64
MAX_SEQ = 10_000
MAX_CONCURRENT_CONNECTIONS = 32          # per listener; extra connections are closed
MAX_SESSION_BYTES = 50 * 1024 * 1024     # messages.jsonl cap per session

UUID_FIELDS = {"run_id", "step_id"}
SHORT_FIELDS = {"client_time", "app_version", "test_label", "transport", "test_id", "test_mode"}
INT_FIELDS = {"seq"}
REQUIRED_FIELDS = {"request_id", "message"}
OPTIONAL_FIELDS = UUID_FIELDS | SHORT_FIELDS | INT_FIELDS
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

_SHORT_FIELD_RE = re.compile(r"^[A-Za-z0-9 ._:+\-_]*$")


class ValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _printable(s: str) -> bool:
    return all(ch.isprintable() for ch in s)


def _uuid4(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ValidationError(f"{name} must be a UUID") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValidationError(f"{name} must be a canonical lowercase UUIDv4")
    return value


def validate_message(payload: object) -> dict:
    """Return a normalized record or raise ValidationError."""
    if not isinstance(payload, dict):
        raise ValidationError("body must be a JSON object")

    unknown = set(payload) - ALLOWED_FIELDS
    if unknown:
        raise ValidationError(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        raise ValidationError(f"missing field(s): {', '.join(sorted(missing))}")

    rid = _uuid4(payload["request_id"], "request_id")

    msg = payload["message"]
    if not isinstance(msg, str) or not msg.strip():
        raise ValidationError("message must be a non-empty string")
    if len(msg) > MAX_MESSAGE_CHARS:
        raise ValidationError(f"message exceeds {MAX_MESSAGE_CHARS} characters")
    if not _printable(msg):
        raise ValidationError("message contains control characters")

    record = {"request_id": rid, "message": msg}
    for key in sorted(OPTIONAL_FIELDS & set(payload)):
        val = payload[key]
        if key in UUID_FIELDS:
            record[key] = _uuid4(val, key)
        elif key in INT_FIELDS:
            if not isinstance(val, int) or isinstance(val, bool) or not 0 <= val <= MAX_SEQ:
                raise ValidationError(f"{key} must be an integer 0..{MAX_SEQ}")
            record[key] = val
        else:
            if not isinstance(val, str) or len(val) > MAX_SHORT_FIELD_CHARS or not _SHORT_FIELD_RE.match(val):
                raise ValidationError(f"{key} must be a short plain string")
            record[key] = val
    return record


class StorageFull(OSError):
    pass


def _escape_log(s: str) -> str:
    """Escape control/non-printable characters so client input cannot forge log lines."""
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in s)


class RecordWriter:
    """Append-only, thread-safe JSONL writer shared by all listeners of one collector."""

    def __init__(self, output_path: Path, access_log: Path | None = None, session_id: str | None = None):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.access_log = Path(access_log) if access_log else None
        self.session_id = session_id
        self._lock = threading.Lock()

    def append_record(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            size = self.output_path.stat().st_size if self.output_path.exists() else 0
            if size + len(line.encode("utf-8")) > MAX_SESSION_BYTES:
                raise StorageFull(f"session log would exceed {MAX_SESSION_BYTES} bytes")
            with self.output_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def access(self, line: str) -> None:
        if self.access_log is None:
            sys.stderr.write(line)
            return
        with self._lock:
            with self.access_log.open("a", encoding="utf-8") as f:
                f.write(line)


class CollectorServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128   # listen backlog; concurrency itself is bounded by MAX_CONCURRENT_CONNECTIONS

    def __init__(self, addr, output, quiet: bool = False, max_connections: int = MAX_CONCURRENT_CONNECTIONS):
        super().__init__(addr, CollectorHandler)
        self.writer = output if isinstance(output, RecordWriter) else RecordWriter(Path(output))
        self.quiet = quiet
        self._slots = threading.BoundedSemaphore(max_connections)

    # Bound the number of concurrent handler threads (ThreadingHTTPServer is otherwise unbounded).
    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)   # over the cap: close immediately, no handler thread
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    # kept for backward compatibility with earlier tests/tools
    @property
    def output_path(self) -> Path:
        return self.writer.output_path

    def append_record(self, record: dict) -> None:
        self.writer.append_record(record)


class CollectorHandler(BaseHTTPRequestHandler):
    server: CollectorServer
    server_version = f"{SERVICE}/{VERSION}"
    sys_version = ""
    timeout = 10  # per-connection socket timeout (slowloris guard)

    # --- helpers -----------------------------------------------------------
    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, detail: str) -> None:
        self._send_json(status, {"status": "error", "error": detail})

    def log_message(self, fmt, *args):  # noqa: D401 - stdlib signature
        if not self.server.quiet:
            local = self.connection.getsockname()
            self.server.writer.access(
                f"{utc_now()} {self.client_address[0]}:{self.client_address[1]} -> "
                f"{local[0]}:{local[1]} {_escape_log(fmt % args)}\n")

    # --- routes ------------------------------------------------------------
    def do_GET(self):
        if self.path == "/health":
            body = {"status": "ok", "service": SERVICE, "version": VERSION, "server_time": utc_now()}
            if self.server.writer.session_id:
                body["session_id"] = self.server.writer.session_id
            self._send_json(HTTPStatus.OK, body)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        received_mono_ns = time.monotonic_ns()
        if self.path != "/v1/test-message":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return

        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        if self.headers.get("Transfer-Encoding"):
            self._error(HTTPStatus.LENGTH_REQUIRED, "chunked bodies not accepted")
            self.close_connection = True
            return
        raw_len = self.headers.get("Content-Length")
        if raw_len is None or not (raw_len.isascii() and raw_len.isdigit()):
            self._error(HTTPStatus.LENGTH_REQUIRED, "valid Content-Length required")
            self.close_connection = True
            return
        length = int(raw_len)
        if length > MAX_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body exceeds {MAX_BODY_BYTES} bytes")
            self.close_connection = True  # don't read the oversized body
            return

        body = self.rfile.read(length)
        if len(body) != length:
            self._error(HTTPStatus.BAD_REQUEST, "incomplete body")
            self.close_connection = True
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            record = validate_message(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "body must be valid UTF-8 JSON")
            return
        except ValidationError as e:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
            return

        server_time = utc_now()
        local = self.connection.getsockname()
        stored = {"server_time": server_time,
                  "server_mono_ns": received_mono_ns,
                  "remote_addr": self.client_address[0], "remote_port": self.client_address[1],
                  "server_addr": local[0], "server_port": local[1]}
        if self.server.writer.session_id:
            stored["session_id"] = self.server.writer.session_id
        stored.update(record)
        try:
            self.server.append_record(stored)
        except StorageFull:
            self._error(HTTPStatus.INSUFFICIENT_STORAGE, "collector session storage limit reached")
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "could not persist message")
            return
        self._send_json(HTTPStatus.OK, {"status": "accepted",
                                        "request_id": record["request_id"],
                                        "server_time": server_time})


def check_bind_host(host: str) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--host must be a literal IP address, got {host!r}") from None
    if ip.is_unspecified:
        raise argparse.ArgumentTypeError("wildcard bind addresses (0.0.0.0, ::) are refused")
    return host


def new_session(evidence_dir: Path, hosts: list[str], port: int) -> tuple[RecordWriter, Path]:
    session_id = str(uuid.uuid4())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = Path(evidence_dir) / f"{stamp}_{session_id[:8]}"
    d.mkdir(parents=True, exist_ok=False)  # never reuse an existing session directory
    meta = {"session_id": session_id, "service": SERVICE, "version": VERSION,
            "started_at": utc_now(), "started_mono_ns": time.monotonic_ns(),
            "hosts": hosts, "port": port, "pid": os.getpid(),
            "max_body_bytes": MAX_BODY_BYTES}
    (d / "session.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return RecordWriter(d / "messages.jsonl", d / "access.log", session_id), d


def main(argv=None) -> int:
    project = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", action="append", type=check_bind_host,
                   help="specific IP to bind (repeatable; default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--evidence-dir", type=Path, default=project / "evidence" / "server")
    g.add_argument("--output", type=Path, help="legacy single JSONL file")
    args = p.parse_args(argv)
    hosts = args.host or ["127.0.0.1"]
    if not 1 <= args.port <= 65535:
        p.error("--port out of range")

    if args.output:
        writer, where = RecordWriter(args.output), args.output
    else:
        writer, where = new_session(args.evidence_dir, hosts, args.port)

    servers = []
    try:
        for h in hosts:
            servers.append(CollectorServer((h, args.port), writer))
    except OSError as e:
        for s in servers:
            s.server_close()
        print(f"cannot bind {h}:{args.port}: {e}", file=sys.stderr)
        return 2

    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"{utc_now()} {SERVICE} {VERSION} listening on "
          + ", ".join(f"http://{h}:{args.port}" for h in hosts) + f" -> {where}",
          file=sys.stderr, flush=True)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.shutdown() if s is not servers[0] else None
            s.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
