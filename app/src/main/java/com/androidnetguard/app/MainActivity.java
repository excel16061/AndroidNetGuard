package com.androidnetguard.app;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.TextView;

import com.androidnetguard.app.core.Endpoint;
import com.androidnetguard.app.core.RunParams;
import com.androidnetguard.app.core.TestDef;

import java.util.List;

public class MainActivity extends Activity {
    private static final String KEY_TAB = "tab";
    private static final String KEY_TEST = "test";

    private Engine engine;
    private View[] screens;
    private Button[] tabs;
    private int tab;

    private EditText usbEndpoint, wifiEndpoint, seqCount, seqInterval, obsDuration, obsSample, obsProbe, obsLabel;
    private TextView transportView, connectionStatus, explanation, prompt, progressText, historyText;
    private RadioGroup testChoice;
    private Button startBtn, cancelBtn;
    private ProgressBar progress;
    private View sequenceParams, observationParams;

    private final Runnable onEngineChange = this::render;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(R.layout.activity_main);
        engine = Engine.get(this);
        int o = getResources().getConfiguration().orientation;
        engine.recordActivityCreated(o == android.content.res.Configuration.ORIENTATION_LANDSCAPE
                ? "landscape" : o == android.content.res.Configuration.ORIENTATION_PORTRAIT ? "portrait" : "undefined",
                saved != null);

        screens = new View[]{findViewById(R.id.screen_connection), findViewById(R.id.screen_tests),
                findViewById(R.id.screen_history)};
        tabs = new Button[]{findViewById(R.id.tab_connection), findViewById(R.id.tab_tests),
                findViewById(R.id.tab_history)};
        for (int i = 0; i < tabs.length; i++) {
            final int idx = i;
            tabs[i].setOnClickListener(v -> showTab(idx));
        }

        ((TextView) findViewById(R.id.device_info)).setText("AndroidNetGuard " + engine.appVersion
                + "\nAndroid " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")");
        transportView = findViewById(R.id.observed_transport);
        usbEndpoint = findViewById(R.id.usb_endpoint);
        wifiEndpoint = findViewById(R.id.wifi_endpoint);
        connectionStatus = findViewById(R.id.connection_status);
        if (saved == null) {   // otherwise EditText restores its own text after rotation
            usbEndpoint.setText(engine.usbEndpoint());
            wifiEndpoint.setText(engine.wifiEndpoint());
        }
        findViewById(R.id.check_usb).setOnClickListener(v -> check("USB", usbEndpoint));
        findViewById(R.id.check_wifi).setOnClickListener(v -> check("Wi-Fi", wifiEndpoint));

        testChoice = findViewById(R.id.test_choice);
        for (TestDef d : TestDef.values()) {
            RadioButton rb = new RadioButton(this);
            rb.setId(View.generateViewId());
            rb.setTag(d);
            rb.setText(d.title + "  [" + d.mode.wire + "]");
            testChoice.addView(rb);
        }
        explanation = findViewById(R.id.test_explanation);
        sequenceParams = findViewById(R.id.sequence_params);
        seqCount = findViewById(R.id.seq_count);
        seqInterval = findViewById(R.id.seq_interval);
        observationParams = findViewById(R.id.observation_params);
        obsDuration = findViewById(R.id.obs_duration);
        obsSample = findViewById(R.id.obs_sample);
        obsProbe = findViewById(R.id.obs_probe);
        obsLabel = findViewById(R.id.obs_label);
        testChoice.setOnCheckedChangeListener((g, id) -> describeSelected());
        int sel = saved == null ? 0 : saved.getInt(KEY_TEST, 0);
        ((RadioButton) testChoice.getChildAt(sel)).setChecked(true);

        startBtn = findViewById(R.id.start_test);
        cancelBtn = findViewById(R.id.cancel_test);
        progress = findViewById(R.id.progress);
        prompt = findViewById(R.id.prompt);
        progressText = findViewById(R.id.progress_text);
        startBtn.setOnClickListener(v -> startSelected());
        cancelBtn.setOnClickListener(v -> engine.cancel("ui_cancel_button"));

        historyText = findViewById(R.id.history_text);
        findViewById(R.id.refresh_history).setOnClickListener(v -> engine.refreshHistory());

        showTab(saved == null ? 0 : saved.getInt(KEY_TAB, 0));
        if (saved == null) handleDebugIntent(getIntent());
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        handleDebugIntent(intent);
    }

    /**
     * DEBUG BUILDS ONLY: lets the Mac start a test without a screen tap, e.g.
     *   adb shell am start -n com.androidnetguard.app/.MainActivity --es ang.start_test wifi_connectivity
     * Optional extras: ang.wifi_endpoint, ang.count, ang.interval_ms. Runs record trigger=adb_intent.
     */
    private void handleDebugIntent(android.content.Intent intent) {
        if (!BuildConfig.DEBUG || intent == null) return;
        String testId = intent.getStringExtra("ang.start_test");
        if (testId == null) return;
        intent.removeExtra("ang.start_test");   // don't re-run on recreation
        showTab(1);
        TestDef d = TestDef.byId(testId);
        if (d == null) { progressText.setText("adb: unknown test " + testId); return; }
        String wifi = intent.getStringExtra("ang.wifi_endpoint");
        if (wifi != null) wifiEndpoint.setText(wifi);
        if (d == TestDef.WIFI_OBSERVATION) {   // keep the visible inputs equal to what is actually run
            obsDuration.setText(String.valueOf(intent.getIntExtra("ang.duration_s", RunParams.DEFAULT_DURATION_S)));
            obsSample.setText(String.valueOf(intent.getIntExtra("ang.sample_interval_ms", RunParams.DEFAULT_SAMPLE_MS)));
            obsProbe.setText(String.valueOf(intent.getIntExtra("ang.probe_interval_ms", RunParams.DEFAULT_PROBE_MS)));
            String lbl = intent.getStringExtra("ang.label");
            obsLabel.setText(lbl == null ? RunParams.DEFAULT_LABEL : lbl);
        }
        if (d == TestDef.WIFI_SEQUENCE) {   // keep the visible inputs equal to what is actually run
            seqCount.setText(String.valueOf(intent.getIntExtra("ang.count", RunParams.DEFAULT_COUNT)));
            seqInterval.setText(String.valueOf(intent.getIntExtra("ang.interval_ms", RunParams.DEFAULT_INTERVAL_MS)));
        }
        for (int i = 0; i < testChoice.getChildCount(); i++)
            if (testChoice.getChildAt(i).getTag() == d) ((RadioButton) testChoice.getChildAt(i)).setChecked(true);
        EditText field = d.mode == TestDef.Mode.USB ? usbEndpoint : wifiEndpoint;
        Endpoint ep = parseEndpoint(field);
        if (ep == null) { progressText.setText("adb: invalid " + d.mode.wire + " endpoint"); return; }
        RunParams p;
        try {
            p = d == TestDef.WIFI_SEQUENCE
                    ? RunParams.of(intent.getIntExtra("ang.count", RunParams.DEFAULT_COUNT),
                                   intent.getIntExtra("ang.interval_ms", RunParams.DEFAULT_INTERVAL_MS))
                    : d == TestDef.WIFI_OBSERVATION
                    ? RunParams.parseObservation(obsDuration.getText().toString(), obsSample.getText().toString(),
                            obsProbe.getText().toString(), obsLabel.getText().toString())
                    : RunParams.defaults();
        } catch (IllegalArgumentException e) {
            progressText.setText("adb: " + e.getMessage());
            return;
        }
        String err = engine.start(d, p, ep, "adb_intent");
        if (err != null) progressText.setText("adb: " + err);
    }

    @Override
    protected void onStart() {
        super.onStart();
        engine.addListener(onEngineChange);
        engine.refreshHistory();
        render();
    }

    @Override
    protected void onStop() {
        engine.removeListener(onEngineChange);
        super.onStop();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putInt(KEY_TAB, tab);
        out.putInt(KEY_TEST, selectedIndex());
    }

    private void showTab(int idx) {
        tab = idx;
        for (int i = 0; i < screens.length; i++) {
            screens[i].setVisibility(i == idx ? View.VISIBLE : View.GONE);
            tabs[i].setEnabled(i != idx);
        }
    }

    private int selectedIndex() {
        for (int i = 0; i < testChoice.getChildCount(); i++)
            if (((RadioButton) testChoice.getChildAt(i)).isChecked()) return i;
        return 0;
    }

    private TestDef selected() {
        return (TestDef) testChoice.getChildAt(selectedIndex()).getTag();
    }

    private void describeSelected() {
        TestDef d = selected();
        explanation.setText(d.title
                + "\n\nConfigured mode: " + d.mode.label
                + "\n\n" + d.explanation
                + "\n\nExpected: " + d.expected
                + "\n\nRequired evidence: " + d.evidence
                + "\n\nTimeouts: " + d.timeouts
                + "\n\nInconclusive when: " + d.inconclusiveWhen);
        sequenceParams.setVisibility(d == TestDef.WIFI_SEQUENCE ? View.VISIBLE : View.GONE);
        observationParams.setVisibility(d == TestDef.WIFI_OBSERVATION ? View.VISIBLE : View.GONE);
    }

    private Endpoint parseEndpoint(EditText field) {
        try {
            Endpoint ep = Endpoint.parse(field.getText().toString());
            field.setError(null);
            engine.saveEndpoints(usbEndpoint.getText().toString(), wifiEndpoint.getText().toString());
            return ep;
        } catch (IllegalArgumentException e) {
            field.setError(e.getMessage());
            return null;
        }
    }

    private void check(String label, EditText field) {
        Endpoint ep = parseEndpoint(field);
        if (ep != null) engine.healthCheck(label, ep);
    }

    private void startSelected() {
        TestDef d = selected();
        EditText field = d.mode == TestDef.Mode.USB ? usbEndpoint : wifiEndpoint;
        Endpoint ep = parseEndpoint(field);
        if (ep == null) {
            progressText.setText("Fix the " + d.mode.wire + " endpoint on the Connection screen first.");
            return;
        }
        RunParams p;
        try {
            p = d == TestDef.WIFI_SEQUENCE
                    ? RunParams.parse(seqCount.getText().toString(), seqInterval.getText().toString())
                    : d == TestDef.WIFI_OBSERVATION
                    ? RunParams.parseObservation(obsDuration.getText().toString(), obsSample.getText().toString(),
                            obsProbe.getText().toString(), obsLabel.getText().toString())
                    : RunParams.defaults();
        } catch (IllegalArgumentException e) {
            progressText.setText(e.getMessage());
            return;
        }
        String err = engine.start(d, p, ep);
        if (err != null) progressText.setText(err);
    }

    private void render() {
        transportView.setText("Observed active transport: " + engine.net.transport());
        connectionStatus.setText(engine.connectionStatus());

        Engine.Snapshot s = engine.snapshot();
        startBtn.setEnabled(!s.running);
        cancelBtn.setEnabled(s.running);
        testChoice.setEnabled(!s.running);
        for (int i = 0; i < testChoice.getChildCount(); i++) testChoice.getChildAt(i).setEnabled(!s.running);
        progress.setProgress(s.total > 0 ? (100 * s.done) / s.total : 0);
        StringBuilder sb = new StringBuilder();
        if (s.running) {
            sb.append("RUNNING ").append(s.testId).append('\n');
            String ap = engine.activeParams();
            if (ap != null) sb.append("Active run parameters: ").append(ap).append('\n');
        }
        if (s.runId != null) sb.append("run_id=").append(s.runId).append('\n');
        sb.append(s.progress == null ? "" : s.progress);
        if (s.lastResult != null) sb.append("\n\nLast result: ").append(s.lastResult);
        progressText.setText(sb.toString());
        if (s.prompt != null) {
            prompt.setText(s.prompt);
            prompt.setVisibility(View.VISIBLE);
        } else {
            prompt.setVisibility(View.GONE);
        }

        historyText.setText(formatHistory(engine.history()));
    }

    private static String formatHistory(List<HistoryModel.RunSummary> runs) {
        if (runs.isEmpty()) return "No runs recorded yet.";
        StringBuilder sb = new StringBuilder();
        for (HistoryModel.RunSummary r : runs) {
            sb.append("■ ").append(r.testId).append(" [").append(r.mode).append("] ")
                    .append(r.status.toUpperCase()).append("\n  run_id=").append(r.runId)
                    .append("\n  started ").append(r.startedWall);
            if (r.processes.size() > 1) sb.append("\n  processes=").append(r.processes.size());
            if (r.reason != null) sb.append("\n  ").append(r.reason);
            for (HistoryModel.AttemptSummary a : r.attempts) {
                sb.append("\n  · ").append(a.kind).append(a.seq > 0 ? " #" + a.seq : "")
                        .append(" ").append(a.outcome == null ? "(no result recorded)" : a.outcome)
                        .append(a.elapsedMs >= 0 ? " " + a.elapsedMs + " ms" : "")
                        .append(" via ").append(a.transport)
                        .append("\n    id=").append(a.requestId);
                if (a.ack != null) sb.append("\n    ack=").append(a.ack.equals(a.requestId) ? "same ID" : a.ack);
                if (a.error != null) sb.append("\n    err=").append(a.error);
            }
            sb.append("\n\n");
        }
        return sb.toString();
    }
}
