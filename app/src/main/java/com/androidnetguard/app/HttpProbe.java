package com.androidnetguard.app;

import com.androidnetguard.app.core.CancelToken;
import com.androidnetguard.app.core.Endpoint;
import com.androidnetguard.app.core.Json;
import com.androidnetguard.app.core.Probe;
import com.androidnetguard.app.core.ProbeResult;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ConnectException;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.UnknownServiceException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** HttpURLConnection-based probe: bounded timeouts, bounded sizes, cancellable. */
final class HttpProbe implements Probe {
    static final int CONNECT_TIMEOUT_MS = 5_000;
    static final int READ_TIMEOUT_MS = 5_000;
    static final int MAX_REQUEST_BYTES = 4_096;
    static final int MAX_RESPONSE_BYTES = 8_192;

    @Override
    public ProbeResult health(Endpoint ep, CancelToken tok) {
        return exchange(ep.url(HEALTH_PATH), null, null, tok);
    }

    @Override
    public ProbeResult send(Endpoint ep, Map<String, Object> body, CancelToken tok) {
        byte[] bytes = Json.write(body).getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_REQUEST_BYTES) {
            return ProbeResult.failure(ProbeResult.IO_ERROR, -1, "request body too large");
        }
        return exchange(ep.url(MESSAGE_PATH), bytes, (String) body.get("request_id"), tok);
    }

    private ProbeResult exchange(String url, byte[] body, String requestId, CancelToken tok) {
        if (tok.isCancelled()) return ProbeResult.failure(ProbeResult.CANCELLED, -1, "cancelled before start");
        HttpURLConnection conn = null;
        Runnable unhook = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            final HttpURLConnection c = conn;
            unhook = tok.onCancel(c::disconnect);   // aborts blocking connect/read
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setInstanceFollowRedirects(false);
            conn.setUseCaches(false);
            conn.setRequestProperty("Accept", "application/json");
            if (body != null) {
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setFixedLengthStreamingMode(body.length);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(body);
                }
            } else {
                conn.setRequestMethod("GET");
            }
            int status = conn.getResponseCode();
            InputStream is = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String text = is == null ? "" : readLimited(is);
            if (status != HttpURLConnection.HTTP_OK) {
                return ProbeResult.failure(ProbeResult.HTTP_ERROR, status, abbreviate(text));
            }
            JSONObject resp;
            try {
                resp = new JSONObject(text);
            } catch (JSONException e) {
                return ProbeResult.failure(ProbeResult.BAD_RESPONSE, status, "response is not JSON");
            }
            String st = resp.optString("status", "");
            String serverTime = resp.optString("server_time", null);
            String session = resp.has("session_id") ? resp.optString("session_id") : null;
            if (body == null) {
                return "ok".equals(st)
                        ? new ProbeResult(ProbeResult.HEALTH_OK, status, null, serverTime, session, null)
                        : ProbeResult.failure(ProbeResult.BAD_RESPONSE, status, "health status '" + st + "'");
            }
            String ack = resp.optString("request_id", "");
            if (!"accepted".equals(st)) {
                return ProbeResult.failure(ProbeResult.BAD_RESPONSE, status, "server status '" + st + "'");
            }
            if (!ack.equals(requestId)) {
                return new ProbeResult(ProbeResult.ACK_MISMATCH, status, ack, serverTime, session,
                        "server acknowledged a different request ID");
            }
            return new ProbeResult(ProbeResult.ACK, status, ack, serverTime, session, null);
        } catch (SocketTimeoutException e) {
            return ProbeResult.failure(tok.isCancelled() ? ProbeResult.CANCELLED : ProbeResult.TIMEOUT, -1, e.getMessage());
        } catch (UnknownServiceException e) {
            // Thrown by the network security config when cleartext to this host is not permitted.
            return ProbeResult.failure(ProbeResult.CLEARTEXT_NOT_PERMITTED, -1, e.getMessage());
        } catch (ConnectException e) {
            return ProbeResult.failure(tok.isCancelled() ? ProbeResult.CANCELLED : ProbeResult.CONNECT_ERROR, -1, e.getMessage());
        } catch (IOException | RuntimeException e) {
            return ProbeResult.failure(tok.isCancelled() ? ProbeResult.CANCELLED : ProbeResult.IO_ERROR, -1,
                    e.getClass().getSimpleName() + ": " + e.getMessage());
        } finally {
            if (unhook != null) unhook.run();
            if (conn != null) conn.disconnect();
        }
    }

    private static String readLimited(InputStream is) throws IOException {
        try (InputStream in = is) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int n;
            while ((n = in.read(buf)) != -1) {
                if (out.size() + n > MAX_RESPONSE_BYTES) throw new IOException("response too large");
                out.write(buf, 0, n);
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private static String abbreviate(String s) {
        return s.length() <= 160 ? s : s.substring(0, 160) + "…";
    }
}
