package com.androidnetguard.app.core;

/** Validated, bounded parameters for one run. */
public final class RunParams {
    public static final int MIN_COUNT = 1, MAX_COUNT = 50, DEFAULT_COUNT = 10;
    public static final int MIN_INTERVAL_MS = 200, MAX_INTERVAL_MS = 5000, DEFAULT_INTERVAL_MS = 1000;

    // Wireless observation session bounds.
    public static final int MIN_DURATION_S = 10, MAX_DURATION_S = 600, DEFAULT_DURATION_S = 120;
    public static final int MIN_SAMPLE_MS = 1000, MAX_SAMPLE_MS = 10_000, DEFAULT_SAMPLE_MS = 2000;
    public static final int MIN_PROBE_MS = 2000, MAX_PROBE_MS = 30_000, DEFAULT_PROBE_MS = 5000;
    public static final String DEFAULT_LABEL = "unlabelled";

    public final int count;
    public final int intervalMs;
    public final int durationS;
    public final int sampleIntervalMs;
    public final int probeIntervalMs;
    public final String label;

    private RunParams(int count, int intervalMs) {
        this(count, intervalMs, DEFAULT_DURATION_S, DEFAULT_SAMPLE_MS, DEFAULT_PROBE_MS, DEFAULT_LABEL);
    }

    private RunParams(int count, int intervalMs, int durationS, int sampleMs, int probeMs, String label) {
        this.count = count;
        this.intervalMs = intervalMs;
        this.durationS = durationS;
        this.sampleIntervalMs = sampleMs;
        this.probeIntervalMs = probeMs;
        this.label = label;
    }

    public static RunParams defaults() {
        return new RunParams(DEFAULT_COUNT, DEFAULT_INTERVAL_MS);
    }

    /** Observation-session parameters; throws IllegalArgumentException when out of bounds. */
    public static RunParams observation(int durationS, int sampleMs, int probeMs, String label) {
        if (durationS < MIN_DURATION_S || durationS > MAX_DURATION_S)
            throw new IllegalArgumentException("Duration must be " + MIN_DURATION_S + "–" + MAX_DURATION_S + " s");
        if (sampleMs < MIN_SAMPLE_MS || sampleMs > MAX_SAMPLE_MS)
            throw new IllegalArgumentException("Sampling interval must be " + MIN_SAMPLE_MS + "–" + MAX_SAMPLE_MS + " ms");
        if (probeMs < MIN_PROBE_MS || probeMs > MAX_PROBE_MS)
            throw new IllegalArgumentException("Probe interval must be " + MIN_PROBE_MS + "–" + MAX_PROBE_MS + " ms");
        String l = label == null || label.trim().isEmpty() ? DEFAULT_LABEL : label.trim();
        if (!l.matches("[a-z0-9_]{1,32}"))
            throw new IllegalArgumentException("Label: 1–32 of a–z, 0–9, _");
        return new RunParams(DEFAULT_COUNT, DEFAULT_INTERVAL_MS, durationS, sampleMs, probeMs, l);
    }

    public static RunParams parseObservation(String durationS, String sampleMs, String probeMs, String label) {
        try {
            return observation(Integer.parseInt(durationS.trim()), Integer.parseInt(sampleMs.trim()),
                    Integer.parseInt(probeMs.trim()), label);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Duration and intervals must be whole numbers");
        }
    }

    /** Throws IllegalArgumentException with a user-readable message if out of bounds. */
    public static RunParams of(int count, int intervalMs) {
        if (count < MIN_COUNT || count > MAX_COUNT)
            throw new IllegalArgumentException("Message count must be " + MIN_COUNT + "–" + MAX_COUNT);
        if (intervalMs < MIN_INTERVAL_MS || intervalMs > MAX_INTERVAL_MS)
            throw new IllegalArgumentException("Interval must be " + MIN_INTERVAL_MS + "–" + MAX_INTERVAL_MS + " ms");
        return new RunParams(count, intervalMs);
    }

    public static RunParams parse(String count, String intervalMs) {
        int c, i;
        try {
            c = Integer.parseInt(count.trim());
            i = Integer.parseInt(intervalMs.trim());
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Count and interval must be whole numbers");
        }
        return of(c, i);
    }
}
