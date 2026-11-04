package com.androidnetguard.app.core;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Cooperative cancellation shared between the UI and the runner thread. */
public final class CancelToken {
    private final CountDownLatch latch = new CountDownLatch(1);
    private final List<Runnable> hooks = new CopyOnWriteArrayList<>();

    public void cancel() {
        if (latch.getCount() == 0) return;
        latch.countDown();
        for (Runnable r : hooks) {
            try { r.run(); } catch (RuntimeException ignored) { }
        }
    }

    public boolean isCancelled() { return latch.getCount() == 0; }

    /** Waits up to {@code ms}; returns true if cancelled (immediately or during the wait). */
    public boolean await(long ms) throws InterruptedException {
        if (ms <= 0) return isCancelled();
        return latch.await(ms, TimeUnit.MILLISECONDS);
    }

    /** Registers a hook run on cancel (e.g. disconnect a blocking socket). Returns a remover. */
    public Runnable onCancel(Runnable hook) {
        hooks.add(hook);
        if (isCancelled()) {
            try { hook.run(); } catch (RuntimeException ignored) { }
        }
        return () -> hooks.remove(hook);
    }
}
