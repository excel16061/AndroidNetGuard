package com.androidnetguard.app.core;

import java.util.Map;

/** Bounded network operations against the collector. Implementations must honour the token. */
public interface Probe {
    String HEALTH_PATH = "/health";
    String MESSAGE_PATH = "/v1/test-message";

    ProbeResult health(Endpoint ep, CancelToken tok);

    /** POSTs body (must contain request_id) and verifies the ack echoes it. */
    ProbeResult send(Endpoint ep, Map<String, Object> body, CancelToken tok);
}
