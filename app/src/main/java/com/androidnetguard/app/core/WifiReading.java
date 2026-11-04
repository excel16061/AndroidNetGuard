package com.androidnetguard.app.core;

/**
 * One raw reading from Android Wi-Fi APIs, before normalisation. Null means "not obtained".
 * Populated by the Android layer (WifiTelemetry); interpreted by {@link Telemetry}.
 */
public final class WifiReading {
    /** Why the poll could not produce values, or null: permission_denied | not_supported_api | error. */
    public String pollError;
    public String pollMethod;            // e.g. "WifiManager.getConnectionInfo (deprecated API 31)"
    public String supplicantState;       // e.g. "COMPLETED", "DISCONNECTED"
    public Integer rssiDbm;
    public Integer frequencyMhz;
    public Integer linkSpeedMbps;
    public Integer txLinkSpeedMbps;      // API 29+
    public Integer rxLinkSpeedMbps;      // API 29+
    public Integer wifiStandard;         // API 30+
    public int apiLevel;

    /** Latest values delivered by NetworkCallback.onCapabilitiesChanged (event-driven, may be old). */
    public Long callbackMonoNs;          // when the callback arrived (phone monotonic)
    public Integer callbackSignalStrength;
    public Integer callbackRssiDbm;
    public Boolean callbackValidated;
    public String callbackTransports;
    public Long applicableRedactions;    // WifiInfo.getApplicableRedactions (API 31+), informational
}
