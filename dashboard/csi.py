#!/usr/bin/env python3
"""Optional adapter for EXTERNAL CSI hardware (stdlib only). Not used by the phone app.

Source kinds are kept strictly distinct and are always supplied explicitly by the caller:
  ANDROID_WIFI_TELEMETRY  phone RSSI/link telemetry (handled elsewhere; never CSI)
  REAL_CSI                frames received from external hardware by `receive` below
  SYNTHETIC_FIXTURE       hand-built test frames (tests only; never displayed as real data)
There is no fallback: if no real frames arrive, nothing is synthesised.

Wire format: RuView "ADR-018" raw CSI frame, as implemented at the pinned revision
ruvnet/RuView@5728b90949e60e3f908d996997d453147c1794e0 (MIT License, Copyright (c) 2024 rUv).
Files reviewed (no code copied; independent reimplementation of the documented layout):
  firmware/esp32-csi-node/main/csi_collector.h      (CSI_MAGIC, CSI_HEADER_SIZE, flag bits)
  firmware/esp32-csi-node/main/csi_collector.c      (csi_serialize_frame: byte layout)
  v2/crates/wifi-densepose-hardware/src/esp32_parser.rs (validation: antennas 1..4,
      I/Q pairs <= 1024, sibling magics 0xC5110002..7 are not raw CSI)
  docs/adr/ADR-018-esp32-dev-implementation.md       (format table; states <=512 subcarriers,
      which differs from the parser code above - the code is treated as authoritative)

  offset size field
  0      4    magic 0xC5110001 (LE u32)
  4      1    node id
  5      1    antenna count
  6      2    subcarrier count (LE u16)
  8      4    frequency MHz (LE u32; firmware writes 0 for unmapped channels)
  12     4    sequence number (LE u32)
  16     1    RSSI dBm (i8)
  17     1    noise floor dBm (i8)
  18     1    PPDU type (0 HT/legacy, 1 HE-SU, 2 HE-MU, 3 HE-TB, 0xFF unknown; 0 if tagging disabled)
  19     1    flags (bit0 40 MHz, bit2 STBC, bit3 LDPC, bit4 cross-node sync valid,
              bit5 first I/Q word sanitised by firmware)
  20     N*2  I/Q int8 pairs, antenna-major: n_antennas * n_subcarriers pairs
The header carries NO device timestamp; records use host receive time (monotonic + wall).

    python3 -I dashboard/csi.py receive --bind <specific IP> --port 5005 --duration 60
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import struct
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "ang.csi/1"
REAL_CSI = "REAL_CSI"
SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
SOURCE_KINDS = {REAL_CSI, SYNTHETIC_FIXTURE}
ADAPTER = "ruview_adr018@5728b90949e60e3f908d996997d453147c1794e0"
ADAPTER_DESCRIPTION = ("RuView ADR-018 raw CSI frame parser (format pinned to RuView commit 5728b909); "
                       "UDP receiver bound to a specific address; no hardware compatibility claimed "
                       "until real frames are received and validated.")

MAGIC = 0xC5110001
SIBLING_MAGICS = {0xC5110002: "ADR-039 edge vitals", 0xC5110003: "ADR-069 feature vector",
                  0xC5110004: "ADR-063 fused vitals", 0xC5110005: "ADR-039 compressed CSI",
                  0xC5110006: "ADR-081 feature state", 0xC5110007: "ADR-095 temporal classification"}
HEADER = struct.Struct("<IBBHIIbbBB")   # 20 bytes
MAX_ANTENNAS = 4
MAX_IQ_PAIRS = 1024
MAX_DATAGRAM = 4096
UNITS = {"rssi_dbm": "dBm", "noise_floor_dbm": "dBm", "frequency_mhz": "MHz",
         "iq": "raw int8 I/Q counts (uncalibrated, device-specific scale)"}


class FrameError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def parse_frame(data: bytes, source_kind: str, source_id: str, host_seq: int,
                host_mono_ns: int, host_wall_time: str) -> dict:
    """Validates and parses one ADR-018 frame. Raises FrameError; never returns invented values."""
    if source_kind not in SOURCE_KINDS:
        raise FrameError("bad_source_kind", f"source_kind must be one of {sorted(SOURCE_KINDS)}")
    if len(data) >= 4:
        magic = struct.unpack_from("<I", data)[0]
        if magic in SIBLING_MAGICS:
            raise FrameError("non_csi_packet", SIBLING_MAGICS[magic])
    if len(data) < HEADER.size:
        raise FrameError("insufficient_data", f"need {HEADER.size} header bytes, got {len(data)}")
    (magic, node, n_ant, n_sc, freq, seq, rssi, noise, ppdu, flags) = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise FrameError("invalid_magic", f"0x{magic:08X}")
    if not 1 <= n_ant <= MAX_ANTENNAS:
        raise FrameError("invalid_antenna_count", str(n_ant))
    if n_sc > MAX_IQ_PAIRS // n_ant:
        raise FrameError("invalid_subcarrier_count", f"{n_sc} > {MAX_IQ_PAIRS // n_ant}")
    need = HEADER.size + n_ant * n_sc * 2
    if len(data) < need:
        raise FrameError("insufficient_data", f"need {need} bytes, got {len(data)}")
    warnings = []
    if len(data) > need:
        warnings.append(f"trailing_bytes:{len(data) - need}")
    if n_sc == 0:
        warnings.append("no_subcarriers")
    if freq == 0:
        warnings.append("frequency_unknown")
    if not -127 <= rssi <= 0:
        warnings.append("rssi_out_of_range")
    iq = []
    for a in range(n_ant):
        off = HEADER.size + a * n_sc * 2
        raw = struct.unpack_from(f"<{n_sc * 2}b", data, off) if n_sc else ()
        iq.append([[raw[i], raw[i + 1]] for i in range(0, len(raw), 2)])
    return {
        "schema": SCHEMA, "source_kind": source_kind, "source_id": source_id, "adapter": ADAPTER,
        "host_seq": host_seq, "host_mono_ns": host_mono_ns, "host_wall_time": host_wall_time,
        "device_timestamp": {"value": None, "status": "not_provided_by_format"},
        "frame_seq": seq, "node_id": node, "n_antennas": n_ant, "n_subcarriers": n_sc,
        "frequency_mhz": freq if freq else None, "rssi_dbm": rssi, "noise_floor_dbm": noise,
        "ppdu_type": {0: "ht_or_legacy", 1: "he_su", 2: "he_mu", 3: "he_tb", 0xFF: "unknown"}.get(ppdu, f"code_{ppdu}"),
        "flags": {"bw40": bool(flags & 1), "stbc": bool(flags & 4), "ldpc": bool(flags & 8),
                  "sync_valid": bool(flags & 16), "first_word_sanitized": bool(flags & 32), "raw": flags},
        "units": UNITS, "iq": iq, "validation_warnings": warnings,
    }


class SeqTracker:
    """Per-source frame sequence continuity (gaps/repeats are recorded, frames are kept)."""

    def __init__(self):
        self.last = {}

    def check(self, rec: dict) -> list:
        key = (rec["source_id"], rec["node_id"])
        prev = self.last.get(key)
        self.last[key] = rec["frame_seq"]
        if prev is None:
            return []
        delta = (rec["frame_seq"] - prev) & 0xFFFFFFFF
        if delta == 0:
            return ["seq_repeat"]
        if delta != 1:
            return [f"seq_gap:{delta - 1}"]
        return []


def status(root: Path) -> dict:
    """What the dashboard may display. Only REAL_CSI records count as real frames."""
    out = {"real_frames": 0, "rejected": 0, "sessions": 0, "non_real_records": 0}
    base = Path(root) / "evidence" / "csi"
    for sd in sorted(base.glob("*")) if base.exists() else []:
        f = sd / "frames.jsonl"
        if not f.exists():
            continue
        out["sessions"] += 1
        with f.open(encoding="utf-8") as source:
            for line in source:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    out["rejected"] += 1
                    continue
                if rec.get("record") == "rejected":
                    out["rejected"] += 1
                elif rec.get("source_kind") == REAL_CSI and rec.get("schema") == SCHEMA:
                    out["real_frames"] += 1
                else:
                    out["non_real_records"] += 1   # e.g. a stray fixture: never shown as real
    return out


def receive(bind: str, port: int, duration_s: int, out_root: Path, source_kind: str = REAL_CSI,
            max_frames: int = 100_000, stop_after: int | None = None) -> Path:
    """Listens for ADR-018 UDP frames on a SPECIFIC address and appends records to a new session dir."""
    ip = ipaddress.ip_address(bind)
    if ip.is_unspecified:
        raise ValueError("wildcard bind refused; use a specific address")
    if not 1 <= duration_s <= 3600:
        raise ValueError("duration must be 1..3600 s")
    sid = str(uuid.uuid4())
    d = Path(out_root) / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{sid[:8]}"
    d.mkdir(parents=True, exist_ok=False)
    (d / "session.json").write_text(json.dumps({
        "session_id": sid, "schema": SCHEMA, "adapter": ADAPTER, "source_kind": source_kind,
        "bind": bind, "port": port, "duration_s": duration_s,
        "started_at": datetime.now(timezone.utc).isoformat(), "started_mono_ns": time.monotonic_ns()}, indent=2))
    sock = socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind, port))
    sock.settimeout(0.5)
    tracker, n, deadline = SeqTracker(), 0, time.monotonic() + duration_s
    with (d / "frames.jsonl").open("a", encoding="utf-8") as fh:
        while time.monotonic() < deadline and n < max_frames and (stop_after is None or n < stop_after):
            try:
                data, peer = sock.recvfrom(MAX_DATAGRAM + 1)
            except socket.timeout:
                continue
            n += 1
            mono, wall = time.monotonic_ns(), datetime.now(timezone.utc).isoformat()
            src = f"udp:{peer[0]}"
            try:
                if len(data) > MAX_DATAGRAM:
                    raise FrameError("datagram_too_large", str(len(data)))
                rec = parse_frame(data, source_kind, src, n, mono, wall)
                rec["validation_warnings"] += tracker.check(rec)
                rec["record"] = "frame"
            except FrameError as e:
                rec = {"schema": SCHEMA, "record": "rejected", "source_kind": source_kind, "source_id": src,
                       "host_seq": n, "host_mono_ns": mono, "host_wall_time": wall, "error": e.code,
                       "detail": str(e), "bytes": len(data)}
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            fh.flush()
    sock.close()
    return d


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="External CSI adapter (RuView ADR-018 frames over UDP)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("receive", help="record REAL_CSI frames from external hardware")
    r.add_argument("--bind", required=True, help="specific local IP (no wildcard)")
    r.add_argument("--port", type=int, default=5005)
    r.add_argument("--duration", type=int, default=60)
    sub.add_parser("status", help="show whether real CSI frames exist")
    a = p.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if a.cmd == "status":
        st = status(root)
        print(json.dumps(st, indent=2))
        if st["real_frames"] == 0:
            print("External CSI hardware not connected (no REAL_CSI frames received).", file=sys.stderr)
        return 0
    d = receive(a.bind, a.port, a.duration, root / "evidence" / "csi")
    print(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
