package com.androidnetguard.app.core;

import java.util.Map;
import java.util.function.Consumer;

/** Opt-in Wi-Fi telemetry provider. start/stop bound the lifetime of any platform callbacks. */
public interface TelemetrySource {
    /** Begins listening; {@code onCapabilityChange} receives normalised change records. */
    void start(Consumer<Map<String, Object>> onCapabilityChange);

    /** One poll. Must not block for long and must never throw for permission problems. */
    WifiReading poll();

    void stop();
}
