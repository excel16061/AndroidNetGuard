package com.androidnetguard.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class FileEvidenceTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    private static Map<String, Object> ev(int thread, int i) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("event_id", thread + "-" + i);
        m.put("pad", "x".repeat(200));   // make interleaving visible if writes were not atomic
        return m;
    }

    @Test public void concurrentEmitsProduceIntactLines() throws Exception {
        FileEvidence fe = new FileEvidence(tmp.getRoot());
        Thread[] ts = new Thread[8];
        for (int t = 0; t < ts.length; t++) {
            final int id = t;
            ts[t] = new Thread(() -> {
                for (int i = 0; i < 200; i++) {
                    try { fe.emit(ev(id, i)); } catch (Exception e) { throw new RuntimeException(e); }
                }
            });
            ts[t].start();
        }
        for (Thread t : ts) t.join();
        List<String> lines = fe.readLines();
        assertEquals(1600, lines.size());
        Set<String> ids = new HashSet<>();
        for (String l : lines) {
            assertTrue(l, l.startsWith("{\"event_id\":\"") && l.endsWith("\"}"));
            ids.add(l.substring(13, l.indexOf('"', 13)));
        }
        assertEquals(1600, ids.size());
    }

    @Test public void partialLineFromCrashIsTerminatedNotMerged() throws Exception {
        File f = new File(tmp.getRoot(), FileEvidence.RELATIVE_PATH);
        assertTrue(f.getParentFile().mkdirs());
        Files.write(f.toPath(), "{\"event_id\":\"ok\"}\n{\"event_id\":\"trunc".getBytes(StandardCharsets.UTF_8));
        FileEvidence fe = new FileEvidence(tmp.getRoot());
        fe.emit(ev(0, 1));
        List<String> lines = fe.readLines();
        assertEquals(3, lines.size());
        assertEquals("{\"event_id\":\"trunc", lines.get(1));          // preserved as-is
        assertTrue(lines.get(2).startsWith("{\"event_id\":\"0-1\""));  // new event intact on its own line
    }
}
