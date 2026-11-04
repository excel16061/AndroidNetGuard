#!/usr/bin/env python3
"""Build a sanitized, shareable release of AndroidNetGuard (stdlib only).

    python3 -I tools/make_release.py [--out DIR] [--runs RUN_ID ...]

Creates, under --out (default: <project>/release/, git-ignored):
  AndroidNetGuard-<version>-<stamp>/           sanitized package (allow-listed files only)
  AndroidNetGuard-<version>-<stamp>.zip        ZIP of the package
  PRIVATE_MAPPING-<version>-<stamp>.json       NOT for sharing: originals, hashes, real values

Never modifies the source tree or any raw evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "dashboard"))
import analysis  # noqa: E402

# Allow-list: source, build wrapper/config, docs. Nothing else is copied.
INCLUDE = [
    "README.md", "VALIDATION.md", "USB_SMOKE_TEST.md", "WIFI_SMOKE_TEST.md",
    "build.gradle.kts", "settings.gradle.kts", "gradle.properties", "gradlew", "gradlew.bat",
    "gradle/wrapper/gradle-wrapper.jar", "gradle/wrapper/gradle-wrapper.properties",
    "app/build.gradle.kts", "app/src/**/*", "collector/*.py", "dashboard/*.py", "docs/*.md",
    "tools/make_release.py", "docs/images/*.png", "reports/public/*.md", "reports/public/*/**/*",
]
EXCLUDE_PARTS = {"build", ".gradle", "__pycache__", ".idea", ".git", "evidence", "data", "release"}
EXCLUDE_NAMES = {"env.sh", "local.properties", "HANDOFF.md", ".DS_Store"}
TEXT_SUFFIXES = {".md", ".py", ".java", ".kts", ".xml", ".properties", ".txt", ".json", ".bat", ""}

DEFAULT_SAMPLE_RUNS = [
    "cf710e48-9342-47c4-aa44-f67d16d811e5",   # direct Wi-Fi connectivity
    "5a42a6a2-f5cb-4464-b9c2-8b493ad47f19",   # USB connectivity
    "e8822050-8394-4e56-91e6-46caed073652",   # app restart
    "fa3014fa-d33a-4a35-bbb3-57c28471a04d",   # rotation during a run
    "f00ad06f-d23b-43c0-acd9-9463c2fe495e",   # reconnect on 0.2.0, inconclusive (loss not observed; onLost defect)
    "4332f30e-ac6f-40c5-8de3-037bf899f7bc",   # reconnect on 0.4.1, passed (defect fix verified)
    "22516f79-dcd1-4994-9918-f2e3a4c9a717",   # wireless observation: stationary
    "29016b2c-dad9-4db4-a796-1ae4e2768263",   # wireless observation: moved_farther
    "cde75240-ae69-4223-9aa9-f26b4bef4da3",   # wireless observation: disconnect_reconnect
    "50edfe31-5158-4e03-b2f4-de8fefd73e32",   # UI cancellation check on 0.4.1 (mechanism passed)
]


def collect_secrets(ev) -> dict:
    """Real values to rewrite in text files -> placeholder. Kept only in the private mapping."""
    subs = {}
    for m in ev.imports:
        if m.get("serial"):
            subs[m["serial"]] = "DEVICE_SERIAL"
    home = str(Path.home())
    subs[home] = "~"
    return subs


LAN_RE = re.compile(r"\b192\.168\.1\.(\d{1,3})\b")


def sanitize_text(text: str, subs: dict) -> tuple[str, list]:
    applied = []
    for real, ph in sorted(subs.items(), key=lambda kv: -len(kv[0])):
        if real in text:
            text = text.replace(real, ph)
            applied.append(f"literal->{ph}")
    new, n = LAN_RE.subn(lambda m: f"192.0.2.{m.group(1)}", text)
    if n:
        applied.append(f"192.168.1.x->192.0.2.x (RFC 5737) x{n}")
    return new, applied


def files_to_copy() -> list[Path]:
    out = []
    for pat in INCLUDE:
        for p in sorted(PROJECT.glob(pat)):
            rel = p.relative_to(PROJECT)
            if p.is_dir() or EXCLUDE_PARTS & set(rel.parts) or p.name in EXCLUDE_NAMES:
                continue
            out.append(p)
    return sorted(set(out))


ENV_EXAMPLE = """# Copy to env.sh and adjust paths for YOUR machine, then: source ./env.sh
# (env.sh is git-ignored; it is not included in this package.)
export JAVA_HOME="/path/to/jdk-17"                 # e.g. macOS Homebrew: /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME="/path/to/android-sdk"         # needs platform-tools, platforms;android-36, build-tools;36.0.0
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$JAVA_HOME/bin:$PATH"
"""

LOCAL_PROPS_EXAMPLE = """# Copy to local.properties (git-ignored). Used ONLY to generate the DEBUG build's
# cleartext allow-list; release builds ignore it.
# The collector computer's LAN IPv4 address (check with: ipconfig getifaddr en0  /  ip addr):
ang.collectorHost=192.0.2.10
"""

NOTICES = """# Third-party components

| Component | Where | License | Bundled in this package? |
|---|---|---|---|
| Gradle Wrapper (`gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`) | build | Apache License 2.0 (header in `gradlew`; `META-INF/LICENSE` inside the jar) | yes, unmodified |
| Gradle 9.6.1 distribution | build | Apache License 2.0 | no; downloaded by the wrapper (SHA-256 pinned in `gradle-wrapper.properties`) |
| Android Gradle Plugin 9.5.0 | build | Apache License 2.0 | no; resolved from Google Maven |
| JUnit 4.13.2 | tests only | Eclipse Public License 1.0 | no; resolved from Maven Central |
| Hamcrest Core 1.3 (via JUnit) | tests only | BSD 3-Clause | no |
| Android SDK platform 36 / build-tools 36.0.0 | build | Android SDK License | no; installed separately |
| Python 3 standard library | collector, dashboard | PSF License | no |
| RuView ADR-018 CSI frame format (ruvnet/RuView @ 5728b90949e60e3f908d996997d453147c1794e0) | `dashboard/csi.py` | MIT License, Copyright (c) 2024 rUv | no code copied; the parser is an independent implementation of the documented byte layout, attributed here and in the module docstring |

The app has no third-party runtime dependencies (no AndroidX).

**Project license:** the project files carry no license. All rights are reserved by the author
until a license is chosen. This package does not grant any license.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=PROJECT / "release")
    ap.add_argument("--runs", nargs="*", default=None, help="run IDs for the evidence sample")
    args = ap.parse_args(argv)

    version = re.search(r'versionName = "([^"]+)"', (PROJECT / "app/build.gradle.kts").read_text()).group(1)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"AndroidNetGuard-{version}-{stamp}"
    pkg = args.out / name
    pkg.mkdir(parents=True, exist_ok=False)

    ev, runs = analysis.analyse(PROJECT)
    subs = collect_secrets(ev)
    mapping = {"package": name, "created_at": stamp, "analysis_version": analysis.ANALYSIS_VERSION,
               "substitutions": {"literals": subs, "lan_prefix": {"192.168.1.": "192.0.2."}},
               "files": [], "evidence_samples": []}

    # 1. source + docs (sanitized text)
    for src in files_to_copy():
        rel = src.relative_to(PROJECT)
        dst = pkg / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT_SUFFIXES and src.name != "gradle-wrapper.jar":
            text, applied = sanitize_text(src.read_text(encoding="utf-8"), subs)
            dst.write_text(text, encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
            applied = []
        shutil.copymode(src, dst)
        mapping["files"].append({"package_path": str(rel), "source_path": str(rel),
                                 "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                                 "transformations": applied})

    # 2. portable configuration examples
    (pkg / "env.example.sh").write_text(ENV_EXAMPLE)
    (pkg / "local.properties.example").write_text(LOCAL_PROPS_EXAMPLE)
    (pkg / "THIRD_PARTY_NOTICES.md").write_text(NOTICES)
    (pkg / ".gitignore").write_text((PROJECT / ".gitignore").read_text())

    # 3. sanitized evidence sample (public-scope exports of genuine runs)
    sample_ids = args.runs if args.runs is not None else DEFAULT_SAMPLE_RUNS
    sd = pkg / "evidence-sample"
    sd.mkdir()
    secrets = analysis.known_secrets(ev)
    readme = ["# Evidence sample (sanitized)", "",
              "Public-scope exports of genuine runs, produced by `dashboard/analysis.py` "
              f"(analysis v{analysis.ANALYSIS_VERSION}). Transformations applied to every file:", "",
              "* addresses, ports, endpoints and URLs: `[redacted]` or `[ip]`;",
              "* device serial and model: `[redacted]`; local paths: `[path]`;",
              "* everything else (IDs, events, outcomes, phone-monotonic times) is copied unchanged.", "",
              "Each file maps to private originals through a mapping file that is **not** included.", "",
              "| File | Test | Final status |", "|---|---|---|"]
    for rid in sample_ids:
        run = runs.get(rid)
        if run is None:
            print(f"warning: run {rid} not found; skipped", file=sys.stderr)
            continue
        doc = analysis.export_run(run, "public", stamp, secrets)
        f = sd / f"run-{rid}.public.json"
        f.write_text(json.dumps(doc, indent=2) + "\n")
        originals = sorted({str(p.relative_to(PROJECT)) for p in PROJECT.glob("evidence/device/*/events.jsonl")
                            if rid in p.read_text()}
                           | {str(p.relative_to(PROJECT)) for p in PROJECT.glob("evidence/server/*/messages.jsonl")
                              if rid in p.read_text()})
        mapping["evidence_samples"].append({
            "package_path": str(f.relative_to(pkg)), "run_id": rid, "scope": "public",
            "originals": [{"path": o, "sha256": hashlib.sha256((PROJECT / o).read_bytes()).hexdigest()}
                          for o in originals]})
        readme.append(f"| `{f.name}` | {run.test_id} | {run.status} |")
    summ = [{k: analysis.run_summary(r)[k] for k in ("run_id", "test_id", "final_status", "app_status_provisional")}
            for r in sorted(runs.values(), key=lambda r: (r.started or {}).get("wall_time", ""))]
    (sd / "all-runs-summary.public.json").write_text(json.dumps(analysis.redact(
        {"analysis_version": analysis.ANALYSIS_VERSION, "runs": summ}, secrets), indent=2) + "\n")
    readme.append("| `all-runs-summary.public.json` | all runs | final statuses only |")
    (sd / "README.md").write_text("\n".join(readme) + "\n")

    # 4. leak scan of the whole package
    leaks = []
    patterns = [re.compile(re.escape(v)) for v in subs if len(v) >= 4] + [
        re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), re.compile(re.escape("/Users/" + Path.home().name) + r"\b"),
        re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"), re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")]
    # Documented non-secrets only: Android's redaction placeholder MAC, and RFC 2606 example domains.
    allowed = re.compile(r"^(?:02:00:00:00:00:00|[\w.+-]+@example\.(?:org|com|net))$")
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and p.suffix not in (".jar", ".png"):   # binaries: PNGs are rendered from the sanitized snapshot
            t = p.read_text(encoding="utf-8", errors="replace")
            for pat in patterns:
                for m in pat.finditer(t):
                    if not allowed.match(m.group(0)):
                        leaks.append(f"{p.relative_to(pkg)}: {m.group(0)}")
    if leaks:
        print("LEAK SCAN FAILED:\n  " + "\n  ".join(sorted(set(leaks))[:50]), file=sys.stderr)
        shutil.rmtree(pkg, ignore_errors=True)   # never leave a partial package behind
        return 3

    # 5. manifest + checksums
    entries = sorted(p for p in pkg.rglob("*") if p.is_file())
    (pkg / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(pkg)}\n" for p in entries))
    (pkg / "MANIFEST.txt").write_text(f"{name}\nfiles: {len(entries) + 1}\n\n" + "".join(
        f"{p.stat().st_size:>10}  {p.relative_to(pkg)}\n" for p in entries) + f"{'':>10}  SHA256SUMS\n")

    # 6. zip (deterministic order)
    z = args.out / f"{name}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                zi = zipfile.ZipInfo.from_file(p, f"{name}/{p.relative_to(pkg)}")
                with p.open("rb") as fh:
                    zf.writestr(zi, fh.read(), zipfile.ZIP_DEFLATED)
    mapping["zip"] = {"path": str(z), "sha256": hashlib.sha256(z.read_bytes()).hexdigest()}
    priv = args.out / f"PRIVATE_MAPPING-{version}-{stamp}.json"
    priv.write_text(json.dumps(mapping, indent=2) + "\n")
    print(json.dumps({"package": str(pkg), "zip": str(z), "zip_sha256": mapping["zip"]["sha256"],
                      "private_mapping": str(priv), "files": len(entries) + 1}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
