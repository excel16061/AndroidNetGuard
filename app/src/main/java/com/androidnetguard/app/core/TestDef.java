package com.androidnetguard.app.core;

/**
 * The initial CONNECTIVITY / RELIABILITY checks. None of these is a security-policy test.
 * Mirrors dashboard/catalog.py and docs/TESTS.md; keep them in sync.
 */
public enum TestDef {
    USB_CONNECTIVITY(
            "usb_connectivity", Mode.USB,
            "USB connectivity check",
            "Sends one health check and one test message to the loopback collector endpoint, "
                    + "which reaches the Mac only through an adb reverse mapping over USB.",
            "Health check returns ok; the message is acknowledged with the same request ID.",
            "App: attempt events with request ID and ack. Server: exactly one stored record with "
                    + "that request ID, arriving on a loopback address.",
            "5 s connect + 5 s read per request; whole run 30 s.",
            "Cancelled; server records not available for this run. Not evidence of Wi-Fi."),
    WIFI_CONNECTIVITY(
            "wifi_connectivity", Mode.WIFI,
            "Direct Wi-Fi connectivity check",
            "Sends one health check and one test message to the collector's LAN address "
                    + "while the phone's observed default network is Wi-Fi.",
            "Observed transport is exactly 'wifi'; health ok; message acknowledged with the same ID.",
            "App: observed transport before/after each request, ack. Server: one record with the "
                    + "request ID whose peer address is not loopback.",
            "5 s connect + 5 s read per request; whole run 30 s.",
            "Observed transport is not exactly 'wifi' (e.g. cellular, VPN, none); record arrived on "
                    + "loopback; cancelled; server records not available."),
    WIFI_SEQUENCE(
            "wifi_sequence", Mode.WIFI,
            "Bounded message sequence over stable Wi-Fi",
            "Sends a bounded number of test messages at a fixed interval while checking that the "
                    + "observed transport stays Wi-Fi.",
            "Every message acknowledged with its own ID; transport stays 'wifi' throughout.",
            "App: one attempt per message with seq number; transport before/after; transport "
                    + "change events. Server: exactly one record per request ID.",
            "Count 1–50, interval 200–5000 ms, 10 s per request; run bounded to "
                    + "count × (interval + 10 s) + 10 s, max 10 min.",
            "Transport changes during the run; cancelled; server records not available."),
    APP_RESTART(
            "app_restart", Mode.WIFI,
            "App restart, then connectivity check",
            "Checks connectivity, then asks you to close and reopen the app. After the restart "
                    + "the app (in a new process) automatically sends another test message.",
            "Baseline message acknowledged; a new app process is observed within 5 minutes; "
                    + "post-restart message acknowledged.",
            "App: different process IDs before/after; attempts in both processes; "
                    + "Server: one record per request ID.",
            "Restart window 5 min; post-restart Wi-Fi wait 15 s; 5 s + 5 s per request.",
            "Baseline fails; app not restarted within window; device rebooted; no Wi-Fi after "
                    + "restart; cancelled."),
    WIFI_RECONNECT(
            "wifi_reconnect", Mode.WIFI,
            "User-assisted Wi-Fi disconnect/reconnect observation",
            "A RELIABILITY observation, not a security test. You turn Wi-Fi off and back on when "
                    + "prompted; the app observes the transport change and checks recovery. "
                    + "The app never changes Wi-Fi settings itself.",
            "Baseline acknowledged; Wi-Fi loss observed; Wi-Fi return observed; a message is "
                    + "acknowledged within 3 attempts after return. The during-outage attempt is "
                    + "recorded for information only.",
            "App: transport change events, outage attempt outcome, recovery attempts. "
                    + "Server: records for baseline and recovery request IDs.",
            "Wait for Wi-Fi off 120 s; wait for Wi-Fi on 180 s; settle 5 s; 3 recovery attempts "
                    + "3 s apart.",
            "Baseline fails; you don't toggle Wi-Fi in time; transport not 'wifi' after return; "
                    + "cancelled."),
    WIFI_OBSERVATION(
            "wifi_observation", Mode.WIFI,
            "Wireless observation session (opt-in)",
            "Records ANDROID_WIFI_TELEMETRY (RSSI, frequency, link speeds, Wi-Fi standard, capability "
                    + "changes) at a bounded rate, plus periodic test messages and collector health checks. "
                    + "This is Android telemetry, not CSI. SSID/BSSID are not collected.",
            "Session runs for the chosen duration; telemetry samples and request outcomes are recorded. "
                    + "Signal changes and request failures are observations, not test failures.",
            "App: telemetry_sample events (value + status per field, freshness), capability changes, "
                    + "message/health attempts. Server: records for acknowledged probes.",
            "Duration 10–600 s; sampling every 1–10 s; probe every 2–30 s; 5 s + 5 s per request "
                    + "(sampling pauses while a request is blocked).",
            "Cancelled; no valid RSSI sample obtained (e.g. Wi-Fi off the whole time).");

    public enum Mode {
        USB("usb", "USB (adb reverse to loopback)"),
        WIFI("wifi", "Wi-Fi (direct to LAN address)");

        public final String wire;
        public final String label;

        Mode(String wire, String label) { this.wire = wire; this.label = label; }
    }

    public final String id;
    public final Mode mode;
    public final String title;
    public final String explanation;
    public final String expected;
    public final String evidence;
    public final String timeouts;
    public final String inconclusiveWhen;

    TestDef(String id, Mode mode, String title, String explanation, String expected,
            String evidence, String timeouts, String inconclusiveWhen) {
        this.id = id;
        this.mode = mode;
        this.title = title;
        this.explanation = explanation;
        this.expected = expected;
        this.evidence = evidence;
        this.timeouts = timeouts;
        this.inconclusiveWhen = inconclusiveWhen;
    }

    public static TestDef byId(String id) {
        for (TestDef d : values()) if (d.id.equals(id)) return d;
        return null;
    }
}
