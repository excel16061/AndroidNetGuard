#!/usr/bin/env python3
"""AndroidNetGuard local dashboard (Python standard library only).

Binds to 127.0.0.1 ONLY. It is a separate process from the LAN-facing
collector and is the only place with control actions (device import).

  python3 -I dashboard/dashboard.py [--port 8770] [--serial SERIAL]

Routes
  GET  /                               overview, catalog, runs, evidence sources
  GET  /runs/<run_id>                  run detail: steps, attempts, evidence, timeline
  GET  /runs/<run_id>/export.json      ?scope=public (default) | private   download
  GET  /runs/<run_id>/report.html      ?scope=public (default) | private   readable report
  GET  /api/runs                       derived summaries (private, localhost only)
  POST /import                         pull app evidence over adb (CSRF token required)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis  # noqa: E402
import csi  # noqa: E402
import wireless  # noqa: E402
from catalog import (CANCELLATION_VALIDATION_DEFINITIONS, CONNECTIVITY_TESTS,  # noqa: E402
                     FEATURE_STATUS_DEFINITIONS, KIND_LABELS, LIMITATIONS, NOT_IMPLEMENTED,
                     STATUS_DEFINITIONS, STATUS_DEFINITIONS_BY_KIND, STATUS_ORDER)

PROJECT = Path(__file__).resolve().parent.parent
PACKAGE = "com.androidnetguard.app"
DEVICE_EVIDENCE = "files/evidence/events.jsonl"
MAX_IMPORT_BYTES = 10 * 1024 * 1024
ADB_TIMEOUT_S = 20
BIND = "127.0.0.1"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- device import
class ImportError_(Exception):
    pass


def find_adb() -> str:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if home and (Path(home) / "platform-tools" / "adb").exists():
        return str(Path(home) / "platform-tools" / "adb")
    found = shutil.which("adb")
    if not found:
        raise ImportError_("adb not found; run `source ./env.sh` before starting the dashboard")
    return found


def _adb(adb: str, args: list, limit: int = 1 << 20) -> bytes:
    try:
        p = subprocess.run([adb, *args], capture_output=True, timeout=ADB_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise ImportError_(f"adb {' '.join(args[:3])} timed out after {ADB_TIMEOUT_S} s") from None
    if p.returncode != 0:
        raise ImportError_(f"adb failed: {p.stderr.decode(errors='replace').strip()[:300]}")
    if len(p.stdout) > limit:
        raise ImportError_(f"adb output exceeds {limit} bytes")
    return p.stdout


def select_device(adb: str, serial: str | None) -> str:
    out = _adb(adb, ["devices"]).decode(errors="replace").splitlines()[1:]
    ready = [ln.split()[0] for ln in out if ln.strip().endswith("\tdevice") or ln.split()[1:2] == ["device"]]
    if serial:
        if serial not in ready:
            raise ImportError_(f"device {serial} is not connected/authorized")
        return serial
    if len(ready) != 1:
        raise ImportError_(f"expected exactly one authorized device, found {len(ready)}; pass --serial")
    return ready[0]


def import_from_device(root: Path, serial: str | None = None, adb: str | None = None) -> dict:
    """Pull our app's evidence log via `run-as` (our debuggable app only) into a NEW import dir."""
    adb = adb or find_adb()
    s = select_device(adb, serial)
    if not _SERIAL_RE.match(s):
        raise ImportError_("unexpected device serial format")
    data = _adb(adb, ["-s", s, "exec-out", "run-as", PACKAGE, "cat", DEVICE_EVIDENCE], MAX_IMPORT_BYTES)
    if data.startswith(b"run-as:") or data.startswith(b"cat:"):
        raise ImportError_(data.decode(errors="replace").strip()[:300])
    lines = [ln for ln in data.split(b"\n") if ln.strip()]
    invalid = []
    for n, ln in enumerate(lines, 1):
        try:
            if not isinstance(json.loads(ln), dict):
                raise ValueError
        except ValueError:
            invalid.append(n)
    # Raw evidence is preserved even if some lines are damaged (e.g. a partial line left by a
    # crash mid-write); damaged lines are recorded here and reported by the analysis.
    if lines and len(invalid) == len(lines):
        raise ImportError_("no line of the device output is a JSON object; not app evidence, import aborted")
    model = _adb(adb, ["-s", s, "shell", "getprop", "ro.product.model"]).decode(errors="replace").strip()
    digest = hashlib.sha256(data).hexdigest()

    base = root / "evidence" / "device"
    for prev in sorted(base.glob("*/import.json")) if base.exists() else []:
        try:
            if json.loads(prev.read_text())["sha256"] == digest:
                return {"status": "unchanged", "detail": f"identical to existing import {prev.parent.name}",
                        "events": len(lines)}
        except (KeyError, json.JSONDecodeError):
            continue
    d = base / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=False)
    (d / "events.jsonl").write_bytes(data)
    meta = {"import_id": d.name, "imported_at": utc_now(), "serial": s, "model": model,
            "package": PACKAGE, "source_path": DEVICE_EVIDENCE, "bytes": len(data),
            "events": len(lines) - len(invalid), "invalid_lines": invalid,
            "ends_with_newline": data.endswith(b"\n") or not data,
            "sha256": digest, "method": "adb exec-out run-as … cat"}
    (d / "import.json").write_text(json.dumps(meta, indent=2) + "\n")
    return {"status": "imported", "detail": d.name, "events": len(lines)}


# --------------------------------------------------------------------------- rendering
E = html.escape

CSS = """
body{font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;color:#1d1d1f;background:#fafafa}
header{background:#1d2b3a;color:#fff;padding:12px 24px}header a{color:#cfe3ff}
main{padding:16px 24px;max-width:1300px}
h1{font-size:20px;margin:0}h2{font-size:17px;margin:24px 0 8px}h3{font-size:15px;margin:16px 0 6px}
table{border-collapse:collapse;width:100%;background:#fff;margin:6px 0}
th,td{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}th{background:#f0f2f5}
code,.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600;font-size:12px}
.s-pending{background:#e8e8e8}.s-running{background:#d6e8ff}.s-passed{background:#cdeccd}
.s-failed{background:#f8cfcf}.s-inconclusive{background:#fbe9b7}.s-execution_error{background:#e5d0f5}
.ni{color:#777;font-style:italic}.note{background:#fff8e1;border-left:4px solid #e0b000;padding:8px 12px}
.ok{color:#176b17}.bad{color:#a11}.muted{color:#777}
button{font-size:14px;padding:6px 14px}
.scrollx{overflow-x:auto}
""" + wireless.CHART_CSS


def badge(status: str | None) -> str:
    s = status or "pending"
    return f'<span class="badge s-{E(s)}">{E(s)}</span>'


def page(title: str, body: str, nav: bool = True) -> bytes:
    head = '<a href="/">Dashboard</a> · ' if nav else ""
    return (f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8><title>{E(title)}</title>"
            f"<style>{CSS}</style></head><body><header><h1>AndroidNetGuard — {E(title)}</h1>"
            f"<div>{head}Connectivity &amp; reliability evidence. No security findings are produced.</div>"
            f"</header><main>{body}</main></body></html>").encode("utf-8")


def legend(kinds=("connectivity", "observation")) -> str:
    """Status meanings are test-specific; render one table per kind plus the separate mechanism verdict."""
    out = []
    for k in kinds:
        defs = STATUS_DEFINITIONS_BY_KIND[k]
        rows = "".join(f"<tr><td>{badge(s)}</td><td>{E(defs[s])}</td></tr>" for s in STATUS_ORDER)
        out.append(f"<h3>{E(KIND_LABELS.get(k, k))}</h3><table><tr><th>Status</th><th>Meaning</th></tr>{rows}</table>")
    rows = "".join(f"<tr><td>{badge(s)}</td><td>{E(v)}</td></tr>" for s, v in CANCELLATION_VALIDATION_DEFINITIONS.items())
    out.append("<h3>Cancellation mechanism validation (assessed separately from the interrupted run's status)</h3>"
               f"<table><tr><th>Verdict</th><th>Meaning</th></tr>{rows}</table>")
    return "".join(out)


def _runlink(rid):
    return f"<a class=mono href='/runs/{E(rid)}'>{E(rid[:8])}</a>"


def render_index(ev, runs, token: str, flash: str | None) -> bytes:
    by_test: dict = {}
    for r in runs.values():
        by_test.setdefault(r.test_id, []).append(r)
    b = []
    if flash:
        b.append(f'<p class="note">{E(flash)}</p>')
    b.append('<p class="note">All statuses below are <b>derived</b> on the Mac from raw app + collector '
             'evidence. Phone-side statuses are provisional. Connectivity loss/recovery is a reliability '
             'observation, not a security violation.</p>')
    feats = analysis.feature_status(runs)

    def verified_cell(f):
        if not f or not f["verified"]:
            return '<span class="muted">not yet verified</span>'
        vb = f["verified_by"]
        return f'<b class=ok>✓ verified</b> by {_runlink(vb["run_id"])} <span class=muted>({f["passing_runs"]} of {f["runs"]} runs)</span>'

    def latest_cell(f):
        if not f or not f["latest"]:
            return '<span class="muted">no runs</span>'
        lt = f["latest"]
        return f'{badge(lt["status"])} {_runlink(lt["run_id"])}<br><span class="muted mono">{E(str(lt["started"] or ""))}</span>'

    b.append("<h2>Checks and observation sessions</h2>"
             "<p class=muted><b>Feature verified</b> and <b>latest run outcome</b> are shown separately: a later "
             "incomplete run (e.g. the user did not act, or setup was missing) does not erase an earlier verified check.</p>"
             "<div class=scrollx><table><tr><th>Test</th><th>Feature verified</th><th>Latest run outcome</th>"
             "<th>Mode / kind</th><th>Expected</th><th>Required evidence</th><th>Timeouts</th><th>Inconclusive when</th></tr>")
    for t in CONNECTIVITY_TESTS:
        f = feats.get(t["id"])
        b.append(f"<tr><td><b>{E(t['title'])}</b><br><code>{E(t['id'])}</code></td>"
                 f"<td>{verified_cell(f)}</td><td>{latest_cell(f)}</td><td>{E(t['mode'])} / {E(t['kind'])}</td>"
                 f"<td>{E(t['expected'])}</td><td>{E(t['evidence'])}</td>"
                 f"<td>{E(t['timeouts'])}</td><td>{E(t['inconclusive'])}</td></tr>")
    b.append("</table></div>")
    b.append("<h3>Mechanism checks assessed inside other runs</h3><table><tr><th>Mechanism</th><th>Verification rule</th>"
             "<th>Feature verified</th><th>Latest assessed run</th></tr>")
    b.append(f"<tr><td>UI cancellation</td><td>{E(CANCELLATION_VALIDATION_DEFINITIONS['passed'])}</td>"
             f"<td>{verified_cell(feats['ui_cancellation'])}</td><td>{latest_cell(feats['ui_cancellation'])}</td></tr>")
    b.append("<tr><td>Rotation during a run</td><td>Activity recreated (state restored) while a run was in progress, "
             "and that run reached passed (no lost, duplicated or missing requests).</td>"
             f"<td>{verified_cell(feats['rotation_during_run'])}</td><td>{latest_cell(feats['rotation_during_run'])}</td></tr>")
    b.append("</table><p class=muted>Cancel taps that never reached a run window leave no evidence and therefore "
             "cannot appear here as attempts.</p>")

    b.append("<p><a href='/wireless'>Wireless observability: session comparison and CSI input status →</a></p>")
    b.append("<h2>Security-policy tests</h2><p class=ni>Not implemented. No security policy and no "
             "independent verification method exist yet, so no security-policy test is offered or claimed.</p>")
    b.append("<h2>Future features</h2><table>")
    for name, why in NOT_IMPLEMENTED:
        b.append(f"<tr><td>{E(name)}</td><td class=ni>{E(why)}</td></tr>")
    b.append("</table>")

    b.append("<h2>Run history</h2>")
    if not runs:
        b.append("<p class=muted>No runs in imported evidence. Run a test on the phone, then import.</p>")
    else:
        b.append("<table><tr><th>Started (phone wall clock)</th><th>Test</th><th>Mode</th><th>Final</th>"
                 "<th>App-side</th><th>Attempts</th><th>Run ID</th></tr>")
        for r in sorted(runs.values(), key=lambda r: (r.started or {}).get("wall_time", ""), reverse=True):
            app = (r.finished or {}).get("status")
            b.append(f"<tr><td class=mono>{E((r.started or {}).get('wall_time', '?'))}</td>"
                     f"<td>{E(r.test_id or '?')}</td><td>{E((r.started or {}).get('test_mode', '?'))}</td>"
                     f"<td>{badge(r.status)}</td><td>{badge(app) if app else '—'}</td><td>{len(r.attempts)}</td>"
                     f"<td><a class=mono href='/runs/{E(r.run_id)}'>{E(r.run_id)}</a></td></tr>")
        b.append("</table>")

    b.append("<h2>Import app evidence</h2><p>Pulls <code>files/evidence/events.jsonl</code> from our app only "
             "(<code>run-as com.androidnetguard.app</code>) into a new <code>evidence/device/&lt;import&gt;/</code>"
             " directory. Existing imports are never modified.</p>"
             f"<form method=post action=/import><input type=hidden name=token value='{E(token)}'>"
             "<button type=submit>Import from connected phone</button></form>")

    b.append("<h2>Evidence sources (raw, read-only)</h2><table><tr><th>Source</th><th>Count</th></tr>")
    b.append(f"<tr><td>Collector sessions (evidence/server)</td><td>{len(ev.sessions)} sessions, "
             f"{len(ev.server_records)} records</td></tr>")
    b.append(f"<tr><td>Device imports (evidence/device)</td><td>{len(ev.imports)} imports, "
             f"{len(ev.app_events)} unique app events</td></tr>")
    b.append(f"<tr><td>Legacy smoke-test logs (collector/data, not linked to runs)</td>"
             f"<td>{len(ev.legacy_records)} records</td></tr></table>")
    if ev.issues:
        b.append("<h3>Evidence issues</h3><ul>" + "".join(f"<li>{E(i)}</li>" for i in ev.issues) + "</ul>")
    b.append("<h2>Status meanings (test-specific)</h2>" + legend())
    b.append("<h3>Feature verification</h3><table>" + "".join(
        f"<tr><td>{E(k)}</td><td>{E(v)}</td></tr>" for k, v in FEATURE_STATUS_DEFINITIONS.items()) + "</table>")
    b.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{E(x)}</li>" for x in LIMITATIONS) + "</ul>")
    return page("Dashboard", "".join(b))


def run_body(run, scope: str, links: bool, secrets: tuple = ()) -> str:
    s = analysis.run_summary(run)
    if scope == "public":
        s = analysis.redact(s, secrets)
    b = []
    b.append(f"<p>Final status {badge(s['final_status'])} "
             f"<span class=muted>({E(KIND_LABELS.get(s.get('status_kind'), 'Connectivity / reliability checks'))} definition)</span>"
             f" — {E(s['status_meaning'] or '')}</p>")
    b.append("<table>")
    for k in ("run_id", "test_id", "test_kind", "configured_mode", "endpoint", "params",
              "started_wall_time_phone", "duration_ms_phone_monotonic", "app_status_provisional", "process_ids"):
        b.append(f"<tr><th>{E(k)}</th><td class=mono>{E(json.dumps(s.get(k)) if not isinstance(s.get(k), str) else s.get(k))}</td></tr>")
    b.append("</table><p class=muted>Configured mode is what the test was set up for. Observed transport (per "
             "attempt, below) is what Android reported; the LAN address alone does not prove Wi-Fi.</p>")
    if links:
        rid = E(run.run_id)
        b.append(f"<p>Downloads: <a href='/runs/{rid}/export.json?scope=public'>JSON (public, redacted)</a> · "
                 f"<a href='/runs/{rid}/export.json?scope=private'>JSON (private, full)</a> · "
                 f"<a href='/runs/{rid}/report.html?scope=public'>HTML report (public)</a> · "
                 f"<a href='/runs/{rid}/report.html?scope=private'>HTML report (private)</a></p>")
    b.append("<h2>Derivation</h2><ul>" + "".join(f"<li>{E(x)}</li>" for x in s["reasons"]) + "</ul>")
    b.append("<table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>" + "".join(
        f"<tr><td>{E(c['check'])}</td><td class={'ok' if c['ok'] else 'bad'}>{'yes' if c['ok'] else 'no'}</td>"
        f"<td>{E(c['detail'])}</td></tr>" for c in s["checks"]) + "</table>")
    cx = s.get("cancellation")
    if cx and (cx["requested"] or cx["completed"]):
        b.append("<h2>Cancellation</h2><table>"
                 f"<tr><th>Cancel tapped (cancel_requested)</th><td>{E(str(cx['requested_ms']))} ms</td></tr>"
                 f"<tr><th>Runner stopped (run_cancelled)</th><td>{E(str(cx['completed_ms']))} ms</td></tr>"
                 f"<tr><th>Finished before tap</th><td>{len(cx['finished_before_tap'])}</td></tr>"
                 f"<tr><th>In flight at tap</th><td>{E('; '.join(cx['in_flight']) or 'none')}</td></tr>"
                 f"<tr><th>Started after tap</th><td>{E('; '.join(cx['started_after_request']) or 'none')}</td></tr>"
                 f"<tr><th>Stop latency</th><td>{E(str(cx['stop_latency_ms']))} ms</td></tr>"
                 f"<tr><th>Cancellation mechanism validation</th><td>{badge(cx['validation'])} "
                 f"{E(cx['validation_reason'])}</td></tr>"
                 "</table><p class=muted>Times are phone-monotonic ms since run_started. The mechanism verdict is "
                 "separate from the run status: a cancelled run is <b>inconclusive</b> (the user stopped it), "
                 "not an execution error.</p>")
    w = s.get("wireless")
    if w:
        b.append(wireless_section(w))
    rec = s.get("ui_recreations") or []
    if rec:
        b.append("<h2>UI recreations during run</h2><table><tr><th>t (ms)</th><th>Orientation</th>"
                 "<th>Restored state</th></tr>" + "".join(
                     f"<tr><td>{E(str(r['t_ms']))}</td><td>{E(str(r['orientation']))}</td>"
                     f"<td>{E(str(r['restored_state']))}</td></tr>" for r in rec) + "</table>")
    if s["issues"]:
        b.append("<h3>Evidence issues</h3><ul>" + "".join(f"<li>{E(i)}</li>" for i in s["issues"]) + "</ul>")

    b.append("<h2>Steps and request evidence</h2><table><tr><th>#</th><th>Step</th><th>App step status</th>"
             "<th>Request ID</th><th>App: outcome / ack / elapsed (phone mono)</th><th>Observed transport</th>"
             "<th>Collector records</th><th>Correlation</th></tr>")
    for st in s["steps"]:
        atts = st["attempts"] or [None]
        for i, a in enumerate(atts):
            head = (f"<td rowspan={len(atts)}>{st['index']}</td><td rowspan={len(atts)}>{E(st['kind'])}"
                    f"<br><span class=muted>{E(st['description'])}</span>"
                    f"{'<br><i>informational</i>' if st['informational'] else ''}</td>"
                    f"<td rowspan={len(atts)}>{badge(st['app_status'])}<br><span class=muted>"
                    f"{E(st['app_reason'] or '')}</span></td>") if i == 0 else ""
            if a is None:
                b.append(f"<tr>{head}<td colspan=5 class=muted>no request (wait/observation step)</td></tr>")
                continue
            srv = (f"{a['server_record_count']}" + (f" (peer {E(str(a['server_remote_addr']))} → "
                                                     f"{E(str(a['server_addr']))})" if a['server_record_count'] == 1 else ""))
            if a["kind"] == "health":
                srv = "n/a (health is app-side only)"
            b.append(f"<tr>{head}<td class=mono>{E(a['request_id'])}<br>{E(a['kind'])}"
                     f"{' #' + str(a['seq']) if a['seq'] else ''}</td>"
                     f"<td>{E(str(a['outcome']))} / {'same ID' if a['acked_same_id'] else 'no'} / "
                     f"{E(str(a['elapsed_ms']))} ms{('<br><span class=bad>' + E(a['error']) + '</span>') if a['error'] else ''}</td>"
                     f"<td>{E(str(a['observed_transport_before']))} → {E(str(a['observed_transport_after']))}</td>"
                     f"<td>{srv}</td><td><code>{E(a['correlation'])}</code></td></tr>")
    b.append("</table>")

    rows = analysis.timeline(run)
    if scope == "public":
        rows = analysis.redact(rows, secrets)
    b.append("<h2>Timeline</h2><p class=muted>Each source uses its own monotonic clock with its own origin. "
             "App and server offsets are not comparable and are never subtracted.</p>"
             "<table><tr><th>Source</th><th>t (ms)</th><th>Event</th><th>Request ID</th><th>Detail</th>"
             "<th>Wall clock (that device)</th></tr>")
    for r in rows:
        b.append(f"<tr><td>{E(r['source'])}</td><td class=mono>{E(str(r['t_ms']))}</td><td>{E(r['event'] or '')}</td>"
                 f"<td class=mono>{E(r['request_id'] or '')}</td><td>{E(r['detail'] or '')}</td>"
                 f"<td class=mono>{E(r['wall_time'] or '')}</td></tr>")
    b.append("</table>")
    b.append("<h2>Status meanings (definitions used for this run)</h2>" + legend((s.get("status_kind") or "connectivity",)))
    b.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{E(x)}</li>" for x in LIMITATIONS) + "</ul>")
    return "".join(b)


def wireless_section(w: dict) -> str:
    det = w["detector"]
    b = ["<h2>Wireless observation (ANDROID_WIFI_TELEMETRY — not CSI)</h2>",
         f"<p>Session label <code>{E(str(w['session_label']))}</code>; parameters <code>{E(json.dumps(w['params']))}</code>. "
         "All times are phone-monotonic seconds since run start. Collector records are linked to probes by request ID; "
         "phone and Mac clocks are never subtracted.</p>",
         wireless.tiles(w), wireless.rssi_chart(w), wireless.duration_chart(w)]
    b.append("<h3>Baseline and anomaly alerts</h3>")
    if det["baseline"]:
        b.append(f"<p>Baseline: median of the first {det['baseline']['samples']} fresh samples = "
                 f"<b>{E(str(det['baseline']['rssi_dbm']))} dBm</b>, link speed {E(str(det['baseline']['link_speed_mbps']))} Mbps "
                 f"(up to t={E(str(det['baseline']['t_end_s']))} s).</p>")
    rows = det["alerts"] + det["observations"]
    if rows:
        b.append("<table><tr><th>Type</th><th>Kind</th><th>Start s</th><th>End s</th><th>Detail</th></tr>" + "".join(
            f"<tr><td>{'anomaly alert' if r in det['alerts'] else 'observation'}</td><td>{E(r['kind'])}</td>"
            f"<td>{E(str(r.get('start_s', '')))}</td><td>{E(str(r.get('end_s', '')))}</td><td>{E(r['detail'])}</td></tr>"
            for r in rows) + "</table>")
    else:
        b.append("<p class=muted>No anomaly alerts or observations under the documented thresholds.</p>")
    b.append("<p class=muted>Alerts are rule-based observations about signal level, link speed and connectivity. "
             "They do not identify a cause and are not attack detections.</p>")
    fails = [p for p in w["series"]["probes"] if not p["ok"]]
    b.append("<h3>Probe outcomes</h3>")
    if fails:
        b.append("<table><tr><th>t (s)</th><th>Request ID</th><th>Outcome</th><th>Transport</th><th>Follow-up health</th>"
                 "<th>Attribution (most specific the evidence supports)</th></tr>" + "".join(
                     f"<tr><td>{E(str(p['t_s']))}</td><td class=mono>{E(p['request_id'])}</td><td>{E(str(p['outcome']))}</td>"
                     f"<td>{E(str(p['transport_before']))}→{E(str(p['transport_after']))}</td>"
                     f"<td>{E(str(p.get('follow_up_health')))}</td><td>{E(p['attribution'])}</td></tr>" for p in fails) + "</table>")
    else:
        b.append(f"<p>All {w['probes_total']} probes acknowledged.</p>")
    b.append("<details><summary>Telemetry table view</summary><table><tr><th>#</th><th>t (s)</th><th>RSSI dBm</th>"
             "<th>RSSI status</th><th>Freshness</th><th>MHz</th><th>Link Mbps</th><th>Tx/Rx Mbps</th><th>Standard</th>"
             "<th>Callback</th><th>Transport</th></tr>" + "".join(
                 f"<tr><td>{x['seq']}</td><td>{x['t_s']}</td><td>{E(str(x['rssi_dbm']))}</td><td>{E(str(x['rssi_status']))}</td>"
                 f"<td>{E(str(x['freshness']))}</td><td>{E(str(x['frequency_mhz']))}</td><td>{E(str(x['link_speed_mbps']))}</td>"
                 f"<td>{E(str(x['tx_link_speed_mbps']))}/{E(str(x['rx_link_speed_mbps']))}</td><td>{E(str(x['wifi_standard']))}</td>"
                 f"<td>{E(str(x['callback_status']))}{' (' + str(x['callback_age_ms']) + ' ms)' if x['callback_age_ms'] is not None else ''}</td>"
                 f"<td>{E(str(x['observed_transport']))}</td></tr>" for x in w["series"]["samples"]) + "</table></details>")
    return "".join(b)


def render_wireless(ev, runs, root: Path) -> bytes:
    obs = sorted((r for r in runs.values() if r.test_id == "wifi_observation" and r.started),
                 key=lambda r: r.started.get("wall_time", ""))
    b = ['<p class="note">ANDROID_WIFI_TELEMETRY sessions recorded by the phone app. This is platform-reported Wi-Fi '
         'telemetry (RSSI, frequency, link speed), <b>not</b> channel state information.</p>']
    b.append("<h2>Session comparison</h2>")
    if not obs:
        b.append("<p class=muted>No wireless observation sessions in the imported evidence yet. Start one on the phone "
                 "(Tests → Wireless observation session), then import.</p>")
    else:
        b.append("<div class=scrollx><table><tr><th>Started (phone wall clock)</th><th>Label</th><th>Final</th><th>Duration s</th>"
                 "<th>Sample completeness (recorded / expected)</th><th>Telemetry freshness (fresh / repeated)</th><th>RSSI median [min, max] dBm</th><th>MHz</th><th>Link Mbps median</th>"
                 "<th>Probes ack/total</th><th>Health ok/total</th><th>Transport changes</th><th>Alerts</th><th>Run</th></tr>")
        for r in obs:
            w = analysis._wireless(r)
            q = w["rssi_dbm"]
            b.append(f"<tr><td class=mono>{E(r.started.get('wall_time', ''))}</td><td><code>{E(str(w['session_label']))}</code></td>"
                     f"<td>{badge(r.status)}</td><td>{E(str(w['duration_s']))}</td>"
                     f"<td>{w['sample_completeness']['recorded']} / {w['sample_completeness']['expected']}"
                     f"{' (' + format(w['sample_completeness']['ratio'], '.0%') + ')' if w['sample_completeness']['ratio'] is not None else ''}</td>"
                     f"<td>{w['telemetry_freshness']['fresh']} / {w['telemetry_freshness']['repeated']}</td>"
                     f"<td>{E(str(q['median']))} [{E(str(q['min']))}, {E(str(q['max']))}]</td>"
                     f"<td>{E(', '.join(str(x) for x in w['frequency_mhz_values']) or 'n/a')}</td>"
                     f"<td>{E(str(w['link_speed_mbps_median']))}</td>"
                     f"<td>{w['probes_acknowledged']}/{w['probes_total']}</td><td>{w['health_checks_ok']}/{w['health_checks_total']}</td>"
                     f"<td>{len(w['transport_changes'])}</td><td>{len(w['detector']['alerts'])}</td>"
                     f"<td><a class=mono href='/runs/{E(r.run_id)}'>{E(r.run_id[:8])}</a></td></tr>")
        b.append("</table></div><p class=muted>Sessions differ in time, placement and conditions; the table compares what was "
                 "recorded, not causes.</p>")
    st = csi.status(root)
    b.append("<h2>External CSI input</h2>")
    if st["real_frames"] == 0:
        b.append("<p><b>External CSI hardware not connected.</b> No REAL_CSI frames have been received. "
                 "Android telemetry is never substituted for CSI, and synthetic fixtures are never shown as real data.</p>")
    else:
        b.append(f"<p>REAL_CSI frames received: <b>{st['real_frames']}</b> in {st['sessions']} capture session(s); "
                 f"rejected frames: {st['rejected']}.</p>")
    b.append(f"<p class=muted>Adapter: {E(csi.ADAPTER_DESCRIPTION)}</p>")
    b.append("<h2>Detector parameters</h2><table>" + "".join(
        f"<tr><td><code>{E(k)}</code></td><td>{E(str(v))}</td></tr>" for k, v in wireless.PARAMETERS.items()) + "</table>")
    return page("Wireless observability", "".join(b))


def render_report(run, scope: str, secrets: tuple = ()) -> bytes:
    banner = (f'<p class="note">Report scope: <b>{E(scope)}</b>. Generated {E(utc_now())} from raw evidence. '
              + ("Device identifiers and local addresses are redacted." if scope == "public"
                 else "PRIVATE: contains device identifiers and local addresses — do not share.") + "</p>")
    body = run_body(run, scope, links=False, secrets=secrets)
    return page(f"Run report {run.run_id[:8]}", banner + body + f"<p class=muted>analysis v{E(analysis.ANALYSIS_VERSION)}</p>",
                nav=False)


# --------------------------------------------------------------------------- server
class Dashboard(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, root: Path, serial: str | None):
        super().__init__((BIND, port), Handler)
        self.root = root
        self.serial = serial
        self.token = secrets.token_urlsafe(24)
        self.flash: str | None = None


class Handler(BaseHTTPRequestHandler):
    server: Dashboard
    server_version = "androidnetguard-dashboard"
    sys_version = ""
    timeout = 15

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{utc_now()} {fmt % args}\n")

    def _host_ok(self) -> bool:
        # DNS-rebinding guard: only accept our own loopback origin.
        port = self.server.server_port
        host = self.headers.get("Host", "")
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, msg):
        self._send(status, page("Error", f"<p>{E(msg)}</p>"))

    def _run(self, run_id):
        if not _UUID_RE.match(run_id):
            return None, None
        ev, runs = analysis.analyse(self.server.root)
        return ev, runs.get(run_id)

    def _secrets(self, ev):
        return analysis.known_secrets(ev) + (self.server.token,)

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        scope = q.get("scope", ["public"])[0]
        if scope not in ("public", "private"):
            return self._error(HTTPStatus.BAD_REQUEST, "scope must be public or private")
        parts = [p for p in u.path.split("/") if p]
        if not parts:
            ev, runs = analysis.analyse(self.server.root)
            flash, self.server.flash = self.server.flash, None
            return self._send(HTTPStatus.OK, render_index(ev, runs, self.server.token, flash))
        if parts == ["wireless"]:
            ev, runs = analysis.analyse(self.server.root)
            return self._send(HTTPStatus.OK, render_wireless(ev, runs, self.server.root))
        if parts == ["api", "runs"]:
            _, runs = analysis.analyse(self.server.root)
            body = json.dumps([analysis.run_summary(r) for r in runs.values()], indent=2).encode()
            return self._send(HTTPStatus.OK, body, "application/json")
        if parts[0] == "runs" and len(parts) in (2, 3):
            ev, run = self._run(parts[1])
            if run is None:
                return self._error(HTTPStatus.NOT_FOUND, "run not found")
            if len(parts) == 2:
                return self._send(HTTPStatus.OK, page(f"Run {run.run_id[:8]}", run_body(run, "private", True)))
            if parts[2] == "export.json":
                doc = analysis.export_run(run, scope, utc_now(), self._secrets(ev))
                return self._send(HTTPStatus.OK, json.dumps(doc, indent=2).encode(), "application/json",
                                  {"Content-Disposition":
                                   f'attachment; filename="androidnetguard-run-{run.run_id}-{scope}.json"'})
            if parts[2] == "report.html":
                return self._send(HTTPStatus.OK, render_report(run, scope, self._secrets(ev)), extra={
                    "Content-Disposition": f'inline; filename="androidnetguard-run-{run.run_id}-{scope}.html"'})
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        origin = self.headers.get("Origin")
        port = self.server.server_port
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self._error(HTTPStatus.FORBIDDEN, "cross-origin request refused")
        if self.path != "/import":
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        n = self.headers.get("Content-Length", "")
        if not n.isdigit() or int(n) > 1024:
            return self._error(HTTPStatus.BAD_REQUEST, "bad request size")
        form = parse_qs(self.rfile.read(int(n)).decode(errors="replace"))
        if not secrets.compare_digest(form.get("token", [""])[0], self.server.token):
            return self._error(HTTPStatus.FORBIDDEN, "invalid token")
        try:
            r = import_from_device(self.server.root, self.server.serial)
            self.server.flash = f"Import {r['status']}: {r['detail']} ({r['events']} events)"
        except ImportError_ as e:
            self.server.flash = f"Import failed: {e}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AndroidNetGuard local dashboard (binds 127.0.0.1 only)")
    p.add_argument("--port", type=int, default=8770)
    p.add_argument("--serial", help="adb serial to import from (default: the single connected device)")
    p.add_argument("--root", type=Path, default=PROJECT, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.port == 8765:
        p.error("port 8765 is the collector's; use a different port")
    srv = Dashboard(args.port, args.root, args.serial)
    print(f"{utc_now()} dashboard on http://{BIND}:{srv.server_port}/ (localhost only)", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
