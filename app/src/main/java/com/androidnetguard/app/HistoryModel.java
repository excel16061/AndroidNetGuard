package com.androidnetguard.app;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Rebuilds run history from the raw app evidence log (app-side view only). */
final class HistoryModel {
    static final class AttemptSummary {
        String requestId, kind, outcome, transport, error, ack;
        int seq, http;
        long elapsedMs = -1;
    }

    static final class RunSummary {
        String runId, testId, mode, startedWall, status, reason;
        final Set processes = new Set();
        final List<AttemptSummary> attempts = new ArrayList<>();
    }

    /** Tiny ordered set of strings (avoids pulling in more types for display). */
    static final class Set {
        final List<String> items = new ArrayList<>();
        void add(String s) { if (s != null && !items.contains(s)) items.add(s); }
        int size() { return items.size(); }
    }

    static List<RunSummary> build(List<String> lines, int limit) {
        Map<String, RunSummary> runs = new LinkedHashMap<>();
        Map<String, AttemptSummary> attempts = new LinkedHashMap<>();
        for (String l : lines) {
            JSONObject j;
            try { j = new JSONObject(l); } catch (Exception e) { continue; }
            if (j.isNull("run_id")) continue;
            String runId = j.optString("run_id");
            String type = j.optString("event");
            RunSummary r = runs.get(runId);
            if (r == null) {
                r = new RunSummary();
                r.runId = runId;
                r.status = "running";
                runs.put(runId, r);
            }
            r.processes.add(j.optString("process_id", null));
            if (j.has("test_id") && !j.isNull("test_id")) r.testId = j.optString("test_id");
            switch (type) {
                case "run_started":
                    r.mode = j.optString("test_mode");
                    r.startedWall = j.optString("wall_time");
                    break;
                case "attempt_started": {
                    AttemptSummary a = new AttemptSummary();
                    a.requestId = j.optString("request_id");
                    a.kind = j.optString("kind");
                    a.seq = j.optInt("seq", 0);
                    a.transport = j.optString("observed_transport");
                    attempts.put(a.requestId, a);
                    r.attempts.add(a);
                    break;
                }
                case "attempt_finished": {
                    AttemptSummary a = attempts.get(j.optString("request_id"));
                    if (a != null) {
                        a.outcome = j.optString("outcome");
                        a.http = j.optInt("http_status", -1);
                        a.elapsedMs = j.optLong("elapsed_ms", -1);
                        a.error = j.isNull("error") ? null : j.optString("error");
                        a.ack = j.isNull("ack_request_id") ? null : j.optString("ack_request_id");
                        String after = j.optString("observed_transport_after");
                        if (!after.equals(a.transport)) a.transport = a.transport + "→" + after;
                    }
                    break;
                }
                case "run_finished":
                    r.status = j.optString("status");
                    r.reason = j.optString("reason");
                    break;
                default:
                    break;
            }
        }
        List<RunSummary> out = new ArrayList<>(runs.values());
        Collections.reverse(out);
        return out.size() > limit ? out.subList(0, limit) : out;
    }
}
