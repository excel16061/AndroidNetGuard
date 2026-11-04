# Live dashboard and saved reports

All dashboard code lives in this project, under `dashboard/`:

| File | Role |
|---|---|
| `dashboard.py` | the live server, all page templates (server-side HTML), and the stylesheet (`CSS`) |
| `wireless.py` | wireless analysis, chart CSS, and SVG chart generation |
| `snapshot.py` | the saved-report generator |

There are no external templates, CDNs, fonts or JavaScript.

## Where things live

| Path | What | Git |
|---|---|---|
| `evidence/` | **private raw evidence**: collector sessions, device imports, CSI captures, private derived reports | ignored |
| `collector/data/` | private legacy smoke-test logs | ignored |
| `reports/public/<UTC>_snapshot/` | **public-ready saved results** (sanitized) | tracked |
| `docs/images/` | screenshots rendered from a saved snapshot (sanitized) | tracked |

## Start the live dashboard

> **Live vs portable.** `http://127.0.0.1:8770/…` links only work on the Mac while `dashboard.py` is running; they show private, unredacted data. The portable, shareable report is the saved snapshot listed in [`reports/public/INDEX.md`](../reports/public/INDEX.md) (open its `index.html` directly).

```sh
cd AndroidNetGuard && source ./env.sh
python3 -I collector/collector.py --host <Mac LAN IP> --host 127.0.0.1 --port 8765   # terminal 1
python3 -I dashboard/dashboard.py --port 8770 --serial <adb serial>                  # terminal 2
open http://127.0.0.1:8770/            # overview, runs, import button
open http://127.0.0.1:8770/wireless    # wireless sessions and CSI input status
```

The live dashboard binds to `127.0.0.1` only. It shows private data (local addresses on run
pages), so don't screenshot it for publication. Use a snapshot instead. Stop either process with
Ctrl-C.

## Regenerate a saved report

```sh
python3 -I dashboard/snapshot.py          # writes reports/public/<UTC>_snapshot/
```

The command needs neither the collector nor the dashboard running. It reads `evidence/` and
`collector/data/` without modifying them, and writes a **new** directory each time; it refuses to
overwrite an existing one. The output contains:

* `index.html`, `wireless.html`, `runs/<run_id>.html`: static pages with relative links only, a
  local stylesheet `assets/dashboard.css`, and no scripts. Every page carries a **"SAVED RESULTS
  SNAPSHOT … Not live"** banner.
* `data.json`: sanitized derived data and the public export of every run.
* `manifest.json`: export time, analysis versions, source collector session IDs, device import
  IDs, app process IDs, run and status counts, input file fingerprints (SHA-256), and the
  checksum of every content file.
* `SHA256SUMS`, which covers all files. Verify with `cd <snapshot> && shasum -a 256 -c SHA256SUMS`.

### Sanitization

The public scope redacts addresses, ports, endpoints and URLs, device serials and models, local
paths, emails, the dashboard's CSRF token, and collector bind addresses, **including when they
appear inside free text** such as error messages. This is done in two independent layers:
1. the run export and HTML generation redact these values;
2. a final leak scan checks every output file for private IPv4 ranges, MAC addresses, home paths,
   emails, known device serials and models, the project path, and token markers.

Any hit aborts the snapshot and deletes the partial directory. This is unit-tested, including a
test that disables all redaction and checks that the leak scan still blocks the output.

### Opens without the servers

`snapshot.py` checks that every `href`/`src` is relative and resolves to a file inside the
snapshot. The screenshots in `docs/images/` were rendered straight from the snapshot files
(macOS Quick Look), not from the running dashboard.

## Screenshots

```sh
S=reports/public/<snapshot>
cd "$S" && qlmanage -t -s 1400 -o ../../../docs/images index.html wireless.html   # macOS
```

The current images:
* `snapshot-overview.png`
* `snapshot-wireless-sessions.png`
* `snapshot-run-stationary-session.png`
* `snapshot-stationary-charts.png`, `snapshot-moved-farther-charts.png`, `snapshot-disconnect-reconnect-charts.png`
  (observation sections of the saved session pages)
* `snapshot-reconnect-0.4.1-derivation.png` (derivation and checks of the 0.4.1 reconnect run)
* `snapshot-cancellation-0.4.1.png` (derivation, checks and cancellation table of the UI cancellation run)
* `snapshot-feature-verification.png` (overview: feature verified vs latest run outcome, mechanism checks)

## Versions recorded in each snapshot

* `analysis_version` (dashboard/analysis.py), currently 7 (history and reasons in `docs/ANALYSIS_CHANGELOG.md`).
* `wireless_analysis_version` (dashboard/wireless.py), currently 4 (v4: completeness and freshness reported separately). v2 reworded the validated capability
  alert, v3 attributes failures inside Wi-Fi-lost intervals to `phone_not_on_wifi`.

Which snapshot is current, and which are superseded, is recorded in `reports/public/INDEX.md`.
