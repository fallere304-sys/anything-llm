package com.z4motioncam;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.nio.charset.Charset;
import java.security.KeyStore;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

/** Address classification and small HTTP helpers. Pure java.net, so it is unit-testable. */
final class NetUtil {
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private NetUtil() {}

    /** Parses a dotted IPv4 literal without DNS; null when invalid. */
    static int[] parseV4(String s) {
        if (s == null) return null;
        String[] p = s.trim().split("\\.", -1);
        if (p.length != 4) return null;
        int[] out = new int[4];
        for (int i = 0; i < 4; i++) {
            if (p[i].isEmpty() || p[i].length() > 3) return null;
            for (int j = 0; j < p[i].length(); j++) {
                if (!Character.isDigit(p[i].charAt(j))) return null;
            }
            out[i] = Integer.parseInt(p[i]);
            if (out[i] > 255) return null;
        }
        return out;
    }

    /** 10/8, 172.16/12, 192.168/16. */
    static boolean isPrivateV4(String s) {
        int[] a = parseV4(s);
        return a != null && (a[0] == 10 || (a[0] == 172 && (a[1] & 0xF0) == 16) || (a[0] == 192 && a[1] == 168));
    }

    /** 100.64.0.0/10: carrier-grade NAT shared address space. */
    static boolean isCgnatV4(String s) {
        int[] a = parseV4(s);
        return a != null && a[0] == 100 && (a[1] & 0xC0) == 64;
    }

    /** A globally routable unicast IPv4 address. */
    static boolean isPublicV4(String s) {
        int[] a = parseV4(s);
        if (a == null || isPrivateV4(s) || isCgnatV4(s)) return false;
        return a[0] != 0 && a[0] != 127 && !(a[0] == 169 && a[1] == 254) && a[0] < 224
                && !(a[0] == 192 && a[1] == 0 && a[2] == 2)      // documentation ranges
                && !(a[0] == 198 && (a[1] == 18 || a[1] == 19))  // benchmarking
                && !(a[0] == 198 && a[1] == 51 && a[2] == 100)
                && !(a[0] == 203 && a[1] == 0 && a[2] == 113);
    }

    /**
     * True for addresses that can only come from the home network: loopback, link-local, private
     * IPv4, IPv6 unique-local. Public and CGNAT addresses, and global IPv6, are not local.
     */
    static boolean isLocalAddress(InetAddress a) {
        if (a == null) return false;
        if (a.isLoopbackAddress() || a.isLinkLocalAddress() || a.isAnyLocalAddress()) return true;
        if (a instanceof Inet4Address) return isPrivateV4(a.getHostAddress());
        byte[] b = a.getAddress();
        if (b.length == 16) {
            if ((b[0] & 0xFE) == 0xFC) return true; // fc00::/7 unique local
            // IPv4-mapped (::ffff:a.b.c.d)
            boolean mapped = true;
            for (int i = 0; i < 10; i++) if (b[i] != 0) mapped = false;
            if (mapped && (b[10] & 0xFF) == 0xFF && (b[11] & 0xFF) == 0xFF) {
                return isPrivateV4((b[12] & 0xFF) + "." + (b[13] & 0xFF) + "." + (b[14] & 0xFF) + "." + (b[15] & 0xFF))
                        || (b[12] & 0xFF) == 127;
            }
        }
        return false;
    }

    /** IPv4 of the Wi-Fi / Ethernet interface, or null when offline. */
    static String localIpv4() {
        for (InetAddress a : interfaceAddresses()) {
            if (a instanceof Inet4Address && !a.isLoopbackAddress()) return a.getHostAddress();
        }
        return null;
    }

    /**
     * A global IPv6 (2000::/3) of the Wi-Fi / Ethernet interface, preferring a stable (EUI-64)
     * address over short-lived privacy addresses. Null when there is none.
     */
    static String globalIpv6() {
        String fallback = null;
        for (InetAddress a : interfaceAddresses()) {
            if (!(a instanceof Inet6Address)) continue;
            byte[] b = a.getAddress();
            if ((b[0] & 0xE0) != 0x20) continue;
            String s = a.getHostAddress();
            int pct = s.indexOf('%');
            if (pct >= 0) s = s.substring(0, pct);
            if ((b[11] & 0xFF) == 0xFF && (b[12] & 0xFF) == 0xFE) return s;
            if (fallback == null) fallback = s;
        }
        return fallback;
    }

    private static List<InetAddress> interfaceAddresses() {
        List<InetAddress> out = new ArrayList<>();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                String name = ni.getName();
                // Wi-Fi (wlan*) or Ethernet; mobile data (rmnet*) is not the home network.
                if (!name.startsWith("wlan") && !name.startsWith("eth")) continue;
                out.addAll(Collections.list(ni.getInetAddresses()));
            }
        } catch (Exception ignored) {
            // no network
        }
        return out;
    }

    /** GET returning the body (max 64 KB), throwing on non-2xx. */
    static String httpGet(String url, int timeoutMs, SSLSocketFactory tls) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            if (tls != null && c instanceof HttpsURLConnection) ((HttpsURLConnection) c).setSSLSocketFactory(tls);
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setUseCaches(false);
            c.setRequestProperty("User-Agent", "Z4MotionCam");
            int code = c.getResponseCode();
            if (code / 100 != 2) throw new IOException("HTTP " + code);
            return readLimited(c.getInputStream(), 64 * 1024);
        } finally {
            c.disconnect();
        }
    }

    static String readLimited(InputStream in, int limit) throws IOException {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > limit) throw new IOException("response too large");
            }
            return new String(out.toByteArray(), UTF8);
        } finally {
            in.close();
        }
    }

    /** The public IPv4 seen from the Internet, or null. */
    static String publicIpv4(SSLSocketFactory tls) {
        String[] services = {"https://api.ipify.org", "https://checkip.amazonaws.com", "http://checkip.amazonaws.com"};
        for (String u : services) {
            try {
                String ip = httpGet(u, 5000, tls).trim();
                if (parseV4(ip) != null) return ip;
            } catch (IOException ignored) {
                // try the next service
            }
        }
        return null;
    }

    /** Parses concatenated PEM certificates. */
    static List<X509Certificate> parsePem(String pem) throws CertificateException {
        CertificateFactory cf = CertificateFactory.getInstance("X.509");
        List<X509Certificate> out = new ArrayList<>();
        String begin = "-----BEGIN CERTIFICATE-----";
        String end = "-----END CERTIFICATE-----";
        int i = 0;
        while ((i = pem.indexOf(begin, i)) >= 0) {
            int j = pem.indexOf(end, i);
            if (j < 0) break;
            String b64 = pem.substring(i + begin.length(), j).replaceAll("\\s", "");
            byte[] der = HttpServer.Base64.decode(b64);
            if (der == null) throw new CertificateException("bad PEM");
            out.add((X509Certificate) cf.generateCertificate(new java.io.ByteArrayInputStream(der)));
            i = j + end.length();
        }
        return out;
    }

    /**
     * TLS client factory trusting the system roots plus {@code extraRoots}. Android 7.0 and older
     * lack e.g. ISRG Root X1 (Let's Encrypt), which DDNS / IP lookup services commonly use.
     */
    static SSLSocketFactory clientTls(List<X509Certificate> extraRoots) {
        try {
            final X509TrustManager system = defaultTrustManager(null);
            KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
            ks.load(null, null);
            for (int i = 0; i < extraRoots.size(); i++) ks.setCertificateEntry("extra" + i, extraRoots.get(i));
            final X509TrustManager extra = defaultTrustManager(ks);
            TrustManager tm = new X509TrustManager() {
                @Override
                public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
                    system.checkClientTrusted(chain, authType);
                }

                @Override
                public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
                    try {
                        system.checkServerTrusted(chain, authType);
                    } catch (CertificateException e) {
                        extra.checkServerTrusted(chain, authType);
                    }
                }

                @Override
                public X509Certificate[] getAcceptedIssuers() {
                    return system.getAcceptedIssuers();
                }
            };
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, new TrustManager[] {tm}, null);
            return ctx.getSocketFactory();
        } catch (Exception e) {
            return null; // fall back to the platform default
        }
    }

    private static X509TrustManager defaultTrustManager(KeyStore ks) throws Exception {
        TrustManagerFactory f = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        f.init(ks);
        for (TrustManager t : f.getTrustManagers()) {
            if (t instanceof X509TrustManager) return (X509TrustManager) t;
        }
        throw new IllegalStateException("no X509TrustManager");
    }
}
