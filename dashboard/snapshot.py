#!/usr/bin/env python3
"""Reproducible, sanitized SAVED-RESULT snapshot of the dashboard (stdlib only).

    python3 -I dashboard/snapshot.py [--out reports/public] [--root .]

Writes reports/public/<UTC stamp>_snapshot/ containing:
  index.html, wireless.html, runs/<run_id>.html   static pages, relative links only
  assets/dashboard.css                            local stylesheet (no network, no JavaScript)
  data.json                                       sanitized derived data + public run exports
  manifest.json                                   export time, source session/import IDs, analysis
                                                  versions, input fingerprints, file checksums
  SHA256SUMS

Raw evidence is only READ. Every output file is redacted (device identifiers, local addresses,
machine paths, secrets - including values embedded in free text) and then leak-scanned; any hit
aborts the snapshot and removes the partial directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis  # noqa: E402
import csi  # noqa: E402
import dashboard  # noqa: E402
import wireless  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
BANNER = ("SAVED RESULTS SNAPSHOT: static export generated {when} from local evidence "
          "(analysis v{av}, wireless analysis v{wv}). Not live, not connected to the phone or collector. "
          "Device identifiers, local addresses and machine paths are redacted.")


class LeakError(RuntimeError):
    pass


def _secrets(ev) -> tuple:
    out = set(analysis.known_secrets(ev))
    out.add(str(Path.home()))
    out.add(str(PROJECT))
    for s in ev.sessions:
        for h in s.get("hosts") or []:
            out.add(h)
    return tuple(sorted((x for x in out if x and len(x) >= 4), key=len, reverse=True))


def _scrub_html(html_text: str, secrets: tuple) -> str:
    # Redact free text (IPs, IPv6, MACs, paths, secrets) without touching markup structure.
    return analysis.redact(html_text, secrets)


def _relink(html_text: str, depth: int) -> str:
    up = "../" * depth
    html_text = re.sub(r"href='/runs/([0-9a-f-]{36})'", lambda m: f"href='{up}runs/{m.group(1)}.html'", html_text)
    html_text = re.sub(r'href="/runs/([0-9a-f-]{36})"', lambda m: f'href="{up}runs/{m.group(1)}.html"', html_text)
    html_text = html_text.replace("href='/wireless'", f"href='{up}wireless.html'").replace('href="/"', f'href="{up}index.html"')
    # The live import form and live-only download links have no meaning in a static snapshot.
    html_text = re.sub(r"<h2>Import app evidence</h2>.*?</form>", "", html_text, flags=re.S)
    html_text = re.sub(r"<p>Downloads: .*?</p>", "", html_text, flags=re.S)
    return html_text


def _externalize_css(html_text: str, depth: int) -> str:
    return re.sub(r"<style>.*?</style>", f'<link rel="stylesheet" href="{"../" * depth}assets/dashboard.css">',
                  html_text, count=1, flags=re.S)


def _bannerize(html_text: str, banner: str) -> str:
    return html_text.replace("<main>", f'<main><p class="note snapshot-banner"><b>{dashboard.E(banner)}</b></p>', 1)


def leak_patterns(secrets: tuple) -> list:
    pats = [re.compile(re.escape(s)) for s in secrets]
    pats += [re.compile(r"\b(?:10|127|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
             re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"),
             re.compile(r"/Users/[A-Za-z0-9._-]+"), re.compile(r"/home/[A-Za-z0-9._-]+"),
             re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"),
             re.compile(r"name=token|csrf", re.I)]
    return pats


def scan(directory: Path, secrets: tuple) -> list:
    hits = []
    pats = leak_patterns(secrets)
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            t = p.read_text(encoding="utf-8", errors="replace")
            for pat in pats:
                for m in pat.finditer(t):
                    hits.append(f"{p.relative_to(directory)}: {m.group(0)}")
    return hits


def build(root: Path, out_root: Path, stamp: str | None = None) -> Path:
    root = Path(root)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ev, runs = analysis.analyse(root)
    secrets = _secrets(ev)
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    banner = BANNER.format(when=when, av=analysis.ANALYSIS_VERSION, wv=wireless.WIRELESS_ANALYSIS_VERSION)
    out = Path(out_root) / f"{stamp}_snapshot"
    out.mkdir(parents=True, exist_ok=False)
    try:
        (out / "assets").mkdir()
        (out / "runs").mkdir()
        (out / "assets" / "dashboard.css").write_text(
            dashboard.CSS + "\n.snapshot-banner{border-left-color:#6250d6;background:#f1effc}\n", encoding="utf-8")

        def emit(rel: str, html_bytes: bytes, depth: int):
            t = html_bytes.decode("utf-8")
            t = _bannerize(_externalize_css(_relink(t, depth), depth), banner)
            t = t.replace("AndroidNetGuard — ", "AndroidNetGuard (saved snapshot) — ", 1)
            (out / rel).write_text(_scrub_html(t, secrets), encoding="utf-8")

        emit("index.html", dashboard.render_index(ev, runs, "", None), 0)
        emit("wireless.html", dashboard.render_wireless(ev, runs, root), 0)
        ordered = sorted(runs.values(), key=lambda r: (r.started or {}).get("wall_time", ""))
        for r in ordered:
            emit(f"runs/{r.run_id}.html", dashboard.page(f"Run {r.run_id[:8]}",
                                                          dashboard.run_body(r, "public", False, secrets)), 1)

        data = {
            "kind": "saved_result_snapshot", "exported_at": when,
            "analysis_version": analysis.ANALYSIS_VERSION,
            "wireless_analysis_version": wireless.WIRELESS_ANALYSIS_VERSION,
            "csi": {"schema": csi.SCHEMA, "adapter": csi.ADAPTER, "status": csi.status(root)},
            "status_definitions": analysis.STATUS_DEFINITIONS,
            "runs": [analysis.export_run(r, "public", when, secrets) for r in ordered],
        }
        (out / "data.json").write_text(json.dumps(analysis.redact(data, secrets), indent=1) + "\n", encoding="utf-8")

        inputs = []
        for f in sorted((root / "evidence").glob("*/*/*.jsonl")) + sorted((root / "collector" / "data").glob("*.jsonl")):
            inputs.append({"file": f"{f.parent.parent.name}/{f.parent.name}/{f.name}",
                           "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "bytes": f.stat().st_size})
        manifest = {
            "kind": "saved_result_snapshot", "label": "SAVED RESULTS (not live)", "exported_at": when,
            "generator": "dashboard/snapshot.py",
            "analysis_version": analysis.ANALYSIS_VERSION,
            "wireless_analysis_version": wireless.WIRELESS_ANALYSIS_VERSION,
            "source": {
                "collector_session_ids": sorted(s.get("session_id") for s in ev.sessions if s.get("session_id")),
                "device_import_ids": sorted(m.get("import_id") or m.get("_dir") for m in ev.imports),
                "legacy_record_count": len(ev.legacy_records),
                "app_process_ids": sorted({p for r in runs.values() for p in r.processes}),
            },
            "run_count": len(runs),
            "statuses": {s: sum(1 for r in runs.values() if r.status == s) for s in analysis.STATUS_DEFINITIONS},
            "redaction": "addresses/ports/endpoints/urls/serial/model/paths/secrets replaced with [redacted]/[ip]/[path]",
            "input_fingerprints": inputs,
        }
        (out / "manifest.json").write_text(json.dumps(analysis.redact(manifest, secrets), indent=2) + "\n", encoding="utf-8")

        hits = scan(out, secrets)
        if hits:
            raise LeakError("leak scan failed:\n  " + "\n  ".join(sorted(set(hits))[:40]))
        # manifest lists every content file; SHA256SUMS (written last) covers everything incl. the manifest
        content = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "manifest.json")
        man = json.loads((out / "manifest.json").read_text())
        man["files"] = [{"path": str(p.relative_to(out)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                        for p in content]
        (out / "manifest.json").write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        files = sorted(p for p in out.rglob("*") if p.is_file())
        (out / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}\n" for p in files), encoding="utf-8")
        return out
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise


def check_static(out: Path) -> list:
    """Problems that would stop the snapshot from opening offline: absolute/remote links, missing targets."""
    problems = []
    for p in sorted(out.rglob("*.html")):
        t = p.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:href|src)=["\']([^"\']+)["\']', t):
            u = m.group(1)
            if u.startswith(("http:", "https:", "//", "/")):
                problems.append(f"{p.relative_to(out)}: non-local reference {u}")
            elif not u.startswith("#") and not (p.parent / u).resolve().exists():
                problems.append(f"{p.relative_to(out)}: missing target {u}")
        if "<script" in t.lower():
            problems.append(f"{p.relative_to(out)}: contains script")
        # A saved report must never point readers at the live (machine-local) dashboard or collector.
        for m in re.finditer(r"(?:https?://)?(?:127\.0\.0\.1|localhost)(?::\d+)?", t):
            problems.append(f"{p.relative_to(out)}: live/local address reference {m.group(0)}")
    for p in sorted(out.rglob("*.json")):
        t = p.read_text(encoding="utf-8")
        for m in re.finditer(r"https?://(?:127\.0\.0\.1|localhost)[^\s\"]*", t):
            problems.append(f"{p.relative_to(out)}: live/local URL {m.group(0)}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=PROJECT / "reports" / "public")
    ap.add_argument("--root", type=Path, default=PROJECT)
    a = ap.parse_args(argv)
    out = build(a.root, a.out)
    probs = check_static(out)
    if probs:
        print("STATIC CHECK FAILED:\n  " + "\n  ".join(probs), file=sys.stderr)
        return 3
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
