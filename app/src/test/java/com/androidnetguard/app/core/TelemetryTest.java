package com.androidnetguard.app.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

@SuppressWarnings("unchecked")
public class TelemetryTest {

    static WifiReading ok() {
        WifiReading r = new WifiReading();
        r.apiLevel = 33;
        r.pollMethod = "test";
        r.supplicantState = "COMPLETED";
        r.rssiDbm = -55; r.frequencyMhz = 5180; r.linkSpeedMbps = 433;
        r.txLinkSpeedMbps = 433; r.rxLinkSpeedMbps = 390; r.wifiStandard = 5;
        return r;
    }

    static Map<String, Object> f(Map<String, Object> s, String k) { return (Map<String, Object>) s.get(k); }

    @Test public void validReadingIsOkWithUnitsAndNeverCsi() {
        Map<String, Object> s = Telemetry.sample(ok(), null, 0);
        assertEquals("ANDROID_WIFI_TELEMETRY", s.get("source"));
        assertEquals(-55, f(s, "rssi_dbm").get("value"));
        assertEquals("dBm", f(s, "rssi_dbm").get("unit"));
        assertEquals("ok", f(s, "frequency_mhz").get("status"));
        assertEquals("802.11ac", f(s, "wifi_standard").get("label"));
        assertEquals("not_collected", f(s, "ssid").get("status"));
        assertNull(f(s, "bssid").get("value"));
        assertFalse(s.toString().contains("CSI"));
    }

    @Test public void sentinelsAreExplicitNotNumbers() {
        WifiReading r = ok();
        r.linkSpeedMbps = Telemetry.LINK_SPEED_UNKNOWN; r.rssiDbm = -127; r.wifiStandard = 0;
        Map<String, Object> s = Telemetry.sample(r, null, 0);
        assertEquals("unknown", f(s, "link_speed_mbps").get("status"));
        assertNull(f(s, "link_speed_mbps").get("value"));
        assertEquals("invalid", f(s, "rssi_dbm").get("status"));
        assertNull(f(s, "rssi_dbm").get("value"));
        assertEquals("unknown", f(s, "wifi_standard").get("status"));
        assertFalse(Telemetry.hasRssi(s));
    }

    @Test public void disconnectedIsUnavailable() {
        WifiReading r = ok();
        r.supplicantState = "DISCONNECTED";
        Map<String, Object> s = Telemetry.sample(r, null, 0);
        for (String k : new String[]{"rssi_dbm", "frequency_mhz", "link_speed_mbps"})
            assertEquals(k, "unavailable", f(s, k).get("status"));
    }

    @Test public void permissionDeniedPropagatesToEveryPolledField() {
        WifiReading r = new WifiReading();
        r.apiLevel = 33; r.pollError = "permission_denied";
        Map<String, Object> s = Telemetry.sample(r, null, 0);
        for (String k : new String[]{"rssi_dbm", "frequency_mhz", "link_speed_mbps", "tx_link_speed_mbps", "wifi_standard"})
            assertEquals(k, "permission_denied", f(s, k).get("status"));
    }

    @Test public void olderApiFieldsAreNotSupportedNotZero() {
        WifiReading r = ok();
        r.apiLevel = 28;
        Map<String, Object> s = Telemetry.sample(r, null, 0);
        assertEquals("not_supported_api", f(s, "tx_link_speed_mbps").get("status"));
        assertEquals("not_supported_api", f(s, "wifi_standard").get("status"));
        assertEquals("not_supported_api", f(s, "callback").get("status"));
    }

    @Test public void identicalPollIsRepeatedNotFresh() {
        WifiReading a = ok(), b = ok(), c = ok();
        c.rssiDbm = -60;
        assertEquals("new_value", Telemetry.sample(a, null, 0).get("freshness"));
        assertEquals("repeated", Telemetry.sample(b, a, 0).get("freshness"));
        assertEquals("new_value", Telemetry.sample(c, b, 0).get("freshness"));
    }

    @Test public void callbackAgeAndStaleness() {
        WifiReading r = ok();
        r.callbackMonoNs = 1_000_000_000L; r.callbackSignalStrength = -54; r.callbackValidated = true;
        Map<String, Object> fresh = f(Telemetry.sample(r, null, 1_000_000_000L + 5_000_000_000L), "callback");
        assertEquals("ok", fresh.get("status"));
        assertEquals(5000L, fresh.get("age_ms"));
        Map<String, Object> stale = f(Telemetry.sample(r, null, 1_000_000_000L + (Telemetry.CALLBACK_STALE_MS + 1) * 1_000_000L), "callback");
        assertEquals("stale", stale.get("status"));
        r.callbackSignalStrength = Telemetry.SIGNAL_STRENGTH_UNSPECIFIED;
        assertEquals("unavailable", f(f(Telemetry.sample(r, null, 1_000_000_000L), "callback"), "signal_strength").get("status"));
        WifiReading none = ok();
        assertEquals("unavailable", f(Telemetry.sample(none, null, 0), "callback").get("status"));
    }

    // ------------------------------------------------------------ observation runner
    static final class FakeTelemetry implements TelemetrySource {
        int polls, starts, stops;
        WifiReading next = ok();
        Consumer<Map<String, Object>> cb;
        @Override public void start(Consumer<Map<String, Object>> c) { starts++; cb = c; }
        @Override public WifiReading poll() {
            polls++;
            if (polls == 2 && cb != null) { Map<String, Object> m = new java.util.HashMap<>(); m.put("validated", true); cb.accept(m); }
            WifiReading r = next; WifiReading copy = ok();
            copy.pollError = r.pollError; copy.rssiDbm = r.rssiDbm - (polls % 3); copy.supplicantState = r.supplicantState;
            return copy;
        }
        @Override public void stop() { stops++; }
    }

    TestRunnerTest.FakeClock clock = new TestRunnerTest.FakeClock();
    TestRunnerTest.FakeNet net = new TestRunnerTest.FakeNet();
    TestRunnerTest.FakeProbe probe = new TestRunnerTest.FakeProbe();
    TestRunnerTest.Sink sink = new TestRunnerTest.Sink();
    FakeTelemetry tel = new FakeTelemetry();
    final Endpoint ep = Endpoint.parse("http://192.0.2.6:8765");

    TestRunner runner() {
        TestRunner t = new TestRunner(probe, net, clock, sink, new TestRunnerTest.MemRestart(),
                TestRunnerTest.NOOP, "p", "test");
        t.setTelemetrySource(tel);
        return t;
    }

    @Test public void observationSamplesAtBoundedRateAndCompletes() {
        TestRunner.Outcome o = runner().run(TestDef.WIFI_OBSERVATION, RunParams.observation(20, 2000, 5000, "stationary"),
                ep, new CancelToken());
        assertEquals(Status.PASSED, o.status);
        int samples = sink.of("telemetry_sample").size();
        assertTrue("samples=" + samples, samples >= 9 && samples <= 11);    // 20 s / 2 s
        assertTrue(probe.sent.size() >= 3 && probe.sent.size() <= 5);       // 20 s / 5 s
        assertEquals(1, tel.starts);
        assertEquals(1, tel.stops);                                         // callbacks always released
        assertEquals(1, sink.of("wifi_capabilities_changed").size());
        Map<String, Object> started = sink.of("run_started").get(0);
        assertEquals("stationary", ((Map<String, Object>) started.get("params")).get("session_label"));
        for (Map<String, Object> e : sink.of("telemetry_sample")) assertEquals("ANDROID_WIFI_TELEMETRY", e.get("source"));
    }

    @Test public void observationWithoutAnyRssiIsInconclusive() {
        tel.next.pollError = "permission_denied";
        TestRunner.Outcome o = runner().run(TestDef.WIFI_OBSERVATION, RunParams.observation(10, 2000, 5000, "x"),
                ep, new CancelToken());
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertTrue(o.reason.contains("no valid RSSI"));
    }

    @Test public void failedProbeTriggersFollowUpHealthCheckAndIsNotAFailure() {
        probe.responder = (b, t) -> ProbeResult.failure(ProbeResult.CONNECT_ERROR, -1, "unreachable");
        TestRunner.Outcome o = runner().run(TestDef.WIFI_OBSERVATION, RunParams.observation(10, 2000, 5000, "x"),
                ep, new CancelToken());
        assertEquals(Status.PASSED, o.status);   // failures are observations
        List<Object> kinds = new ArrayList<>();
        for (Map<String, Object> e : sink.of("attempt_started")) kinds.add(e.get("kind"));
        // start health, then (message, follow-up health) per probe, then end health
        assertEquals("health", kinds.get(0));
        assertEquals("message", kinds.get(1));
        assertEquals("health", kinds.get(2));
    }

    @Test public void cancellationStopsSessionAndReleasesCallbacks() {
        CancelToken tok = new CancelToken();
        probe.responder = (b, t) -> { if (((Integer) b.get("seq")) == 2) t.cancel();
            return new ProbeResult(ProbeResult.ACK, 200, (String) b.get("request_id"), "t", null, null); };
        TestRunner.Outcome o = runner().run(TestDef.WIFI_OBSERVATION, RunParams.observation(600, 1000, 2000, "x"), ep, tok);
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertEquals("cancelled by user", o.reason);
        assertEquals(1, tel.stops);
        assertTrue(sink.of("telemetry_sample").size() < 10);
        assertEquals(1, sink.of("run_cancelled").size());
    }

    @Test public void missingTelemetrySourceIsExecutionError() {
        TestRunner t = new TestRunner(probe, net, clock, sink, new TestRunnerTest.MemRestart(),
                TestRunnerTest.NOOP, "p", "test");
        assertEquals(Status.EXECUTION_ERROR,
                t.run(TestDef.WIFI_OBSERVATION, RunParams.observation(10, 2000, 5000, "x"), ep, new CancelToken()).status);
    }

    @Test public void observationParamsBounded() {
        int[][] bad = {{9, 2000, 5000}, {601, 2000, 5000}, {60, 999, 5000}, {60, 10_001, 5000}, {60, 2000, 1999}, {60, 2000, 30_001}};
        for (int[] b : bad) {
            try { RunParams.observation(b[0], b[1], b[2], "x"); throw new AssertionError("accepted " + b[0]); }
            catch (IllegalArgumentException expected) { }
        }
        for (String l : new String[]{"Bad Label", "a/b", "x".repeat(33)}) {
            try { RunParams.observation(60, 2000, 5000, l); throw new AssertionError("accepted " + l); }
            catch (IllegalArgumentException expected) { }
        }
        assertEquals("unlabelled", RunParams.observation(60, 2000, 5000, " ").label);
    }
}
