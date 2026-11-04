package com.androidnetguard.app.core;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** A validated collector base URL: scheme://host[:port] with no path, query or credentials. */
public final class Endpoint {
    public final String scheme;
    public final String host;
    public final int port;

    private Endpoint(String scheme, String host, int port) {
        this.scheme = scheme;
        this.host = host;
        this.port = port;
    }

    public static Endpoint parse(String raw) {
        if (raw == null) throw new IllegalArgumentException("Endpoint is empty");
        String s = raw.trim();
        if (s.isEmpty()) throw new IllegalArgumentException("Endpoint is empty");
        if (s.length() > 200) throw new IllegalArgumentException("Endpoint is too long");
        URI u;
        try {
            u = new URI(s);
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("Endpoint is not a valid URL");
        }
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https"))
            throw new IllegalArgumentException("Endpoint must start with http:// or https://");
        if (u.getRawUserInfo() != null) throw new IllegalArgumentException("Credentials in URL are not allowed");
        if (u.getRawQuery() != null || u.getRawFragment() != null)
            throw new IllegalArgumentException("Query/fragment not allowed; enter only scheme://host:port");
        String path = u.getRawPath();
        if (path != null && !path.isEmpty() && !path.equals("/"))
            throw new IllegalArgumentException("Enter only scheme://host:port (no path)");
        String host = u.getHost();
        if (host == null || host.isEmpty()) throw new IllegalArgumentException("Endpoint has no host");
        int port = u.getPort();
        if (port == -1) port = scheme.equals("https") ? 443 : 80;
        if (port < 1 || port > 65535) throw new IllegalArgumentException("Port out of range");
        return new Endpoint(scheme, host.toLowerCase(Locale.ROOT), port);
    }

    /** True for loopback hosts (used by the adb-reverse USB path). */
    public boolean isLoopback() {
        String h = host.startsWith("[") ? host.substring(1, host.length() - 1) : host;
        return h.equals("localhost") || h.equals("::1") || h.matches("127\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}");
    }

    public String base() {
        return scheme + "://" + host + ":" + port;
    }

    public String url(String path) {
        return base() + path;
    }

    @Override
    public String toString() { return base(); }
}
