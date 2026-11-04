package com.androidnetguard.app.core;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Executes one connectivity/reliability test, emitting raw evidence events.
 * The status it returns is PROVISIONAL (app evidence only); the dashboard
 * re-derives the final status from raw app + server evidence.
 *
 * Not thread-safe: run on a single background thread.
 */
public final class TestRunner {

    public static final String SCHEMA = "ang.app.event/1";
    public static final String MESSAGE_TEXT = "AndroidNetGuard connectivity check (harmless test data)";

    public static final long CONNECTIVITY_RUN_LIMIT_MS = 30_000;
    public static final long PER_REQUEST_BUDGET_MS = 10_000;      // 5 s connect + 5 s read
    public static final long MAX_SEQUENCE_RUN_MS = 10 * 60_000;
    public static final long RESTART_WAIT_MS = 5 * 60_000;
    public static final long POST_RESTART_NET_WAIT_MS = 15_000;
    public static final long LOSS_WAIT_MS = 120_000;
    public static final long RESTORE_WAIT_MS = 180_000;
    public static final long SETTLE_MS = 5_000;
    public static final int RECOVERY_ATTEMPTS = 3;
    public static final long RECOVERY_GAP_MS = 3_000;

    public interface Listener {
        void progress(String runId, String text, int done, int total);
        void prompt(String runId, String text);   // null text clears the prompt
    }

    public static final class Outcome {
        public final String runId;
        public final Status status;
        public final String reason;

        Outcome(String runId, Status status, String reason) {
            this.runId = runId;
            this.status = status;
            this.reason = reason;
        }
    }

    private final Probe probe;
    private final NetView net;
    private final Clock clock;
    private final EvidenceSink sink;
    private final RestartStore restartStore;
    private final Listener listener;
    private final String processId;
    private final String appVersion;
    private volatile TelemetrySource telemetry;   // optional; only used by WIFI_OBSERVATION

    public void setTelemetrySource(TelemetrySource t) { this.telemetry = t; }

    public TestRunner(Probe probe, NetView net, Clock clock, EvidenceSink sink, RestartStore restartStore,
                      Listener listener, String processId, String appVersion) {
        this.probe = probe;
        this.net = net;
        this.clock = clock;
        this.sink = sink;
        this.restartStore = restartStore;
        this.listener = listener;
        this.processId = processId;
        this.appVersion = appVersion;
    }

    public static String newId() { return UUID.randomUUID().toString(); }

    // ------------------------------------------------------------------ run state
    private final class Run {
        final String runId;
        final TestDef def;
        final Endpoint ep;
        final RunParams params;
        final long startNs;
        final long deadlineNs;
        final CancelToken tok;
        int stepIndex;
        int done;
        int total;
        Status status;
        String reason;

        Run(String runId, TestDef def, Endpoint ep, RunParams params, long startNs, long limitMs, CancelToken tok) {
            this.runId = runId;
            this.def = def;
            this.ep = ep;
            this.params = params;
            this.startNs = startNs;
            this.deadlineNs = startNs + limitMs * 1_000_000L;
            this.tok = tok;
        }

        /** Records a status; keeps the more severe one and the first reason given at that severity. */
        void mark(Status s, String why) {
            Status w = Status.worst(status, s);
            if (w != status) {
                status = w;
                reason = why;
            }
        }

        boolean overDeadline() { return clock.monoNs() > deadlineNs; }
    }

    private static final class Cancelled extends Exception { }

    static long runLimitMs(TestDef def, RunParams p) {
        switch (def) {
            case WIFI_SEQUENCE:
                long ms = (long) p.count * (p.intervalMs + PER_REQUEST_BUDGET_MS) + 2 * PER_REQUEST_BUDGET_MS;
                return Math.min(ms, MAX_SEQUENCE_RUN_MS);
            case APP_RESTART:
                return RESTART_WAIT_MS + 3 * PER_REQUEST_BUDGET_MS + POST_RESTART_NET_WAIT_MS;
            case WIFI_RECONNECT:
                return LOSS_WAIT_MS + RESTORE_WAIT_MS + SETTLE_MS
                        + (RECOVERY_ATTEMPTS + 2) * PER_REQUEST_BUDGET_MS + RECOVERY_ATTEMPTS * RECOVERY_GAP_MS;
            case WIFI_OBSERVATION:
                // session duration + one blocked request at the end + start/end health checks
                return p.durationS * 1000L + 3 * PER_REQUEST_BUDGET_MS;
            default:
                return CONNECTIVITY_RUN_LIMIT_MS;
        }
    }

    // ------------------------------------------------------------------ public entry points

    /** Runs a test to completion (or until cancelled / the process dies during an app-restart wait). */
    public Outcome run(TestDef def, RunParams params, Endpoint ep, CancelToken tok) {
        return run(def, params, ep, tok, "ui");
    }

    /** @param trigger how the run was started: "ui" or "adb_intent" (debug builds only). */
    public Outcome run(TestDef def, RunParams params, Endpoint ep, CancelToken tok, String trigger) {
        return run(def, params, ep, tok, trigger, newId());
    }

    /** @param runId pre-generated so the caller can link UI events (e.g. cancel_requested) to the run. */
    public Outcome run(TestDef def, RunParams params, Endpoint ep, CancelToken tok, String trigger, String runId) {
        Run r = new Run(runId, def, ep, params, clock.monoNs(), runLimitMs(def, params), tok);
        try {
            Map<String, Object> e = base("run_started", r);
            e.put("test_id", def.id);
            e.put("test_mode", def.mode.wire);
            e.put("endpoint", ep.base());
            e.put("params", params(def, params));
            e.put("run_limit_ms", runLimitMs(def, params));
            e.put("observed_transport", net.transport());
            e.put("trigger", trigger);
            sink.emit(e);

            if (def.mode == TestDef.Mode.USB && !ep.isLoopback()) {
                r.mark(Status.EXECUTION_ERROR, "USB mode requires a loopback endpoint (adb reverse); got " + ep.host);
            } else if (def.mode == TestDef.Mode.WIFI && ep.isLoopback()) {
                r.mark(Status.EXECUTION_ERROR, "Wi-Fi mode requires the collector's LAN address, not loopback");
            } else {
                switch (def) {
                    case USB_CONNECTIVITY: connectivity(r, false); break;
                    case WIFI_CONNECTIVITY: connectivity(r, true); break;
                    case WIFI_SEQUENCE: sequence(r); break;
                    case APP_RESTART: restartPhaseA(r); break;
                    case WIFI_RECONNECT: reconnect(r); break;
                    case WIFI_OBSERVATION: observation(r); break;
                }
            }
        } catch (Cancelled c) {
            r.mark(Status.INCONCLUSIVE, "cancelled by user");
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            r.mark(Status.INCONCLUSIVE, "interrupted");
        } catch (EvidenceSink.EvidenceException ee) {
            r.mark(Status.EXECUTION_ERROR, "evidence could not be written: " + ee.getMessage());
        } catch (Exception other) {
            r.mark(Status.EXECUTION_ERROR, "unexpected error: " + other);
        }
        return finish(r);
    }

    /** Called in a NEW process when an app-restart wait was pending. */
    public Outcome resumeRestart(RestartStore.PendingRestart p, CancelToken tok) {
        restartStore.clear();   // never resume twice, even if this process also dies
        Endpoint ep;
        try {
            ep = Endpoint.parse(p.endpoint);
        } catch (IllegalArgumentException e) {
            ep = null;
        }
        Run r = new Run(p.runId, TestDef.APP_RESTART, ep, RunParams.defaults(), p.runStartNs,
                runLimitMs(TestDef.APP_RESTART, RunParams.defaults()), tok);
        r.stepIndex = 2;
        try {
            Map<String, Object> e = base("run_resumed", r);
            e.put("previous_process_id", p.processId);
            e.put("observed_transport", net.transport());
            sink.emit(e);

            long now = clock.monoNs();
            if (ep == null) {
                stepFinished(r, p.stepId, Status.EXECUTION_ERROR, "stored endpoint invalid", false);
                r.mark(Status.EXECUTION_ERROR, "stored endpoint invalid");
            } else if (now < p.waitStartNs) {
                stepFinished(r, p.stepId, Status.INCONCLUSIVE, "monotonic clock went backwards: device likely rebooted", false);
                r.mark(Status.INCONCLUSIVE, "device appears to have rebooted during the restart window");
            } else if (now > p.deadlineNs) {
                stepFinished(r, p.stepId, Status.INCONCLUSIVE, "restart observed after the 5-minute window", false);
                r.mark(Status.INCONCLUSIVE, "app was restarted after the 5-minute window");
            } else if (processId.equals(p.processId)) {
                stepFinished(r, p.stepId, Status.INCONCLUSIVE, "same process; no restart observed", false);
                r.mark(Status.INCONCLUSIVE, "no new process observed");
            } else {
                Map<String, Object> f = stepFinishedEvent(r, p.stepId, Status.PASSED,
                        "new app process observed", false);
                f.put("previous_process_id", p.processId);
                f.put("wait_ms", (now - p.waitStartNs) / 1_000_000L);
                sink.emit(f);
                listener.prompt(r.runId, null);

                String stepId = stepStarted(r, "post_restart_message",
                        "Send a test message from the new process", POST_RESTART_NET_WAIT_MS + PER_REQUEST_BUDGET_MS);
                listener.progress(r.runId, "Waiting for Wi-Fi after restart…", 2, 3);
                boolean wifi = net.awaitTransport(NetView.WIFI::equals, POST_RESTART_NET_WAIT_MS, tok);
                checkCancel(r);
                if (!wifi) {
                    stepFinished(r, stepId, Status.INCONCLUSIVE, "observed transport is '" + net.transport() + "', not wifi", false);
                    r.mark(Status.INCONCLUSIVE, "no Wi-Fi within 15 s after restart");
                } else {
                    Attempt a = message(r, stepId, 1);
                    Status s = evaluateRequired(a, true);
                    stepFinished(r, stepId, s, a.describe(), false);
                    r.mark(s, "post-restart message: " + a.describe());
                }
            }
        } catch (Cancelled c) {
            r.mark(Status.INCONCLUSIVE, "cancelled by user");
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            r.mark(Status.INCONCLUSIVE, "interrupted");
        } catch (EvidenceSink.EvidenceException ee) {
            r.mark(Status.EXECUTION_ERROR, "evidence could not be written: " + ee.getMessage());
        } catch (Exception other) {
            r.mark(Status.EXECUTION_ERROR, "unexpected error: " + other);
        }
        return finish(r);
    }

    // ------------------------------------------------------------------ tests

    private void connectivity(Run r, boolean requireWifi) throws Exception {
        r.total = 2;
        String step = stepStarted(r, "health", "Collector health check", PER_REQUEST_BUDGET_MS);
        Attempt h = health(r, step);
        if (requireWifi && !h.wifiThroughout()) {
            stepFinished(r, step, Status.INCONCLUSIVE, "observed transport " + h.transports() + ", not wifi", false);
            r.mark(Status.INCONCLUSIVE, "observed transport " + h.transports() + ", not exactly wifi");
            return;
        }
        if (!h.result.ok()) {
            Status s = executionProblem(h.result) ? Status.EXECUTION_ERROR : Status.FAILED;
            stepFinished(r, step, s, h.describe(), false);
            r.mark(s, "health check: " + h.describe());
            return;
        }
        stepFinished(r, step, Status.PASSED, h.describe(), false);
        progress(r, "Health ok; sending test message…");
        checkCancel(r);

        step = stepStarted(r, "message", "Send one test message", PER_REQUEST_BUDGET_MS);
        Attempt m = message(r, step, 1);
        Status s = evaluateRequired(m, requireWifi);
        stepFinished(r, step, s, m.describe(), false);
        r.mark(s, "message: " + m.describe());
    }

    private void sequence(Run r) throws Exception {
        int n = r.params.count;
        r.total = n + 1;
        String step = stepStarted(r, "health", "Collector health check", PER_REQUEST_BUDGET_MS);
        Attempt h = health(r, step);
        if (!h.wifiThroughout()) {
            stepFinished(r, step, Status.INCONCLUSIVE, "observed transport " + h.transports() + ", not wifi", false);
            r.mark(Status.INCONCLUSIVE, "transport not wifi before sequence");
            return;
        }
        if (!h.result.ok()) {
            Status s = executionProblem(h.result) ? Status.EXECUTION_ERROR : Status.FAILED;
            stepFinished(r, step, s, h.describe(), false);
            r.mark(s, "health check: " + h.describe());
            return;
        }
        stepFinished(r, step, Status.PASSED, h.describe(), false);

        int acked = 0;
        for (int i = 1; i <= n; i++) {
            checkCancel(r);
            if (r.overDeadline()) {
                r.mark(Status.INCONCLUSIVE, "run time limit reached after " + (i - 1) + " of " + n + " messages");
                return;
            }
            step = stepStarted(r, "message", "Sequence message " + i + " of " + n, PER_REQUEST_BUDGET_MS);
            Attempt m = message(r, step, i);
            Status s = evaluateRequired(m, true);
            if (s == Status.PASSED) acked++;
            stepFinished(r, step, s, m.describe(), false);
            r.mark(s, "message " + i + ": " + m.describe());
            progress(r, acked + " of " + i + " acknowledged");
            if (s == Status.INCONCLUSIVE) return;   // transport changed: stop; result can't be attributed
            if (i < n && clock.sleep(r.params.intervalMs, r.tok)) throw new Cancelled();
        }
    }

    private void restartPhaseA(Run r) throws Exception {
        r.total = 3;
        String step = stepStarted(r, "baseline_message", "Baseline test message before restart", PER_REQUEST_BUDGET_MS);
        Attempt m = message(r, step, 1);
        Status s = evaluateRequired(m, true);
        stepFinished(r, step, s, m.describe(), false);
        if (s != Status.PASSED) {
            r.mark(Status.INCONCLUSIVE, "baseline not acknowledged (" + m.describe() + "); restart behaviour not evaluated");
            return;
        }
        checkCancel(r);

        step = stepStarted(r, "wait_restart", "Wait for the app to be closed and reopened", RESTART_WAIT_MS);
        long waitStart = clock.monoNs();
        restartStore.save(new RestartStore.PendingRestart(r.runId, step, processId, r.ep.base(),
                r.startNs, waitStart, waitStart + RESTART_WAIT_MS * 1_000_000L));
        prompt(r, step, "Close AndroidNetGuard completely (swipe it away in Recents), then open it again. "
                + "Waiting up to 5 minutes.");
        progress(r, "Waiting for app restart…");
        boolean cancelled = clock.sleep(RESTART_WAIT_MS, r.tok);   // Clock: testable without a real 5-min wait
        // Still the same process: no restart happened.
        restartStore.clear();
        listener.prompt(r.runId, null);
        if (cancelled) {
            stepFinished(r, step, Status.INCONCLUSIVE, "cancelled while waiting for restart", false);
            throw new Cancelled();
        }
        stepFinished(r, step, Status.INCONCLUSIVE, "app was not restarted within 5 minutes", false);
        r.mark(Status.INCONCLUSIVE, "app was not restarted within 5 minutes");
    }

    private void reconnect(Run r) throws Exception {
        r.total = 6;
        String step = stepStarted(r, "baseline_message", "Baseline test message on Wi-Fi", PER_REQUEST_BUDGET_MS);
        Attempt m = message(r, step, 1);
        Status s = evaluateRequired(m, true);
        stepFinished(r, step, s, m.describe(), false);
        if (s != Status.PASSED) {
            r.mark(Status.INCONCLUSIVE, "baseline not acknowledged (" + m.describe() + "); reconnect not evaluated");
            return;
        }
        checkCancel(r);

        step = stepStarted(r, "wait_wifi_loss", "Wait for you to turn Wi-Fi off", LOSS_WAIT_MS);
        prompt(r, step, "Turn Wi-Fi OFF now (Quick Settings or Settings). The app will not change it. Waiting up to 2 minutes.");
        boolean lost = net.awaitTransport(t -> !NetView.WIFI.equals(t), LOSS_WAIT_MS, r.tok);
        listener.prompt(r.runId, null);
        checkCancel(r);
        if (!lost) {
            stepFinished(r, step, Status.INCONCLUSIVE, "Wi-Fi loss not observed within 2 minutes", false);
            r.mark(Status.INCONCLUSIVE, "Wi-Fi loss not observed within 2 minutes");
            return;
        }
        stepFinished(r, step, Status.PASSED, "observed transport changed to '" + net.transport() + "'", false);
        progress(r, "Wi-Fi off observed");

        step = stepStarted(r, "outage_message", "Informational: one attempt while Wi-Fi is off", PER_REQUEST_BUDGET_MS);
        Attempt o = message(r, step, 2);
        stepFinished(r, step, Status.PASSED, "observation recorded (informational): " + o.describe(), true);
        checkCancel(r);

        step = stepStarted(r, "wait_wifi_restore", "Wait for you to turn Wi-Fi back on", RESTORE_WAIT_MS);
        prompt(r, step, "Turn Wi-Fi back ON now. Waiting up to 3 minutes.");
        boolean back = net.awaitTransport(NetView.WIFI::equals, RESTORE_WAIT_MS, r.tok);
        listener.prompt(r.runId, null);
        checkCancel(r);
        if (!back) {
            stepFinished(r, step, Status.INCONCLUSIVE, "Wi-Fi return not observed within 3 minutes", false);
            r.mark(Status.INCONCLUSIVE, "Wi-Fi return not observed within 3 minutes");
            return;
        }
        stepFinished(r, step, Status.PASSED, "observed transport 'wifi' again", false);

        step = stepStarted(r, "settle", "Settle for 5 s after Wi-Fi returns", SETTLE_MS);
        if (clock.sleep(SETTLE_MS, r.tok)) throw new Cancelled();
        stepFinished(r, step, Status.PASSED, "waited 5 s", false);

        step = stepStarted(r, "recovery_messages", "Up to " + RECOVERY_ATTEMPTS + " recovery attempts",
                RECOVERY_ATTEMPTS * (PER_REQUEST_BUDGET_MS + RECOVERY_GAP_MS));
        Status rs = Status.FAILED;
        String why = "no acknowledged message within " + RECOVERY_ATTEMPTS + " attempts after Wi-Fi returned";
        for (int i = 1; i <= RECOVERY_ATTEMPTS; i++) {
            Attempt a = message(r, step, 2 + i);
            Status as = evaluateRequired(a, true);
            if (as == Status.PASSED) { rs = Status.PASSED; why = "recovered on attempt " + i; break; }
            if (as == Status.INCONCLUSIVE) { rs = Status.INCONCLUSIVE; why = "transport not wifi during recovery: " + a.transports(); break; }
            if (as == Status.EXECUTION_ERROR) { rs = as; why = a.describe(); break; }
            if (i < RECOVERY_ATTEMPTS && clock.sleep(RECOVERY_GAP_MS, r.tok)) throw new Cancelled();
        }
        stepFinished(r, step, rs, why, false);
        r.mark(rs, why);
    }

    /**
     * Opt-in wireless observation: bounded-rate ANDROID_WIFI_TELEMETRY sampling, periodic probe
     * messages, and a collector health check after any failed probe (to help separate server-side
     * from network-side failure). Request failures and signal changes are OBSERVATIONS; they do not
     * make the session "failed".
     */
    private void observation(Run r) throws Exception {
        TelemetrySource src = telemetry;
        if (src == null) {
            r.mark(Status.EXECUTION_ERROR, "no telemetry source configured");
            return;
        }
        RunParams p = r.params;
        long durNs = p.durationS * 1_000_000_000L;
        r.total = p.durationS;

        String step = stepStarted(r, "health", "Collector health check (start)", PER_REQUEST_BUDGET_MS);
        Attempt h0 = health(r, step);
        stepFinished(r, step, Status.PASSED, "observation: " + h0.describe(), true);

        String obs = stepStarted(r, "observe", "Observation: telemetry every " + p.sampleIntervalMs
                + " ms, probe every " + p.probeIntervalMs + " ms, " + p.durationS + " s", durNs / 1_000_000L);
        final String obsStep = obs;
        src.start(change -> {
            Map<String, Object> e = base("wifi_capabilities_changed", r);
            e.put("step_id", obsStep);
            e.putAll(change);
            try { sink.emit(e); } catch (EvidenceSink.EvidenceException ignored) { }
        });
        int samples = 0, rssiOk = 0, probes = 0, acks = 0, seq = 0;
        WifiReading prev = null;
        try {
            long start = clock.monoNs();
            long end = start + durNs;
            long nextSample = start, nextProbe = start;
            while (true) {
                checkCancel(r);
                long now = clock.monoNs();
                if (now >= end) break;
                if (now >= nextSample) {
                    WifiReading rd = src.poll();
                    Map<String, Object> e = base("telemetry_sample", r);
                    e.put("step_id", obs);
                    e.put("seq", ++samples);
                    e.put("observed_transport", net.transport());
                    e.putAll(Telemetry.sample(rd, prev, clock.monoNs()));
                    sink.emit(e);
                    if (Telemetry.hasRssi(e)) rssiOk++;
                    prev = rd;
                    // Next due time; if we fell behind (e.g. blocked request), skip missed slots
                    // rather than bursting. Missed slots are visible as gaps in sampled_mono_ns.
                    nextSample += p.sampleIntervalMs * 1_000_000L;
                    if (nextSample <= clock.monoNs()) nextSample = clock.monoNs() + p.sampleIntervalMs * 1_000_000L;
                }
                if (clock.monoNs() >= nextProbe && clock.monoNs() < end) {
                    Attempt a = message(r, obs, ++seq);
                    probes++;
                    if (a.result.ok()) acks++;
                    else if (!ProbeResult.CANCELLED.equals(a.result.outcome)) {
                        health(r, obs);   // follow-up: is the collector reachable right now?
                    }
                    nextProbe += p.probeIntervalMs * 1_000_000L;
                    if (nextProbe <= clock.monoNs()) nextProbe = clock.monoNs() + p.probeIntervalMs * 1_000_000L;
                }
                long elapsedS = (clock.monoNs() - start) / 1_000_000_000L;
                r.done = (int) Math.min(elapsedS, p.durationS);
                listener.progress(r.runId, "Observing [" + p.label + "]: " + samples + " samples (" + rssiOk
                        + " with RSSI), probes " + acks + "/" + probes + " acknowledged", r.done, r.total);
                long wake = Math.min(Math.min(nextSample, nextProbe), end) - clock.monoNs();
                if (wake > 0 && clock.sleep(Math.max(1, wake / 1_000_000L), r.tok)) throw new Cancelled();
            }
        } finally {
            src.stop();
        }
        String summary = samples + " telemetry samples (" + rssiOk + " with RSSI); probes " + acks + "/" + probes
                + " acknowledged";
        stepFinished(r, obs, rssiOk > 0 ? Status.PASSED : Status.INCONCLUSIVE, summary, true);

        step = stepStarted(r, "health", "Collector health check (end)", PER_REQUEST_BUDGET_MS);
        Attempt h1 = health(r, step);
        stepFinished(r, step, Status.PASSED, "observation: " + h1.describe(), true);

        if (rssiOk == 0) r.mark(Status.INCONCLUSIVE, "no valid RSSI sample obtained; " + summary);
        else r.mark(Status.PASSED, "observation completed: " + summary);
    }

    // ------------------------------------------------------------------ attempts

    private final class Attempt {
        final String requestId;
        final String before;
        final String after;
        final ProbeResult result;
        final long elapsedMs;

        Attempt(String requestId, String before, String after, ProbeResult result, long elapsedMs) {
            this.requestId = requestId;
            this.before = before;
            this.after = after;
            this.result = result;
            this.elapsedMs = elapsedMs;
        }

        boolean wifiThroughout() { return NetView.WIFI.equals(before) && NetView.WIFI.equals(after); }

        String transports() { return before.equals(after) ? "'" + before + "'" : "'" + before + "'→'" + after + "'"; }

        String describe() {
            return result.outcome + (result.httpStatus > 0 ? " (HTTP " + result.httpStatus + ")" : "")
                    + ", " + elapsedMs + " ms" + (result.error != null ? ": " + result.error : "");
        }
    }

    private static boolean executionProblem(ProbeResult pr) {
        return ProbeResult.CLEARTEXT_NOT_PERMITTED.equals(pr.outcome);
    }

    /** App-side status for a required message attempt. */
    private Status evaluateRequired(Attempt a, boolean requireWifi) {
        if (ProbeResult.CANCELLED.equals(a.result.outcome)) return Status.INCONCLUSIVE;
        if (executionProblem(a.result)) return Status.EXECUTION_ERROR;
        if (requireWifi && !a.wifiThroughout()) return Status.INCONCLUSIVE;
        return a.result.ok() ? Status.PASSED : Status.FAILED;
    }

    private Attempt health(Run r, String stepId) throws Exception {
        String rid = newId();
        String before = net.transport();
        Map<String, Object> s = base("attempt_started", r);
        s.put("step_id", stepId);
        s.put("request_id", rid);
        s.put("kind", "health");
        s.put("url", r.ep.url(Probe.HEALTH_PATH));
        s.put("observed_transport", before);
        sink.emit(s);
        long t0 = clock.monoNs();
        ProbeResult pr = probe.health(r.ep, r.tok);
        long t1 = clock.monoNs();
        return attemptFinished(r, stepId, rid, before, pr, t0, t1);
    }

    private Attempt message(Run r, String stepId, int seq) throws Exception {
        String rid = newId();
        String before = net.transport();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("request_id", rid);
        body.put("message", MESSAGE_TEXT);
        body.put("client_time", clock.wallTime());
        body.put("app_version", appVersion);
        body.put("test_label", r.def.id);
        body.put("test_id", r.def.id);
        body.put("test_mode", r.def.mode.wire);
        body.put("transport", before);
        body.put("run_id", r.runId);
        body.put("step_id", stepId);
        body.put("seq", seq);

        Map<String, Object> s = base("attempt_started", r);
        s.put("step_id", stepId);
        s.put("request_id", rid);
        s.put("kind", "message");
        s.put("seq", seq);
        s.put("url", r.ep.url(Probe.MESSAGE_PATH));
        s.put("observed_transport", before);
        s.put("body_bytes", Json.write(body).getBytes(java.nio.charset.StandardCharsets.UTF_8).length);
        sink.emit(s);
        long t0 = clock.monoNs();
        ProbeResult pr = probe.send(r.ep, body, r.tok);
        long t1 = clock.monoNs();
        // Never trust an ack carrying a different ID.
        if (ProbeResult.ACK.equals(pr.outcome) && !rid.equals(pr.ackRequestId)) {
            pr = new ProbeResult(ProbeResult.ACK_MISMATCH, pr.httpStatus, pr.ackRequestId, pr.serverTime,
                    pr.serverSessionId, "server acknowledged a different request ID");
        }
        return attemptFinished(r, stepId, rid, before, pr, t0, t1);
    }

    private Attempt attemptFinished(Run r, String stepId, String rid, String before, ProbeResult pr,
                                    long t0, long t1) throws Exception {
        String after = net.transport();
        long elapsedMs = (t1 - t0) / 1_000_000L;
        Map<String, Object> f = base("attempt_finished", r);
        f.put("step_id", stepId);
        f.put("request_id", rid);
        f.put("outcome", pr.outcome);
        f.put("http_status", pr.httpStatus);
        f.put("ack_request_id", pr.ackRequestId);
        f.put("server_time", pr.serverTime);
        f.put("server_session_id", pr.serverSessionId);
        f.put("error", pr.error);
        f.put("elapsed_ms", elapsedMs);
        f.put("start_mono_ns", t0);
        f.put("observed_transport_after", after);
        sink.emit(f);
        if (ProbeResult.CANCELLED.equals(pr.outcome)) throw new Cancelled();
        return new Attempt(rid, before, after, pr, elapsedMs);
    }

    // ------------------------------------------------------------------ events

    private Map<String, Object> base(String type, Run r) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("schema", SCHEMA);
        e.put("event_id", newId());
        e.put("event", type);
        e.put("wall_time", clock.wallTime());
        e.put("mono_ns", clock.monoNs());
        e.put("process_id", processId);
        e.put("app_version", appVersion);
        e.put("run_id", r.runId);
        e.put("test_id", r.def.id);
        return e;
    }

    private static Map<String, Object> params(TestDef def, RunParams p) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (def == TestDef.WIFI_SEQUENCE) {
            m.put("count", p.count);
            m.put("interval_ms", p.intervalMs);
        }
        if (def == TestDef.WIFI_OBSERVATION) {
            m.put("duration_s", p.durationS);
            m.put("sample_interval_ms", p.sampleIntervalMs);
            m.put("probe_interval_ms", p.probeIntervalMs);
            m.put("session_label", p.label);
        }
        return m;
    }

    private String stepStarted(Run r, String kind, String desc, long timeoutMs) throws Exception {
        String id = newId();
        Map<String, Object> e = base("step_started", r);
        e.put("step_id", id);
        e.put("step_index", ++r.stepIndex);
        e.put("step_kind", kind);
        e.put("description", desc);
        e.put("timeout_ms", timeoutMs);
        sink.emit(e);
        listener.progress(r.runId, desc, r.done, r.total);
        return id;
    }

    private Map<String, Object> stepFinishedEvent(Run r, String stepId, Status s, String reason, boolean informational) {
        Map<String, Object> e = base("step_finished", r);
        e.put("step_id", stepId);
        e.put("status", s.wire);
        e.put("reason", reason);
        e.put("informational", informational);
        return e;
    }

    private void stepFinished(Run r, String stepId, Status s, String reason, boolean informational) throws Exception {
        sink.emit(stepFinishedEvent(r, stepId, s, reason, informational));
        r.done++;
    }

    private void prompt(Run r, String stepId, String text) throws Exception {
        Map<String, Object> e = base("prompt_shown", r);
        e.put("step_id", stepId);
        e.put("text", text);
        sink.emit(e);
        listener.prompt(r.runId, text);
    }

    private void progress(Run r, String text) {
        listener.progress(r.runId, text, r.done, r.total);
    }

    private void checkCancel(Run r) throws Cancelled {
        if (r.tok.isCancelled()) throw new Cancelled();
    }

    private Outcome finish(Run r) {
        if (r.status == null) r.mark(Status.EXECUTION_ERROR, "no result recorded");
        listener.prompt(r.runId, null);
        try {
            if (r.tok.isCancelled()) {
                // Cancellation COMPLETED: the runner has stopped and will start no further requests.
                // The user's tap is recorded separately (cancel_requested) by the UI layer.
                Map<String, Object> c = base("run_cancelled", r);
                c.put("meaning", "cancellation_completed");
                sink.emit(c);
            }
            Map<String, Object> e = base("run_finished", r);
            e.put("status", r.status.wire);
            e.put("status_scope", "app_provisional");
            e.put("reason", r.reason);
            e.put("duration_ms", (clock.monoNs() - r.startNs) / 1_000_000L);
            sink.emit(e);
        } catch (EvidenceSink.EvidenceException ee) {
            if (r.status != Status.EXECUTION_ERROR) {
                r.status = Status.EXECUTION_ERROR;
                r.reason = "final evidence could not be written: " + ee.getMessage();
            }
        }
        listener.progress(r.runId, "Finished: " + r.status.wire + " — " + r.reason, r.total, r.total);
        return new Outcome(r.runId, r.status, r.reason);
    }
}
