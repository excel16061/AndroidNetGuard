package com.androidnetguard.app.core;

import java.util.Map;

/** Append-only evidence store. Implementations must persist before returning. */
public interface EvidenceSink {
    void emit(Map<String, Object> event) throws EvidenceException;

    final class EvidenceException extends Exception {
        public EvidenceException(String msg, Throwable cause) { super(msg, cause); }
    }
}
