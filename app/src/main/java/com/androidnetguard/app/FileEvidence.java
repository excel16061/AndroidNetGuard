package com.androidnetguard.app;

import com.androidnetguard.app.core.EvidenceSink;
import com.androidnetguard.app.core.Json;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Append-only JSONL evidence log in app-private storage:
 *   files/evidence/events.jsonl
 * Pulled to the Mac with `adb exec-out run-as com.androidnetguard.app cat files/evidence/events.jsonl`.
 */
final class FileEvidence implements EvidenceSink {
    static final long MAX_BYTES = 5L * 1024 * 1024;
    static final String RELATIVE_PATH = "evidence/events.jsonl";

    private final File file;

    FileEvidence(File filesDir) {
        this.file = new File(filesDir, RELATIVE_PATH);
    }

    @Override
    public synchronized void emit(Map<String, Object> event) throws EvidenceException {
        byte[] line = (Json.write(event) + "\n").getBytes(StandardCharsets.UTF_8);
        File dir = file.getParentFile();
        if (dir != null && !dir.isDirectory() && !dir.mkdirs()) {
            throw new EvidenceException("cannot create " + dir, null);
        }
        if (file.length() + line.length > MAX_BYTES) {
            throw new EvidenceException("evidence log is full (" + MAX_BYTES / (1024 * 1024)
                    + " MB); import it on the Mac and archive it before new runs", null);
        }
        try (FileOutputStream out = new FileOutputStream(file, true)) {
            // If a previous process died mid-write, the file may end without a newline.
            // Terminate that partial line first so it cannot corrupt this event; the
            // partial line itself is kept (raw evidence) and reported by the analysis.
            if (endsWithoutNewline()) out.write('\n');
            out.write(line);
            out.getFD().sync();
        } catch (IOException e) {
            throw new EvidenceException(e.getMessage(), e);
        }
    }

    private boolean endsWithoutNewline() throws IOException {
        long len = file.length();
        if (len == 0) return false;
        try (java.io.RandomAccessFile raf = new java.io.RandomAccessFile(file, "r")) {
            raf.seek(len - 1);
            return raf.read() != '\n';
        }
    }

    synchronized List<String> readLines() throws IOException {
        List<String> lines = new ArrayList<>();
        if (!file.exists()) return lines;
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
            String l;
            while ((l = r.readLine()) != null) if (!l.isEmpty()) lines.add(l);
        }
        return lines;
    }
}
