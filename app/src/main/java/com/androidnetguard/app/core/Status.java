package com.androidnetguard.app.core;

/**
 * Run/step status vocabulary (same names on the app and the dashboard).
 * See docs/STATUS.md for exact meanings.
 */
public enum Status {
    PENDING("pending"),
    RUNNING("running"),
    PASSED("passed"),
    FAILED("failed"),
    INCONCLUSIVE("inconclusive"),
    EXECUTION_ERROR("execution_error");

    public final String wire;

    Status(String wire) { this.wire = wire; }

    /** Precedence when combining: execution_error > inconclusive > failed > passed. */
    int rank() {
        switch (this) {
            case EXECUTION_ERROR: return 4;
            case INCONCLUSIVE: return 3;
            case FAILED: return 2;
            case PASSED: return 1;
            default: return 0;
        }
    }

    public static Status worst(Status a, Status b) {
        if (a == null) return b;
        if (b == null) return a;
        return b.rank() > a.rank() ? b : a;
    }

    public static Status fromWire(String w) {
        for (Status s : values()) if (s.wire.equals(w)) return s;
        return null;
    }
}
