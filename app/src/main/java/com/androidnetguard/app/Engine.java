package com.androidnetguard.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import com.androidnetguard.app.core.CancelToken;
import com.androidnetguard.app.core.Clock;
import com.androidnetguard.app.core.Endpoint;
import com.androidnetguard.app.core.EvidenceSink;
import com.androidnetguard.app.core.ProbeResult;
import com.androidnetguard.app.core.RestartStore;
import com.androidnetguard.app.core.RunParams;
import com.androidnetguard.app.core.TestDef;
import com.androidnetguard.app.core.TestRunner;

import org.json.JSONObject;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Process-wide owner of test execution. Lives in the Application, so screen
 * rotation (Activity recreation) never interrupts or loses a run.
 */
final class Engine {
    static final String TAG = "ANG-Engine";

    /** Immutable UI snapshot. */
    static final class Snapshot {
        final boolean running;
        final String runId, testId, progress, prompt, lastResult;
        final int done, total;

        Snapshot(boolean running, String runId, String testId, String progress, int done, int total,
                 String prompt, String lastResult) {
            this.running = running; this.runId = runId; this.testId = testId; this.progress = progress;
            this.done = done; this.total = total; this.prompt = prompt; this.lastResult = lastResult;
        }
    }

    private static Engine instance;

    static synchronized Engine get(Context ctx) {
        if (instance == null) instance = new Engine(ctx.getApplicationContext());
        return instance;
    }

    final String processId = UUID.randomUUID().toString();
    final String appVersion = BuildConfig.VERSION_NAME;
    final AndroidNetView net;
    private final FileEvidence evidence;
    private final RestartStore restartStore;
    private final SharedPreferences config;
    private final TestRunner runner;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService runExec = Executors.newSingleThreadExecutor();
    private final ExecutorService auxExec = Executors.newSingleThreadExecutor();
    private final List<Runnable> listeners = new CopyOnWriteArrayList<>();

    private volatile Snapshot snap = new Snapshot(false, null, null, "Idle", 0, 0, null, null);
    private volatile CancelToken currentTok;
    private volatile String activeParams;
    private volatile String connectionStatus = "No health check yet.";
    private volatile List<HistoryModel.RunSummary> history = new ArrayList<>();

    private final Clock clock = new Clock() {
        @Override public long monoNs() { return SystemClock.elapsedRealtimeNanos(); }
        @Override public String wallTime() { return Instant.now().toString(); }
    };

    private Engine(Context app) {
        net = new AndroidNetView(app);
        evidence = new FileEvidence(app.getFilesDir());
        restartStore = new PrefsRestartStore(app);
        config = app.getSharedPreferences("config", Context.MODE_PRIVATE);
        runner = new TestRunner(new HttpProbe(), net, clock, evidence, restartStore, new TestRunner.Listener() {
            @Override public void progress(String runId, String text, int done, int total) {
                Snapshot s = snap;
                publish(new Snapshot(s.running, runId, s.testId, text, done, total, s.prompt, s.lastResult));
            }
            @Override public void prompt(String runId, String text) {
                Snapshot s = snap;
                publish(new Snapshot(s.running, s.runId, s.testId, s.progress, s.done, s.total, text, s.lastResult));
            }
        }, processId, appVersion);

        runner.setTelemetrySource(new WifiTelemetry(app));   // opt-in: used only by wifi_observation

        net.addListener(t -> {
            Map<String, Object> e = baseEvent("transport_changed", snap.running ? snap.runId : null);
            e.put("transport", t);
            emitQuietly(e);
            notifyListeners();
        });
        auxExec.execute(this::startup);
    }

    // ---------------------------------------------------------------- config
    String usbEndpoint() { return config.getString("usb_endpoint", "http://127.0.0.1:8765"); }
    String wifiEndpoint() { return config.getString("wifi_endpoint", ""); }

    void saveEndpoints(String usb, String wifi) {
        config.edit().putString("usb_endpoint", usb.trim()).putString("wifi_endpoint", wifi.trim()).apply();
    }

    // ---------------------------------------------------------------- listeners
    void addListener(Runnable r) { listeners.add(r); }
    void removeListener(Runnable r) { listeners.remove(r); }
    Snapshot snapshot() { return snap; }
    String connectionStatus() { return connectionStatus; }
    List<HistoryModel.RunSummary> history() { return history; }

    private void publish(Snapshot s) {
        snap = s;
        notifyListeners();
    }

    private void notifyListeners() {
        main.post(() -> { for (Runnable r : listeners) r.run(); });
    }

    // ---------------------------------------------------------------- runs
    /** Returns null if started, or a user-readable reason it was not. */
    synchronized String start(TestDef def, RunParams params, Endpoint ep) {
        return start(def, params, ep, "ui");
    }

    synchronized String start(TestDef def, RunParams params, Endpoint ep, String trigger) {
        if (snap.running) return "A test is already running.";
        CancelToken tok = new CancelToken();
        currentTok = tok;
        String runId = TestRunner.newId();
        activeParams = def == TestDef.WIFI_SEQUENCE
                ? "count=" + params.count + ", interval=" + params.intervalMs + " ms"
                : def == TestDef.WIFI_OBSERVATION
                ? "duration=" + params.durationS + " s, sample every " + params.sampleIntervalMs
                        + " ms, probe every " + params.probeIntervalMs + " ms, label=" + params.label
                : "fixed (no count/interval parameters)";
        publish(new Snapshot(true, runId, def.id, "Starting " + def.title + "…", 0, 0, null, snap.lastResult));
        runExec.execute(() -> complete(def.id, runner.run(def, params, ep, tok, trigger, runId)));
        return null;
    }

    /** Parameters of the active run as actually passed to the runner (null when idle). */
    String activeParams() { return activeParams; }

    /**
     * Records the user's Cancel tap immediately (timestamp taken on the UI thread at tap time),
     * then signals the runner. The runner separately records run_cancelled when it has stopped.
     */
    void cancel(String source) {
        CancelToken t = currentTok;
        Snapshot s = snap;
        Map<String, Object> e = baseEvent("cancel_requested", s.running ? s.runId : null);
        e.put("test_id", s.testId);
        e.put("source", source);
        e.put("run_in_progress", s.running);
        e.put("token_present", t != null);
        e.put("already_cancelled", t != null && t.isCancelled());
        e.put("progress_text", s.progress);
        auxExec.execute(() -> emitQuietly(e));
        // Cancel hooks disconnect sockets; run them off the UI thread. The latch is released
        // immediately either way, so the runner's waits end without delay.
        if (t != null) new Thread(t::cancel, "ang-cancel").start();
    }

    private void complete(String testId, TestRunner.Outcome o) {
        currentTok = null;
        activeParams = null;
        String line = testId + " → " + o.status.wire + " (app-side, provisional): " + o.reason;
        Log.i(TAG, "run_id=" + o.runId + " " + line);
        publish(new Snapshot(false, o.runId, testId, "Finished", 1, 1, null, line));
        refreshHistory();
    }

    // ---------------------------------------------------------------- startup / recovery
    private void startup() {
        Map<String, Object> e = baseEvent("process_started", null);
        e.put("observed_transport", net.transport());
        emitQuietly(e);

        RestartStore.PendingRestart pending = restartStore.load();
        try {
            Set<String> unfinished = new LinkedHashSet<>();
            Map<String, String> testOf = new LinkedHashMap<>();
            for (String l : evidence.readLines()) {
                JSONObject j;
                try { j = new JSONObject(l); } catch (Exception ex) { continue; }
                String type = j.optString("event"), run = j.optString("run_id", "");
                if (run.isEmpty() || j.isNull("run_id")) continue;
                // Only runs started by an EARLIER process can have been orphaned; a run this process
                // has just started (e.g. adb trigger at cold start) must not be closed by recovery.
                if (type.equals("run_started") && !processId.equals(j.optString("process_id"))) {
                    unfinished.add(run);
                    testOf.put(run, j.optString("test_id"));
                }
                if (type.equals("run_finished")) unfinished.remove(run);
            }
            if (pending != null) unfinished.remove(pending.runId);
            for (String run : unfinished) {
                Map<String, Object> f = baseEvent("run_finished", run);
                f.put("test_id", testOf.get(run));
                f.put("status", "execution_error");
                f.put("status_scope", "app_provisional");
                f.put("reason", "app process ended before the run finished (recorded by the next process)");
                emitQuietly(f);
            }
        } catch (Exception ex) {
            Log.w(TAG, "startup recovery failed", ex);
        }

        if (pending != null) {
            synchronized (this) {
                CancelToken tok = new CancelToken();
                currentTok = tok;
                activeParams = "fixed (no count/interval parameters)";
                publish(new Snapshot(true, pending.runId, TestDef.APP_RESTART.id,
                        "Restart observed; resuming app_restart test…", 1, 3, null, snap.lastResult));
                runExec.execute(() -> complete(TestDef.APP_RESTART.id, runner.resumeRestart(pending, tok)));
            }
        }
        refreshHistory();
    }

    /** Records Activity (re)creation, e.g. after rotation, linked to the running run if any. */
    void recordActivityCreated(String orientation, boolean restoredState) {
        Snapshot s = snap;
        Map<String, Object> e = baseEvent("activity_created", s.running ? s.runId : null);
        e.put("orientation", orientation);
        e.put("restored_state", restoredState);
        e.put("run_in_progress", s.running);
        auxExec.execute(() -> emitQuietly(e));
    }

    // ---------------------------------------------------------------- ad-hoc health check
    void healthCheck(String label, Endpoint ep) {
        connectionStatus = "Checking " + label + " " + ep + " …";
        notifyListeners();
        auxExec.execute(() -> {
            String rid = TestRunner.newId();
            String before = net.transport();
            long t0 = clock.monoNs();
            ProbeResult pr = new HttpProbe().health(ep, new CancelToken());
            long ms = (clock.monoNs() - t0) / 1_000_000L;
            Map<String, Object> ev = baseEvent("adhoc_health_check", null);
            ev.put("request_id", rid);
            ev.put("url", ep.url("/health"));
            ev.put("outcome", pr.outcome);
            ev.put("http_status", pr.httpStatus);
            ev.put("error", pr.error);
            ev.put("elapsed_ms", ms);
            ev.put("observed_transport", before);
            ev.put("observed_transport_after", net.transport());
            emitQuietly(ev);
            connectionStatus = label + " " + ep + ": " + (pr.ok() ? "reachable (health ok)" : pr.outcome)
                    + (pr.httpStatus > 0 ? " HTTP " + pr.httpStatus : "") + ", " + ms + " ms"
                    + (pr.error != null ? "\n" + pr.error : "")
                    + "\nObserved transport during check: " + before;
            notifyListeners();
        });
    }

    // ---------------------------------------------------------------- history
    void refreshHistory() {
        auxExec.execute(() -> {
            try {
                history = HistoryModel.build(evidence.readLines(), 30);
            } catch (Exception e) {
                Log.w(TAG, "history read failed", e);
            }
            notifyListeners();
        });
    }

    // ---------------------------------------------------------------- events
    private Map<String, Object> baseEvent(String type, String runId) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("schema", TestRunner.SCHEMA);
        e.put("event_id", UUID.randomUUID().toString());
        e.put("event", type);
        e.put("wall_time", clock.wallTime());
        e.put("mono_ns", clock.monoNs());
        e.put("process_id", processId);
        e.put("app_version", appVersion);
        e.put("run_id", runId);
        return e;
    }

    private void emitQuietly(Map<String, Object> e) {
        try {
            evidence.emit(e);
        } catch (EvidenceSink.EvidenceException ex) {
            Log.w(TAG, "evidence write failed: " + ex.getMessage());
        }
    }
}
