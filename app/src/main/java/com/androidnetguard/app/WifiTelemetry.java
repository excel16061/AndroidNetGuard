package com.androidnetguard.app;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.SystemClock;

import com.androidnetguard.app.core.TelemetrySource;
import com.androidnetguard.app.core.WifiReading;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Consumer;

/**
 * ANDROID_WIFI_TELEMETRY provider (NOT CSI). Uses only documented, unprivileged APIs:
 *  - a listen-only NetworkCallback for Wi-Fi networks (registered only during a session), whose
 *    NetworkCapabilities carry a WifiInfo via getTransportInfo() (API 29+). Location-sensitive
 *    fields stay redacted because FLAG_INCLUDE_LOCATION_INFO is not used;
 *  - WifiManager.getConnectionInfo() for polling (deprecated in API 31 but documented as usable;
 *    needs the normal ACCESS_WIFI_STATE permission). SSID/BSSID/MAC are never read.
 */
final class WifiTelemetry implements TelemetrySource {
    static final String POLL_METHOD = "WifiManager.getConnectionInfo (deprecated API 31)";

    private final ConnectivityManager cm;
    private final WifiManager wm;
    private final Object lock = new Object();
    private ConnectivityManager.NetworkCallback callback;

    // Latest callback-delivered values (guarded by lock).
    private Long cbMonoNs;
    private Integer cbSignal, cbRssi;
    private Boolean cbValidated;
    private String cbTransports;
    private Long cbRedactions;
    private String lastChangeKey;

    WifiTelemetry(Context ctx) {
        cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
        wm = (WifiManager) ctx.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
    }

    @Override
    public void start(Consumer<Map<String, Object>> onChange) {
        synchronized (lock) {
            if (callback != null) return;
            cbMonoNs = null; cbSignal = null; cbRssi = null; cbValidated = null; cbTransports = null;
            lastChangeKey = null;
            callback = new ConnectivityManager.NetworkCallback() {
                @Override
                public void onCapabilitiesChanged(Network network, NetworkCapabilities nc) {
                    long now = SystemClock.elapsedRealtimeNanos();
                    Integer signal = null, rssi = null;
                    Long redactions = null;
                    if (Build.VERSION.SDK_INT >= 29) {
                        signal = nc.getSignalStrength();
                        Object ti = nc.getTransportInfo();
                        if (ti instanceof WifiInfo) {
                            rssi = ((WifiInfo) ti).getRssi();
                            if (Build.VERSION.SDK_INT >= 31) redactions = ((WifiInfo) ti).getApplicableRedactions();
                        }
                    }
                    boolean validated = nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
                    String transports = AndroidNetView.describe(nc);
                    Map<String, Object> change = null;
                    synchronized (lock) {
                        cbMonoNs = now; cbSignal = signal; cbRssi = rssi; cbValidated = validated;
                        cbTransports = transports; cbRedactions = redactions;
                        // Record a change event only when a tracked field changes (bounded volume).
                        String key = validated + "|" + transports + "|" + signal;
                        if (!Objects.equals(key, lastChangeKey)) {
                            lastChangeKey = key;
                            change = new LinkedHashMap<>();
                            change.put("source", "ANDROID_WIFI_TELEMETRY");
                            change.put("callback_mono_ns", now);
                            change.put("validated", validated);
                            change.put("transports", transports);
                            change.put("signal_strength_dbm", signal == null || signal == Integer.MIN_VALUE ? null : signal);
                            change.put("signal_strength_status", signal == null ? "not_supported_api"
                                    : signal == Integer.MIN_VALUE ? "unavailable" : "ok");
                        }
                    }
                    if (change != null) onChange.accept(change);
                }

                @Override
                public void onLost(Network network) {
                    long now = SystemClock.elapsedRealtimeNanos();
                    synchronized (lock) {
                        cbMonoNs = now; cbSignal = null; cbRssi = null; cbValidated = false; cbTransports = "none";
                        lastChangeKey = "lost";
                    }
                    Map<String, Object> change = new LinkedHashMap<>();
                    change.put("source", "ANDROID_WIFI_TELEMETRY");
                    change.put("callback_mono_ns", now);
                    change.put("wifi_network_lost", true);
                    onChange.accept(change);
                }
            };
            NetworkRequest req = new NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build();
            cm.registerNetworkCallback(req, callback);   // listen only: never requests or binds a network
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public WifiReading poll() {
        WifiReading r = new WifiReading();
        r.apiLevel = Build.VERSION.SDK_INT;
        r.pollMethod = POLL_METHOD;
        try {
            WifiInfo wi = wm == null ? null : wm.getConnectionInfo();
            if (wi == null) {
                r.supplicantState = "UNAVAILABLE";
            } else {
                r.supplicantState = String.valueOf(wi.getSupplicantState());
                r.rssiDbm = wi.getRssi();
                r.frequencyMhz = wi.getFrequency();
                r.linkSpeedMbps = wi.getLinkSpeed();
                if (Build.VERSION.SDK_INT >= 29) {
                    r.txLinkSpeedMbps = wi.getTxLinkSpeedMbps();
                    r.rxLinkSpeedMbps = wi.getRxLinkSpeedMbps();
                }
                if (Build.VERSION.SDK_INT >= 30) r.wifiStandard = wi.getWifiStandard();
            }
        } catch (SecurityException e) {
            r.pollError = "permission_denied";
        } catch (RuntimeException e) {
            r.pollError = "error";
        }
        synchronized (lock) {
            r.callbackMonoNs = cbMonoNs;
            r.callbackSignalStrength = cbSignal;
            r.callbackRssiDbm = cbRssi;
            r.callbackValidated = cbValidated;
            r.callbackTransports = cbTransports;
            r.applicableRedactions = cbRedactions;
        }
        return r;
    }

    @Override
    public void stop() {
        synchronized (lock) {
            if (callback != null) {
                try { cm.unregisterNetworkCallback(callback); } catch (IllegalArgumentException ignored) { }
                callback = null;
            }
        }
    }
}
