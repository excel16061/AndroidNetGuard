package com.androidnetguard.app.core;

import java.util.function.Predicate;

/**
 * Observed default-network transport, e.g. "wifi", "cellular", "wifi+vpn", "none".
 * Only the exact value "wifi" counts as Wi-Fi for test preconditions.
 */
public interface NetView {
    String WIFI = "wifi";
    String NONE = "none";

    String transport();

    /** Blocks until cond(transport) holds, the timeout expires, or tok is cancelled. True if cond held. */
    boolean awaitTransport(Predicate<String> cond, long timeoutMs, CancelToken tok) throws InterruptedException;
}
