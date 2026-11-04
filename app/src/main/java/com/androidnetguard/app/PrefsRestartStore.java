package com.androidnetguard.app;

import android.content.Context;
import android.content.SharedPreferences;

import com.androidnetguard.app.core.RestartStore;

/** Stores the pending app-restart wait synchronously (commit) so it survives process death. */
final class PrefsRestartStore implements RestartStore {
    private final SharedPreferences prefs;

    PrefsRestartStore(Context ctx) {
        prefs = ctx.getSharedPreferences("restart_test", Context.MODE_PRIVATE);
    }

    @Override
    public void save(PendingRestart p) {
        prefs.edit()
                .putString("run_id", p.runId).putString("step_id", p.stepId)
                .putString("process_id", p.processId).putString("endpoint", p.endpoint)
                .putLong("run_start_ns", p.runStartNs).putLong("wait_start_ns", p.waitStartNs)
                .putLong("deadline_ns", p.deadlineNs)
                .commit();
    }

    @Override
    public PendingRestart load() {
        String run = prefs.getString("run_id", null);
        if (run == null) return null;
        return new PendingRestart(run, prefs.getString("step_id", ""), prefs.getString("process_id", ""),
                prefs.getString("endpoint", ""), prefs.getLong("run_start_ns", 0),
                prefs.getLong("wait_start_ns", 0), prefs.getLong("deadline_ns", 0));
    }

    @Override
    public void clear() {
        prefs.edit().clear().commit();
    }
}
