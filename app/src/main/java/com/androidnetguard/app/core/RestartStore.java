package com.androidnetguard.app.core;

/** Persists the in-flight app-restart test across process death. */
public interface RestartStore {
    void save(PendingRestart p);
    PendingRestart load();
    void clear();

    final class PendingRestart {
        public final String runId;
        public final String stepId;
        public final String processId;     // process that started the wait
        public final String endpoint;      // base URL
        public final long runStartNs;      // monotonic
        public final long waitStartNs;     // monotonic
        public final long deadlineNs;      // monotonic

        public PendingRestart(String runId, String stepId, String processId, String endpoint,
                              long runStartNs, long waitStartNs, long deadlineNs) {
            this.runId = runId;
            this.stepId = stepId;
            this.processId = processId;
            this.endpoint = endpoint;
            this.runStartNs = runStartNs;
            this.waitStartNs = waitStartNs;
            this.deadlineNs = deadlineNs;
        }
    }
}
