package com.androidnetguard.app.core;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/**
 * Normalises {@link WifiReading}s into ANDROID_WIFI_TELEMETRY records with an explicit status
 * for every value. Never invents a number: anything not obtained is null with a reason.
 *
 * Statuses: ok | unknown (API sentinel, e.g. LINK_SPEED_UNKNOWN) | unavailable (not connected /
 * not provided) | invalid (outside physical range) | permission_denied | not_supported_api |
 * not_collected (deliberately not read, e.g. SSID/BSSID).
 */
public final class Telemetry {
    public static final String SOURCE = "ANDROID_WIFI_TELEMETRY";   // never "CSI"
    public static final String SCHEMA = "ang.wifi_telemetry/1";
    /** Callback-delivered values older than this are labelled stale. */
    public static final long CALLBACK_STALE_MS = 30_000;
    /** Android documents LINK_SPEED_UNKNOWN = -1 (WifiInfo, API 29). */
    public static final int LINK_SPEED_UNKNOWN = -1;
    /** NetworkCapabilities.SIGNAL_STRENGTH_UNSPECIFIED is Integer.MIN_VALUE in AOSP. */
    public static final int SIGNAL_STRENGTH_UNSPECIFIED = Integer.MIN_VALUE;

    private Telemetry() {}

    static Map<String, Object> m(Object value, String unit, String status) {
        Map<String, Object> x = new LinkedHashMap<>();
        x.put("value", value);
        x.put("unit", unit);
        x.put("status", status);
        return x;
    }

    private static boolean connected(WifiReading r) {
        return r.supplicantState == null || "COMPLETED".equals(r.supplicantState);
    }

    static Map<String, Object> rssi(Integer v, WifiReading r) {
        if (r.pollError != null) return m(null, "dBm", r.pollError);
        if (v == null || !connected(r)) return m(null, "dBm", "unavailable");
        // AOSP uses -127 as an invalid-RSSI sentinel; 802.11 RSSI in dBm is negative.
        if (v <= -127 || v >= 0) return m(null, "dBm", "invalid");
        return m(v, "dBm", "ok");
    }

    static Map<String, Object> freq(WifiReading r) {
        if (r.pollError != null) return m(null, "MHz", r.pollError);
        Integer v = r.frequencyMhz;
        if (v == null || !connected(r)) return m(null, "MHz", "unavailable");
        if (v <= 0) return m(null, "MHz", "unavailable");
        if (v < 2400 || v > 7200) return m(null, "MHz", "invalid");
        return m(v, "MHz", "ok");
    }

    static Map<String, Object> speed(Integer v, WifiReading r, int minApi) {
        if (r.pollError != null) return m(null, "Mbps", r.pollError);
        if (r.apiLevel < minApi) return m(null, "Mbps", "not_supported_api");
        if (v == null || !connected(r)) return m(null, "Mbps", "unavailable");
        if (v == LINK_SPEED_UNKNOWN) return m(null, "Mbps", "unknown");
        if (v < 0) return m(null, "Mbps", "invalid");
        return m(v, "Mbps", "ok");
    }

    static Map<String, Object> standard(WifiReading r) {
        if (r.pollError != null) return m(null, null, r.pollError);
        if (r.apiLevel < 30) return m(null, null, "not_supported_api");
        Integer v = r.wifiStandard;
        if (v == null || !connected(r)) return m(null, null, "unavailable");
        String label;
        switch (v) {   // android.net.wifi.ScanResult WIFI_STANDARD_* constants
            case 1: label = "legacy"; break;
            case 4: label = "802.11n"; break;
            case 5: label = "802.11ac"; break;
            case 6: label = "802.11ax"; break;
            case 7: label = "802.11ad"; break;
            case 8: label = "802.11be"; break;
            case 0: return m(null, null, "unknown");
            default: label = "code_" + v;
        }
        Map<String, Object> x = m(v, null, "ok");
        x.put("label", label);
        return x;
    }

    /** The comparable tuple used to decide whether a poll repeated the previous values. */
    static String fingerprint(WifiReading r) {
        return r.pollError + "|" + r.supplicantState + "|" + r.rssiDbm + "|" + r.frequencyMhz + "|"
                + r.linkSpeedMbps + "|" + r.txLinkSpeedMbps + "|" + r.rxLinkSpeedMbps;
    }

    /**
     * Builds the sample record. {@code previous} is the previous reading in this session (or null).
     * An identical value tuple is labelled "repeated"; the first or changed tuple is "new_value".
     * These labels do not establish radio measurement age, caching, or independence.
     */
    public static Map<String, Object> sample(WifiReading r, WifiReading previous, long nowMonoNs) {
        Map<String, Object> s = new LinkedHashMap<>();
        s.put("source", SOURCE);
        s.put("telemetry_schema", SCHEMA);
        s.put("api_level", r.apiLevel);
        s.put("poll_method", r.pollMethod);
        s.put("supplicant_state", r.supplicantState);
        s.put("rssi_dbm", rssi(r.rssiDbm, r));
        s.put("frequency_mhz", freq(r));
        s.put("link_speed_mbps", speed(r.linkSpeedMbps, r, 1));
        s.put("tx_link_speed_mbps", speed(r.txLinkSpeedMbps, r, 29));
        s.put("rx_link_speed_mbps", speed(r.rxLinkSpeedMbps, r, 29));
        s.put("wifi_standard", standard(r));
        s.put("ssid", m(null, null, "not_collected"));
        s.put("bssid", m(null, null, "not_collected"));
        s.put("freshness", previous == null ? "new_value"
                : Objects.equals(fingerprint(previous), fingerprint(r)) ? "repeated" : "new_value");

        Map<String, Object> cb = new LinkedHashMap<>();
        if (r.apiLevel < 29) {
            cb.put("status", "not_supported_api");
        } else if (r.callbackMonoNs == null) {
            cb.put("status", "unavailable");
        } else {
            long ageMs = (nowMonoNs - r.callbackMonoNs) / 1_000_000L;
            cb.put("age_ms", ageMs);
            cb.put("status", ageMs > CALLBACK_STALE_MS ? "stale" : "ok");
            Integer ss = r.callbackSignalStrength;
            cb.put("signal_strength", ss == null || ss == SIGNAL_STRENGTH_UNSPECIFIED
                    ? m(null, "dBm", "unavailable") : m(ss, "dBm", "ok"));
            cb.put("rssi_dbm", rssi(r.callbackRssiDbm, new WifiReading()));
            cb.put("validated", r.callbackValidated);
            cb.put("transports", r.callbackTransports);
        }
        s.put("callback", cb);
        if (r.applicableRedactions != null) s.put("applicable_redactions", r.applicableRedactions);
        return s;
    }

    /** True if this sample carries a usable RSSI value. */
    @SuppressWarnings("unchecked")
    public static boolean hasRssi(Map<String, Object> sample) {
        Object x = sample.get("rssi_dbm");
        return x instanceof Map && "ok".equals(((Map<String, Object>) x).get("status"));
    }
}
