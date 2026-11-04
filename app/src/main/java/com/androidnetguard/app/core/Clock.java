package com.androidnetguard.app.core;

/** Monotonic + wall clock. Durations are always computed from monoNs(), never from wall time. */
public interface Clock {
    /** Monotonic nanoseconds (Android: elapsedRealtimeNanos, survives process restarts until reboot). */
    long monoNs();

    /** Device wall clock, ISO-8601 UTC. Informational only; never subtracted against another device. */
    String wallTime();

    /** Sleeps up to ms; returns true if cancelled. */
    default boolean sleep(long ms, CancelToken tok) throws InterruptedException { return tok.await(ms); }
}
