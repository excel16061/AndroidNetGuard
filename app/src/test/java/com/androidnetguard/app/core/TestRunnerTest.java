package com.androidnetguard.app.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Before;
import org.junit.Test;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Predicate;

public class TestRunnerTest {

    // ------------------------------------------------------------ fakes
    static class FakeClock implements Clock {
        long now = 1_000_000_000L;
        @Override public long monoNs() { return now += 1_000_000L; }   // +1 ms per read
        @Override public String wallTime() { return "2026-01-01T00:00:00Z"; }
        @Override public boolean sleep(long ms, CancelToken tok) {
            now += ms * 1_000_000L;
            return tok.isCancelled();
        }
    }

    static class FakeNet implements NetView {
        final Deque<String> script = new ArrayDeque<>();   // successive transport() values
        String fallback = WIFI;
        boolean awaitResult = true;
        String afterAwait = null;
        @Override public String transport() { return script.isEmpty() ? fallback : script.poll(); }
        @Override public boolean awaitTransport(Predicate<String> c, long t, CancelToken tok) {
            if (afterAwait != null) { fallback = afterAwait; afterAwait = null; }
            return awaitResult && !tok.isCancelled();
        }
    }

    interface Responder { ProbeResult respond(Map<String, Object> body, CancelToken tok); }

    static class FakeProbe implements Probe {
        ProbeResult healthResult = new ProbeResult(ProbeResult.HEALTH_OK, 200, null, "t", "sess", null);
        Responder responder = (b, t) -> new ProbeResult(ProbeResult.ACK, 200, (String) b.get("request_id"), "t", null, null);
        final List<Map<String, Object>> sent = new ArrayList<>();
        @Override public ProbeResult health(Endpoint ep, CancelToken tok) { return healthResult; }
        @Override public ProbeResult send(Endpoint ep, Map<String, Object> body, CancelToken tok) {
            sent.add(body);
            return responder.respond(body, tok);
        }
    }

    static class Sink implements EvidenceSink {
        final List<Map<String, Object>> events = new ArrayList<>();
        boolean fail;
        @Override public synchronized void emit(Map<String, Object> e) throws EvidenceException {
            if (fail) throw new EvidenceException("disk full", null);
            events.add(e);
        }
        List<Map<String, Object>> of(String type) {
            List<Map<String, Object>> out = new ArrayList<>();
            for (Map<String, Object> e : events) if (type.equals(e.get("event"))) out.add(e);
            return out;
        }
    }

    static class MemRestart implements RestartStore {
        PendingRestart p;
        @Override public void save(PendingRestart p) { this.p = p; }
        @Override public PendingRestart load() { return p; }
        @Override public void clear() { p = null; }
    }

    static final TestRunner.Listener NOOP = new TestRunner.Listener() {
        @Override public void progress(String r, String t, int d, int n) { }
        @Override public void prompt(String r, String t) { }
    };

    FakeClock clock; FakeNet net; FakeProbe probe; Sink sink; MemRestart restart;
    final Endpoint wifiEp = Endpoint.parse("http://192.0.2.6:8765");
    final Endpoint usbEp = Endpoint.parse("http://127.0.0.1:8765");

    @Before public void setUp() {
        clock = new FakeClock(); net = new FakeNet(); probe = new FakeProbe(); sink = new Sink(); restart = new MemRestart();
    }

    TestRunner runner(String processId) {
        return new TestRunner(probe, net, clock, sink, restart, NOOP, processId, "test");
    }

    TestRunner.Outcome run(TestDef d, RunParams p, Endpoint ep) {
        return runner("proc-1").run(d, p, ep, new CancelToken());
    }

    // ------------------------------------------------------------ tests
    @Test public void wifiConnectivityPassesOnlyWithAckOfSameIdAndWifi() {
        TestRunner.Outcome o = run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp);
        assertEquals(Status.PASSED, o.status);
        Map<String, Object> fin = sink.of("run_finished").get(0);
        assertEquals("passed", fin.get("status"));
        assertEquals("app_provisional", fin.get("status_scope"));
    }

    @Test public void ackWithDifferentIdIsFailedNotPassed() {
        probe.responder = (b, t) -> new ProbeResult(ProbeResult.ACK, 200, TestRunner.newId(), "t", null, null);
        TestRunner.Outcome o = run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp);
        assertEquals(Status.FAILED, o.status);
        boolean mismatch = false;
        for (Map<String, Object> e : sink.of("attempt_finished"))
            if (ProbeResult.ACK_MISMATCH.equals(e.get("outcome"))) mismatch = true;
        assertTrue(mismatch);
    }

    @Test public void lanAddressWithNonWifiTransportIsInconclusive() {
        net.fallback = "cellular";
        TestRunner.Outcome o = run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp);
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertTrue(probe.sent.isEmpty());   // stopped before sending
    }

    @Test public void vpnOverWifiIsNotTreatedAsWifi() {
        net.fallback = "wifi+vpn";
        assertEquals(Status.INCONCLUSIVE, run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp).status);
    }

    @Test public void timeoutIsFailedConnectivityNeverPassed() {
        probe.responder = (b, t) -> ProbeResult.failure(ProbeResult.TIMEOUT, -1, "read timed out");
        assertEquals(Status.FAILED, run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp).status);
    }

    @Test public void cleartextBlockedIsExecutionError() {
        probe.healthResult = ProbeResult.failure(ProbeResult.CLEARTEXT_NOT_PERMITTED, -1, "not permitted");
        assertEquals(Status.EXECUTION_ERROR, run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp).status);
    }

    @Test public void modeEndpointMismatchIsExecutionError() {
        assertEquals(Status.EXECUTION_ERROR, run(TestDef.USB_CONNECTIVITY, RunParams.defaults(), wifiEp).status);
        assertEquals(Status.EXECUTION_ERROR, run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), usbEp).status);
    }

    @Test public void usbConnectivityDoesNotRequireWifi() {
        net.fallback = "none";
        assertEquals(Status.PASSED, run(TestDef.USB_CONNECTIVITY, RunParams.defaults(), usbEp).status);
    }

    @Test public void sequenceSendsExactlyCountWithUniqueIdsAndSeq() {
        TestRunner.Outcome o = run(TestDef.WIFI_SEQUENCE, RunParams.of(5, 200), wifiEp);
        assertEquals(Status.PASSED, o.status);
        assertEquals(5, probe.sent.size());
        Set<Object> ids = new HashSet<>();
        for (int i = 0; i < 5; i++) {
            ids.add(probe.sent.get(i).get("request_id"));
            assertEquals(i + 1, probe.sent.get(i).get("seq"));
            assertEquals(o.runId, probe.sent.get(i).get("run_id"));
        }
        assertEquals(5, ids.size());
    }

    @Test public void sequenceOneFailureIsFailed() {
        final int[] n = {0};
        probe.responder = (b, t) -> ++n[0] == 3
                ? ProbeResult.failure(ProbeResult.CONNECT_ERROR, -1, "refused")
                : new ProbeResult(ProbeResult.ACK, 200, (String) b.get("request_id"), "t", null, null);
        assertEquals(Status.FAILED, run(TestDef.WIFI_SEQUENCE, RunParams.of(5, 200), wifiEp).status);
        assertEquals(5, probe.sent.size());   // continues collecting evidence after a failure
    }

    @Test public void sequenceTransportChangeIsInconclusiveAndStops() {
        // health: before/after wifi; msg1 before/after wifi; msg2 before = cellular
        net.script.addAll(List.of("wifi", "wifi", "wifi", "wifi", "wifi", "cellular"));
        net.fallback = "cellular";
        TestRunner.Outcome o = run(TestDef.WIFI_SEQUENCE, RunParams.of(5, 200), wifiEp);
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertTrue(probe.sent.size() < 5);
    }

    @Test public void cancellationDuringRequestIsInconclusiveAndRecorded() throws Exception {
        CancelToken tok = new CancelToken();
        probe.responder = (b, t) -> { t.cancel(); return ProbeResult.failure(ProbeResult.CANCELLED, -1, "cancelled"); };
        TestRunner.Outcome o = runner("p").run(TestDef.WIFI_SEQUENCE, RunParams.of(10, 200), wifiEp, tok);
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertEquals("cancelled by user", o.reason);
        assertEquals(1, probe.sent.size());
        assertEquals(1, sink.of("run_cancelled").size());
        assertEquals(1, sink.of("run_finished").size());
    }

    @Test public void preGeneratedRunIdIsUsedAndCancelCompletionIsLabelled() {
        CancelToken tok = new CancelToken();
        String id = TestRunner.newId();
        probe.responder = (b, t) -> { t.cancel(); return ProbeResult.failure(ProbeResult.CANCELLED, -1, "cancelled"); };
        TestRunner.Outcome o = runner("p").run(TestDef.WIFI_SEQUENCE, RunParams.of(10, 200), wifiEp, tok, "ui", id);
        assertEquals(id, o.runId);
        for (Map<String, Object> e : sink.events) assertEquals(id, e.get("run_id"));
        assertEquals("cancellation_completed", sink.of("run_cancelled").get(0).get("meaning"));
    }

    @Test public void cancelDuringIntervalStopsBeforeNextRequest() {
        CancelToken tok = new CancelToken();
        clock = new FakeClock() {
            int sleeps = 0;
            @Override public boolean sleep(long ms, CancelToken t) {
                if (++sleeps == 2) t.cancel();   // user taps Cancel during the 2nd interval
                return super.sleep(ms, t);
            }
        };
        TestRunner.Outcome o = runner("p").run(TestDef.WIFI_SEQUENCE, RunParams.of(10, 200), wifiEp, tok, "ui", TestRunner.newId());
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertEquals(2, probe.sent.size());   // no request started after the cancel
    }

    @Test public void cancelTokenUnblocksWaitAndRunsHooks() throws Exception {
        CancelToken tok = new CancelToken();
        AtomicReference<Boolean> hook = new AtomicReference<>(false);
        tok.onCancel(() -> hook.set(true));
        CountDownLatch started = new CountDownLatch(1);
        AtomicReference<Boolean> res = new AtomicReference<>();
        Thread t = new Thread(() -> {
            started.countDown();
            try { res.set(tok.await(60_000)); } catch (InterruptedException ignored) { }
        });
        t.start();
        started.await();
        long t0 = System.nanoTime();
        tok.cancel();
        t.join(2_000);
        assertTrue(res.get());
        assertTrue(hook.get());
        assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - t0) < 2_000);
    }

    @Test public void evidenceFailureIsExecutionError() {
        sink.fail = true;
        assertEquals(Status.EXECUTION_ERROR, run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp).status);
    }

    @Test public void restartWithoutRestartIsInconclusive() {
        TestRunner.Outcome o = run(TestDef.APP_RESTART, RunParams.defaults(), wifiEp);
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertNull(restart.load());   // pending cleared
    }

    @Test public void restartResumedInNewProcessPasses() {
        // Simulate phase A saving pending state, then a new process resuming.
        long now = clock.monoNs();
        restart.save(new RestartStore.PendingRestart(TestRunner.newId(), TestRunner.newId(), "old-proc",
                wifiEp.base(), now - 10_000_000L, now, now + 300_000_000_000L));
        RestartStore.PendingRestart p = restart.load();
        TestRunner.Outcome o = runner("new-proc").resumeRestart(p, new CancelToken());
        assertEquals(Status.PASSED, o.status);
        assertEquals(p.runId, o.runId);
        assertNull(restart.load());
        Map<String, Object> resumed = sink.of("run_resumed").get(0);
        assertEquals("old-proc", resumed.get("previous_process_id"));
        assertEquals("new-proc", resumed.get("process_id"));
    }

    @Test public void restartResumedSameProcessOrAfterDeadlineIsInconclusive() {
        long now = clock.monoNs();
        restart.save(new RestartStore.PendingRestart(TestRunner.newId(), "s", "same", wifiEp.base(), now, now, now + 1));
        assertEquals(Status.INCONCLUSIVE, runner("same").resumeRestart(restart.load(), new CancelToken()).status);
        now = clock.monoNs();
        restart.save(new RestartStore.PendingRestart(TestRunner.newId(), "s", "old", wifiEp.base(), now, now, now + 1));
        assertEquals(Status.INCONCLUSIVE, runner("new").resumeRestart(restart.load(), new CancelToken()).status);
    }

    @Test public void restartAfterRebootIsInconclusive() {
        long now = clock.monoNs();
        restart.save(new RestartStore.PendingRestart(TestRunner.newId(), "s", "old", wifiEp.base(),
                now + 1_000_000_000_000L, now + 1_000_000_000_000L, now + 2_000_000_000_000L));
        assertEquals(Status.INCONCLUSIVE, runner("new").resumeRestart(restart.load(), new CancelToken()).status);
    }

    @Test public void reconnectWithoutUserToggleIsInconclusive() {
        net.awaitResult = false;
        TestRunner.Outcome o = run(TestDef.WIFI_RECONNECT, RunParams.defaults(), wifiEp);
        assertEquals(Status.INCONCLUSIVE, o.status);
        assertTrue(o.reason.contains("loss not observed"));
    }

    @Test public void reconnectOutageFailureIsInformationalAndRecoveryPasses() {
        // outage attempt (seq 2) fails; recovery (seq 3) acks.
        probe.responder = (b, t) -> ((Integer) b.get("seq")) == 2
                ? ProbeResult.failure(ProbeResult.CONNECT_ERROR, -1, "network unreachable")
                : new ProbeResult(ProbeResult.ACK, 200, (String) b.get("request_id"), "t", null, null);
        TestRunner.Outcome o = run(TestDef.WIFI_RECONNECT, RunParams.defaults(), wifiEp);
        assertEquals(Status.PASSED, o.status);
        boolean informational = false;
        for (Map<String, Object> e : sink.of("step_finished"))
            if (Boolean.TRUE.equals(e.get("informational"))) informational = true;
        assertTrue(informational);
    }

    @Test public void reconnectNoRecoveryIsFailed() {
        probe.responder = (b, t) -> ((Integer) b.get("seq")) == 1
                ? new ProbeResult(ProbeResult.ACK, 200, (String) b.get("request_id"), "t", null, null)
                : ProbeResult.failure(ProbeResult.TIMEOUT, -1, "timeout");
        TestRunner.Outcome o = run(TestDef.WIFI_RECONNECT, RunParams.defaults(), wifiEp);
        assertEquals(Status.FAILED, o.status);
        assertEquals(1 + 1 + TestRunner.RECOVERY_ATTEMPTS, probe.sent.size());
    }

    @Test public void everyEventHasIdsAndMonotonicTime() {
        run(TestDef.WIFI_CONNECTIVITY, RunParams.defaults(), wifiEp);
        Set<Object> ids = new HashSet<>();
        long last = 0;
        for (Map<String, Object> e : sink.events) {
            assertNotNull(e.get("event_id"));
            assertNotNull(e.get("run_id"));
            assertTrue(ids.add(e.get("event_id")));
            long m = (Long) e.get("mono_ns");
            assertTrue(m > last);
            last = m;
        }
    }

    @Test public void paramsAreBounded() {
        for (int[] bad : new int[][]{{0, 1000}, {51, 1000}, {5, 199}, {5, 5001}}) {
            try { RunParams.of(bad[0], bad[1]); throw new AssertionError("accepted " + bad[0] + "/" + bad[1]); }
            catch (IllegalArgumentException expected) { }
        }
        try { RunParams.parse("x", "1"); throw new AssertionError(); } catch (IllegalArgumentException expected) { }
    }

    @Test public void sequenceRunLimitIsCapped() {
        assertTrue(TestRunner.runLimitMs(TestDef.WIFI_SEQUENCE, RunParams.of(50, 5000)) <= TestRunner.MAX_SEQUENCE_RUN_MS);
    }

    @Test public void endpointValidation() {
        assertTrue(Endpoint.parse("http://127.0.0.1:8765").isLoopback());
        assertTrue(Endpoint.parse("http://localhost:8765/").isLoopback());
        assertFalse(Endpoint.parse("http://192.0.2.6:8765").isLoopback());
        assertEquals("http://192.0.2.6:8765", Endpoint.parse(" http://192.0.2.6:8765 ").base());
        for (String bad : new String[]{"", "ftp://x", "http://u:p@h:1", "http://h:1/path", "http://h:1?q=1", "http://:80", "garbage"}) {
            try { Endpoint.parse(bad); throw new AssertionError("accepted " + bad); }
            catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void jsonEscapes() {
        assertEquals("{\"a\":\"x\\\"y\\n\",\"b\":[1,true,null]}",
                Json.write(new java.util.LinkedHashMap<String, Object>() {{
                    put("a", "x\"y\n"); put("b", java.util.Arrays.asList(1, true, null)); }}));
    }
}
