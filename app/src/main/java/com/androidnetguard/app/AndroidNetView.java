package com.androidnetguard.app;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

import com.androidnetguard.app.core.CancelToken;
import com.androidnetguard.app.core.NetView;

import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import java.util.function.Predicate;

/**
 * Observes the DEFAULT network's transports via ConnectivityManager callbacks.
 * Read-only: never requests, binds or changes networks.
 */
final class AndroidNetView implements NetView {
    private final ConnectivityManager cm;
    private final Object lock = new Object();
    private volatile String current;
    private final CopyOnWriteArrayList<Consumer<String>> listeners = new CopyOnWriteArrayList<>();

    AndroidNetView(Context ctx) {
        cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
        current = snapshot();
        // Per the NetworkCallback reference: do NOT call synchronous ConnectivityManager methods
        // (getActiveNetwork/getNetworkCapabilities) inside callbacks - they may return outdated
        // objects. Defect found on device (0.4.0): onLost() -> getActiveNetwork() still returned the
        // lost Wi-Fi network, so a Wi-Fi outage was never observed as a transport change.
        cm.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback() {
            @Override public void onCapabilitiesChanged(Network n, NetworkCapabilities nc) { update(describe(nc)); }
            // For registerDefaultNetworkCallback, onLost is invoked only when the default network is
            // lost and no other network satisfies the request: there is no default network now.
            @Override public void onLost(Network n) { update(NONE); }
            // onAvailable is always followed by onCapabilitiesChanged (API 26+), which carries the data.
        });
    }

    void addListener(Consumer<String> l) { listeners.add(l); }

    private void update(String t) {
        String prev;
        synchronized (lock) {
            prev = current;
            current = t;
            lock.notifyAll();
        }
        if (!t.equals(prev)) for (Consumer<String> l : listeners) l.accept(t);
    }

    private String snapshot() {
        Network n = cm.getActiveNetwork();
        if (n == null) return NONE;
        return describe(cm.getNetworkCapabilities(n));
    }

    static String describe(NetworkCapabilities nc) {
        if (nc == null) return NONE;
        StringBuilder sb = new StringBuilder();
        if (nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) sb.append("wifi+");
        if (nc.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) sb.append("cellular+");
        if (nc.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) sb.append("ethernet+");
        if (nc.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH)) sb.append("bluetooth+");
        if (nc.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) sb.append("vpn+");
        if (sb.length() == 0) return "other";
        return sb.substring(0, sb.length() - 1);
    }

    @Override
    public String transport() { return current; }

    @Override
    public boolean awaitTransport(Predicate<String> cond, long timeoutMs, CancelToken tok) throws InterruptedException {
        long deadline = System.nanoTime() + timeoutMs * 1_000_000L;
        Runnable unhook = tok.onCancel(() -> { synchronized (lock) { lock.notifyAll(); } });
        try {
            synchronized (lock) {
                while (true) {
                    if (cond.test(current)) return true;
                    if (tok.isCancelled()) return false;
                    long left = (deadline - System.nanoTime()) / 1_000_000L;
                    if (left <= 0) return false;
                    lock.wait(Math.min(left, 1_000));
                }
            }
        } finally {
            unhook.run();
        }
    }
}
