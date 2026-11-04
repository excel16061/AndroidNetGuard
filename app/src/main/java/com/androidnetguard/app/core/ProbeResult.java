package com.androidnetguard.app.core;

/** Result of one HTTP attempt. Only ACK (message) or HEALTH_OK (health) count as success. */
public final class ProbeResult {
    public static final String ACK = "ack";
    public static final String HEALTH_OK = "health_ok";
    public static final String ACK_MISMATCH = "ack_mismatch";
    public static final String HTTP_ERROR = "http_error";
    public static final String BAD_RESPONSE = "bad_response";
    public static final String TIMEOUT = "timeout";
    public static final String CONNECT_ERROR = "connect_error";
    public static final String IO_ERROR = "io_error";
    public static final String CLEARTEXT_NOT_PERMITTED = "cleartext_not_permitted";
    public static final String CANCELLED = "cancelled";

    public final String outcome;
    public final int httpStatus;          // -1 if none
    public final String ackRequestId;     // null if none
    public final String serverTime;       // server wall clock string, informational only
    public final String serverSessionId;  // from /health when available
    public final String error;            // null on success

    public ProbeResult(String outcome, int httpStatus, String ackRequestId, String serverTime,
                       String serverSessionId, String error) {
        this.outcome = outcome;
        this.httpStatus = httpStatus;
        this.ackRequestId = ackRequestId;
        this.serverTime = serverTime;
        this.serverSessionId = serverSessionId;
        this.error = error;
    }

    public boolean ok() { return ACK.equals(outcome) || HEALTH_OK.equals(outcome); }

    public static ProbeResult failure(String outcome, int status, String error) {
        return new ProbeResult(outcome, status, null, null, null, error);
    }
}
