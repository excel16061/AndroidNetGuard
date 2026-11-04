"""Wireless observation analysis (stdlib only): ANDROID_WIFI_TELEMETRY series, a transparent
baseline detector, request-failure attribution, and dependency-free SVG charts.

Every output is an OBSERVATION or an ANOMALY ALERT, never an "attack" or a cause. Thresholds are
documented in docs/WIRELESS_OBSERVABILITY.md and recorded in each derived output.

Clock domains: all x-positions are phone-monotonic seconds since the run's run_started event.
Collector records are attached to a probe BY REQUEST ID (correlation), never by subtracting
phone and Mac clocks.
"""

from __future__ import annotations

import html
import statistics

WIRELESS_ANALYSIS_VERSION = "4"   # v4: completeness reported separately from freshness
                                  # v2: validated alert reworded; v3: failures inside Wi-Fi-lost intervals
                                  # (Wi-Fi telemetry onLost) attributed to phone_not_on_wifi

# --- Baseline detector parameters (rationale in docs/WIRELESS_OBSERVABILITY.md) -------------
BASELINE_FRESH_SAMPLES = 5      # first N first/changed RSSI tuples define the session baseline
BASELINE_MIN_SAMPLES = 3        # fewer than this -> no baseline, no degradation alerts
ROLLING_WINDOW = 3              # rolling median over the last N fresh samples
DEGRADATION_DB = 10             # rolling median <= baseline - 10 dB -> degradation alert
WEAK_RSSI_DBM = -75             # rolling median below -> "weak signal" observation
VERY_WEAK_RSSI_DBM = -85        # rolling median below -> "very weak signal" observation
LINK_SPEED_DROP_RATIO = 0.5     # rolling median link speed <= 50 % of baseline -> alert
CONSECUTIVE_PROBE_FAILURES = 2  # >= 2 failed probes in a row -> connectivity disruption alert

PARAMETERS = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))}

E = html.escape


def _rel_s(e, origin):
    return round((e["mono_ns"] - origin) / 1e9, 3)


def _val(sample, key):
    f = sample.get(key) or {}
    return f.get("value") if f.get("status") == "ok" else None


def extract(run) -> dict:
    """Pulls the observation series out of one run (analysis.Run)."""
    events = sorted(run.events, key=lambda e: e.get("mono_ns") or 0)
    origin = run.started["mono_ns"] if run.started else (events[0]["mono_ns"] if events else 0)
    samples, changes, transport = [], [], []
    for e in events:
        t = e.get("event")
        if t == "telemetry_sample":
            samples.append({
                "t_s": _rel_s(e, origin), "seq": e.get("seq"), "freshness": e.get("freshness"),
                "source": e.get("source"),
                "rssi_dbm": _val(e, "rssi_dbm"), "rssi_status": (e.get("rssi_dbm") or {}).get("status"),
                "frequency_mhz": _val(e, "frequency_mhz"),
                "link_speed_mbps": _val(e, "link_speed_mbps"),
                "link_status": (e.get("link_speed_mbps") or {}).get("status"),
                "tx_link_speed_mbps": _val(e, "tx_link_speed_mbps"),
                "rx_link_speed_mbps": _val(e, "rx_link_speed_mbps"),
                "wifi_standard": (e.get("wifi_standard") or {}).get("label"),
                "callback_status": (e.get("callback") or {}).get("status"),
                "callback_age_ms": (e.get("callback") or {}).get("age_ms"),
                "observed_transport": e.get("observed_transport"),
            })
        elif t == "wifi_capabilities_changed":
            changes.append({"t_s": _rel_s(e, origin), "validated": e.get("validated"),
                            "transports": e.get("transports"), "lost": bool(e.get("wifi_network_lost")),
                            "signal_strength_dbm": e.get("signal_strength_dbm")})
        elif t == "transport_changed":
            transport.append({"t_s": _rel_s(e, origin), "transport": e.get("transport")})

    # Probes = message attempts; follow-up health = the next health attempt after a failed probe.
    atts = sorted(run.attempts.values(), key=lambda a: a.started["mono_ns"])
    probes, healths = [], []
    for i, a in enumerate(atts):
        rec = {"t_s": _rel_s(a.started, origin), "request_id": a.request_id, "kind": a.kind, "seq": a.seq,
               "outcome": a.outcome, "ok": a.outcome in ("ack", "health_ok"),
               "http_status": a.finished.get("http_status") if a.finished else None,
               "elapsed_ms": a.finished.get("elapsed_ms") if a.finished else None,
               "error": a.finished.get("error") if a.finished else None,
               "transport_before": a.transports[0], "transport_after": a.transports[1],
               "server_records": len(a.server), "correlation": a.correlation}
        (probes if a.kind == "message" else healths).append(rec)
        if a.kind == "message" and not rec["ok"]:
            nxt = next((b for b in atts[i + 1:] if b.kind == "health"), None)
            rec["follow_up_health"] = nxt.outcome if nxt else None
    duration = None
    if run.finished:
        duration = round((run.finished["mono_ns"] - origin) / 1e9, 3)
    return {"samples": samples, "capability_changes": changes, "transport_changes": transport,
            "probes": probes, "health_checks": healths, "duration_s": duration,
            "params": (run.started or {}).get("params") or {}}


def wifi_lost_intervals(series: dict) -> list:
    """[start, end] (phone-monotonic s) where the Wi-Fi telemetry callback reported the network lost.
    end = next capability change showing Wi-Fi again, or None if it never came back in the run."""
    out, start = [], None
    for c in series["capability_changes"]:
        if c["lost"] and start is None:
            start = c["t_s"]
        elif not c["lost"] and start is not None and c.get("transports") and "wifi" in c["transports"]:
            out.append([start, c["t_s"]]); start = None
    if start is not None:
        out.append([start, None])
    return out


def attribute_failure(p: dict, server_evidence_for_run: bool, wifi_lost: list = ()) -> str:
    """Most specific label the evidence supports; never a cause beyond that."""
    if p["ok"]:
        return "acknowledged"
    if p["server_records"] >= 1:
        return "delivered_not_acknowledged (collector stored it; response path or app-side)"
    if not server_evidence_for_run:
        return "missing_server_evidence (no collector records for this run were loaded)"
    if p.get("http_status") and p["http_status"] > 0:
        return f"server_responded_with_error (HTTP {p['http_status']})"
    t = p.get("t_s")
    if t is not None and any(a <= t and (b is None or t < b) for a, b in wifi_lost):
        return ("phone_not_on_wifi (Wi-Fi reported lost by the Wi-Fi telemetry callback at this time"
                + ("; the default-network transport field said 'wifi', a known app 0.4.0 defect)"
                   if p["transport_before"] == "wifi" else ")"))
    if p["transport_before"] != "wifi" or p["transport_after"] != "wifi":
        return f"phone_not_on_wifi (observed {p['transport_before']}→{p['transport_after']})"
    if p.get("follow_up_health") == "health_ok":
        return "message_failed_while_collector_reachable (follow-up health ok)"
    if p.get("follow_up_health"):
        return ("collector_unreachable_on_wifi (follow-up health also failed: collector down or LAN "
                "path failure — not distinguishable from this evidence)")
    return "not_delivered (no follow-up health check recorded)"


def _median(xs):
    return statistics.median(xs) if xs else None


def detect(series: dict) -> dict:
    """Transparent baseline rules over FRESH samples only (repeated values are not new evidence)."""
    fresh = [s for s in series["samples"] if s["freshness"] == "new_value" and s["rssi_dbm"] is not None]
    out = {"parameters": PARAMETERS, "baseline": None, "observations": [], "alerts": []}
    if len(fresh) < BASELINE_MIN_SAMPLES:
        out["observations"].append({"kind": "insufficient_data",
                                    "detail": f"{len(fresh)} first/changed RSSI tuples (< {BASELINE_MIN_SAMPLES}); no baseline"})
    else:
        base = fresh[:BASELINE_FRESH_SAMPLES]
        b_rssi = _median([s["rssi_dbm"] for s in base])
        links = [s["link_speed_mbps"] for s in base if s["link_speed_mbps"] is not None]
        b_link = _median(links)
        out["baseline"] = {"rssi_dbm": b_rssi, "link_speed_mbps": b_link, "samples": len(base),
                           "t_end_s": base[-1]["t_s"]}

        def spans(flags, kind, detail_fn):
            cur = None
            for s, flag in flags:
                if flag and cur is None:
                    cur = {"kind": kind, "start_s": s["t_s"], "end_s": s["t_s"], "samples": [s]}
                elif flag:
                    cur["end_s"] = s["t_s"]; cur["samples"].append(s)
                elif cur is not None:
                    yield cur; cur = None
            if cur is not None:
                yield cur

        rolled = []
        for i in range(ROLLING_WINDOW - 1, len(fresh)):
            win = fresh[i - ROLLING_WINDOW + 1:i + 1]
            rolled.append((fresh[i], _median([w["rssi_dbm"] for w in win]),
                           _median([w["link_speed_mbps"] for w in win if w["link_speed_mbps"] is not None])))
        for sp in spans([(s, med <= b_rssi - DEGRADATION_DB) for s, med, _ in rolled], "signal_degradation", None):
            sp["detail"] = (f"rolling median RSSI ≤ baseline {b_rssi} dBm − {DEGRADATION_DB} dB; "
                            f"min {min(x['rssi_dbm'] for x in sp.pop('samples'))} dBm")
            out["alerts"].append(sp)
        for thr, kind in ((VERY_WEAK_RSSI_DBM, "very_weak_signal"), (WEAK_RSSI_DBM, "weak_signal")):
            for sp in spans([(s, med < thr) for s, med, _ in rolled], kind, None):
                sp.pop("samples")
                sp["detail"] = f"rolling median RSSI < {thr} dBm"
                out["observations"].append(sp)
        if b_link:
            for sp in spans([(s, lk is not None and lk <= b_link * LINK_SPEED_DROP_RATIO) for s, _, lk in rolled],
                            "link_speed_drop", None):
                sp.pop("samples")
                sp["detail"] = f"rolling median link speed ≤ {int(LINK_SPEED_DROP_RATIO * 100)} % of baseline {b_link} Mbps"
                out["alerts"].append(sp)

    # Connectivity disruption (does not need an RSSI baseline).
    for c in series["transport_changes"]:
        if c["transport"] != "wifi":
            out["alerts"].append({"kind": "connectivity_disruption", "start_s": c["t_s"], "end_s": c["t_s"],
                                  "detail": f"default network transport changed to '{c['transport']}'"})
    for c in series["capability_changes"]:
        if c["lost"]:
            out["alerts"].append({"kind": "connectivity_disruption", "start_s": c["t_s"], "end_s": c["t_s"],
                                  "detail": "Wi-Fi network lost (NetworkCallback.onLost)"})
        elif c["validated"] is False:
            ok_after = [p for p in series["probes"] if p["t_s"] >= c["t_s"] and p["ok"]]
            out["alerts"].append({"kind": "internet_validation_lost", "start_s": c["t_s"], "end_s": c["t_s"],
                                  "detail": "NET_CAPABILITY_VALIDATED absent on the Wi-Fi network (Android's internet "
                                            "validation; not a LAN reachability signal"
                                            + ("; LAN probes after this point were still acknowledged)" if ok_after else ")")})
    streak = []
    for p in series["probes"] + [None]:          # None flushes a trailing streak
        if p is not None and not p["ok"]:
            streak.append(p)
            continue
        if len(streak) >= CONSECUTIVE_PROBE_FAILURES:
            out["alerts"].append({"kind": "connectivity_disruption", "start_s": streak[0]["t_s"],
                                  "end_s": streak[-1]["t_s"],
                                  "detail": f"{len(streak)} consecutive probes not acknowledged"})
        streak = []
    gaps = [s for s in series["samples"] if s["rssi_dbm"] is None]
    if gaps:
        by = {}
        for g in gaps:
            by[g["rssi_status"]] = by.get(g["rssi_status"], 0) + 1
        out["observations"].append({"kind": "missing_rssi", "detail": ", ".join(f"{k}: {v}" for k, v in sorted(by.items()))})
    out["alerts"].sort(key=lambda a: (a["start_s"] is None, a["start_s"] or 0))
    return out


def summarize(run, server_evidence_for_run: bool) -> dict:
    s = extract(run)
    d = detect(s)
    rssi = [x["rssi_dbm"] for x in s["samples"] if x["rssi_dbm"] is not None]
    fresh = [x for x in s["samples"] if x["freshness"] == "new_value"]
    lost = wifi_lost_intervals(s)
    for p in s["probes"]:
        p["attribution"] = attribute_failure(p, server_evidence_for_run, lost)
    status_counts = {}
    for x in s["samples"]:
        status_counts[x["rssi_status"]] = status_counts.get(x["rssi_status"], 0) + 1
    # Sample COMPLETENESS (did the expected sampling slots produce a record?) is reported separately
    # from telemetry FRESHNESS (did a record carry a new value or repeat the previous one?).
    p = s["params"]
    expected = (p.get("duration_s") or 0) * 1000 // max(1, p.get("sample_interval_ms") or 1)
    completeness = {"recorded": len(s["samples"]), "expected": expected or None,
                    "ratio": round(len(s["samples"]) / expected, 3) if expected else None,
                    "note": "records per expected sampling slot (duration / interval); independent of freshness"}
    freshness = {"fresh": len(fresh), "repeated": sum(1 for x in s["samples"] if x["freshness"] == "repeated"),
                 "ratio_fresh": round(len(fresh) / len(s["samples"]), 3) if s["samples"] else None,
                 "note": "Consecutive value-tuple comparison only: first/changed versus repeated. Radio measurement age and independence are unknown."}
    return {
        "wireless_analysis_version": WIRELESS_ANALYSIS_VERSION,
        "sample_completeness": completeness, "telemetry_freshness": freshness,
        "source": "ANDROID_WIFI_TELEMETRY",
        "session_label": s["params"].get("session_label"),
        "params": s["params"], "duration_s": s["duration_s"],
        "samples_total": len(s["samples"]), "samples_fresh": len(fresh),
        "samples_repeated": sum(1 for x in s["samples"] if x["freshness"] == "repeated"),
        "rssi_status_counts": status_counts,
        "rssi_dbm": {"median": _median(rssi), "min": min(rssi) if rssi else None, "max": max(rssi) if rssi else None},
        "frequency_mhz_values": sorted({x["frequency_mhz"] for x in s["samples"] if x["frequency_mhz"]}),
        "link_speed_mbps_median": _median([x["link_speed_mbps"] for x in s["samples"] if x["link_speed_mbps"] is not None]),
        "probes_total": len(s["probes"]), "probes_acknowledged": sum(1 for p in s["probes"] if p["ok"]),
        "probes_with_collector_record": sum(1 for p in s["probes"] if p["server_records"] == 1),
        "health_checks_total": len(s["health_checks"]),
        "health_checks_ok": sum(1 for h in s["health_checks"] if h["ok"]),
        "transport_changes": s["transport_changes"], "capability_changes": s["capability_changes"],
        "wifi_lost_intervals_s": lost,
        "detector": d, "series": s,
    }


# ----------------------------------------------------------------------------- SVG charts
CHART_CSS = """
.viz-root{--surface-1:#fcfcfb;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--series-1:#2a78d6;--good:#0ca30c;--critical:#d03b3b;--warning:#fab219;}
/* Light only: the surrounding dashboard has no dark theme, so charts must not switch alone. */
.viz-root{color-scheme:light}
.viz-root{background:var(--surface-1);color:var(--text-primary);padding:8px;border-radius:6px;margin:8px 0}
.viz-root svg text{fill:var(--text-secondary);font:11px -apple-system,system-ui,sans-serif}
.viz-root .t{fill:var(--text-primary);font-weight:600;font-size:12px}
.viz-root .tiles{display:flex;gap:12px;flex-wrap:wrap}
.viz-root .tile{border:1px solid var(--grid);border-radius:6px;padding:6px 10px;min-width:120px}
.viz-root .tile b{display:block;font-size:20px;color:var(--text-primary)}
.viz-root .empty{color:var(--muted);font-style:italic;padding:12px}
"""


def _scale(v, lo, hi, a, b):
    if hi == lo:
        return (a + b) / 2
    return a + (v - lo) * (b - a) / (hi - lo)


def _frame(width, height, title, x_max, y_lo, y_hi, y_ticks, y_unit, left=52, right=16, top=34, bottom=30):
    pw, ph = width - left - right, height - top - bottom
    gl, gw = left, pw                 # gridline extent
    left, pw = left + 10, pw - 20     # inset data area so edge marks don't sit on the axes
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{E(title)}">',
             f'<text class="t" x="{left}" y="15">{E(title)}</text>']
    for yt in y_ticks:
        y = _scale(yt, y_lo, y_hi, top + ph, top)
        parts.append(f'<line x1="{gl}" x2="{gl + gw}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--grid)" stroke-width="1"/>'
                     f'<text x="{gl - 6}" y="{y + 4:.1f}" text-anchor="end">{yt}{E(y_unit)}</text>')
    step = max(1, round(x_max / 6)) if x_max else 1
    xt = 0
    while xt <= (x_max or 0) + 1e-9:
        x = _scale(xt, 0, x_max or 1, left, left + pw)
        parts.append(f'<text x="{x:.1f}" y="{top + ph + 16}" text-anchor="middle">{xt:g}s</text>')
        xt += step
    parts.append(f'<line x1="{gl}" x2="{gl + gw}" y1="{top + ph}" y2="{top + ph}" stroke="var(--axis)"/>')
    parts.append(f'<text x="{gl + gw}" y="{height - 2}" text-anchor="end">phone-monotonic seconds since run start</text>')
    return parts, (left, top, pw, ph)


def _markers(parts, events, x_max, box):
    left, top, pw, ph = box
    for ev in events:
        x = _scale(ev["t_s"], 0, x_max or 1, left, left + pw)
        parts.append(f'<g><title>{E(ev["label"])} at {ev["t_s"]} s</title>'
                     f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{top + ph}" stroke="var(--muted)" '
                     f'stroke-width="1" stroke-dasharray="3 3"/>'
                     f'<rect x="{x - 4:.1f}" y="{top}" width="8" height="{ph}" fill="transparent"/></g>')


def rssi_chart(summary: dict, width=900, height=240) -> str:
    s = summary["series"]
    if not s["samples"]:
        return '<div class="viz-root"><div class="empty">No telemetry samples recorded in this run.</div></div>'
    x_max = max([x["t_s"] for x in s["samples"]] + [summary["duration_s"] or 0]) or 1
    vals = [x["rssi_dbm"] for x in s["samples"] if x["rssi_dbm"] is not None]
    lo = min(vals + [-90]) // 10 * 10 if vals else -100
    hi = 0 if not vals else min(-10, (max(vals) // 10 + 2) * 10)   # one empty tick of headroom
    parts, box = _frame(width, height, "RSSI (ANDROID_WIFI_TELEMETRY, dBm)", x_max, lo, hi,
                        list(range(int(lo), int(hi) + 1, 10)), "", )
    left, top, pw, ph = box
    det = summary["detector"]
    for a in det["alerts"]:
        if a["kind"] in ("signal_degradation", "link_speed_drop") and a["start_s"] is not None:
            x0 = _scale(a["start_s"], 0, x_max, left, left + pw)
            x1 = max(x0 + 3, _scale(a["end_s"], 0, x_max, left, left + pw))
            parts.append(f'<g><title>ALERT {E(a["kind"])}: {E(a["detail"])}</title>'
                         f'<rect x="{x0:.1f}" y="{top}" width="{x1 - x0:.1f}" height="{ph}" fill="var(--warning)" '
                         f'opacity="0.18"/></g>')
    if det["baseline"]:
        yb = _scale(det["baseline"]["rssi_dbm"], lo, hi, top + ph, top)
        parts.append(f'<line x1="{left}" x2="{left + pw}" y1="{yb:.1f}" y2="{yb:.1f}" stroke="var(--muted)" stroke-dasharray="6 4"/>'
                     f'<text x="{left + 4}" y="{yb + 14:.1f}">baseline {det["baseline"]["rssi_dbm"]} dBm</text>')
    _markers(parts, [{"t_s": c["t_s"], "label": f"transport → {c['transport']}"} for c in s["transport_changes"]]
             + [{"t_s": c["t_s"], "label": "Wi-Fi lost" if c["lost"] else f"capabilities: validated={c['validated']}"}
                for c in s["capability_changes"]], x_max, box)
    # line through ok samples; break at missing values (never interpolate across a gap)
    seg, segs = [], []
    for x in s["samples"]:
        if x["rssi_dbm"] is None:
            if seg:
                segs.append(seg)
            seg = []
        else:
            seg.append(x)
    if seg:
        segs.append(seg)
    for sg in segs:
        pts = " ".join(f'{_scale(x["t_s"], 0, x_max, left, left + pw):.1f},{_scale(x["rssi_dbm"], lo, hi, top + ph, top):.1f}' for x in sg)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"/>')
    for x in s["samples"]:
        cx = _scale(x["t_s"], 0, x_max, left, left + pw)
        if x["rssi_dbm"] is None:
            parts.append(f'<g><title>#{x["seq"]} at {x["t_s"]} s: RSSI {E(str(x["rssi_status"]))}</title>'
                         f'<text x="{cx:.1f}" y="{top + ph - 4}" text-anchor="middle" style="fill:var(--muted)">×</text>'
                         f'<rect x="{cx - 6:.1f}" y="{top + ph - 16}" width="12" height="16" fill="transparent"/></g>')
            continue
        cy = _scale(x["rssi_dbm"], lo, hi, top + ph, top)
        fill = "var(--series-1)" if x["freshness"] == "new_value" else "var(--surface-1)"
        parts.append(f'<g><title>#{x["seq"]} at {x["t_s"]} s: {x["rssi_dbm"]} dBm, {E(str(x["freshness"]))}'
                     f'{", " + str(x["frequency_mhz"]) + " MHz" if x["frequency_mhz"] else ""}'
                     f'{", " + str(x["link_speed_mbps"]) + " Mbps" if x["link_speed_mbps"] is not None else ""}</title>'
                     f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{fill}" stroke="var(--series-1)" stroke-width="2"/>'
                     f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10" fill="transparent"/></g>')
    parts.append("</svg>")
    legend = ('<div style="font-size:12px">● first/changed value &nbsp; ○ repeated value (measurement age unknown) '
              '&nbsp; × no value (status in tooltip) &nbsp; ┆ transport/capability event &nbsp; shaded = anomaly alert</div>')
    return f'<div class="viz-root">{"".join(parts)}{legend}</div>'


def duration_chart(summary: dict, width=900, height=200) -> str:
    s = summary["series"]
    reqs = s["probes"] + s["health_checks"]
    if not reqs:
        return '<div class="viz-root"><div class="empty">No requests recorded in this run.</div></div>'
    x_max = max([r["t_s"] for r in reqs] + [summary["duration_s"] or 0]) or 1
    ok_ms = [r["elapsed_ms"] for r in reqs if r["ok"] and r["elapsed_ms"] is not None]
    hi = max(ok_ms + [100])
    hi = int((hi // 100 + 1) * 100)
    ticks = list(range(0, hi + 1, max(100, hi // 4 // 100 * 100)))
    parts, box = _frame(width, height, "Request duration (phone monotonic, ms) — single observations, not a benchmark",
                        x_max, 0, hi, ticks, "")
    left, top, pw, ph = box
    for r in reqs:
        cx = _scale(r["t_s"], 0, x_max, left, left + pw)
        tip = (f'{r["kind"]}{" #" + str(r["seq"]) if r["seq"] else ""} at {r["t_s"]} s: {r["outcome"]}, '
               f'{r["elapsed_ms"]} ms, collector records {r["server_records"]}'
               + (f', {r.get("attribution")}' if r.get("attribution") and not r["ok"] else ""))
        if r["ok"]:
            cy = _scale(min(r["elapsed_ms"] or 0, hi), 0, hi, top + ph, top)
            shape = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="var(--series-1)" stroke="var(--surface-1)" stroke-width="2"/>'
                     if r["kind"] == "message" else
                     f'<rect x="{cx - 4:.1f}" y="{cy - 4:.1f}" width="8" height="8" fill="none" stroke="var(--series-1)" stroke-width="2"/>')
        else:
            cy = top + 8
            shape = (f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" font-weight="700" '
                     f'style="fill:var(--critical)">✕</text>')   # inline style: CSS text rule would override fill=
        parts.append(f'<g><title>{E(tip)}</title>{shape}<rect x="{cx - 8:.1f}" y="{top}" width="16" height="{ph}" fill="transparent"/></g>')
    parts.append("</svg>")
    legend = '<div style="font-size:12px">● message acknowledged &nbsp; □ health ok &nbsp; <span style="color:var(--critical)">✕</span> failed (top row; reason in tooltip)</div>'
    return f'<div class="viz-root">{"".join(parts)}{legend}</div>'


def tiles(summary: dict) -> str:
    def t(label, value, sub=""):
        return f'<div class="tile">{E(label)}<b>{E(str(value))}</b><span>{E(sub)}</span></div>'
    r = summary["rssi_dbm"]
    c, fr = summary["sample_completeness"], summary["telemetry_freshness"]
    comp = (f'{c["recorded"]}/{c["expected"]}' if c["expected"] else str(c["recorded"]))
    return ('<div class="viz-root"><div class="tiles">'
            + t("Sample completeness", comp,
                f'{c["ratio"]:.0%} of expected slots' if c["ratio"] is not None else "expected slots unknown")
            + t("Reported value changes", f'{fr["fresh"]} first/changed',
                f'{fr["repeated"]} repeated (measurement age unknown)')
            + t("RSSI median", f'{r["median"]} dBm' if r["median"] is not None else "n/a",
                f'min {r["min"]} · max {r["max"]}' if r["median"] is not None else "no valid RSSI")
            + t("Probes acknowledged", f'{summary["probes_acknowledged"]}/{summary["probes_total"]}',
                f'{summary["probes_with_collector_record"]} with collector record')
            + t("Health checks ok", f'{summary["health_checks_ok"]}/{summary["health_checks_total"]}')
            + t("Transport changes", len(summary["transport_changes"]))
            + t("Anomaly alerts", len(summary["detector"]["alerts"]))
            + "</div></div>")
