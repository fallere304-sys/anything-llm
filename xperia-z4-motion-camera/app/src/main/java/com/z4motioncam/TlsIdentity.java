package com.z4motioncam;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetAddress;
import java.security.GeneralSecurityException;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLServerSocketFactory;

/**
 * The HTTPS server's key and self-signed certificate. Generated on the phone (Android has no
 * public certificate builder, so the X.509 DER is assembled here) and kept in app-private storage.
 * Viewers confirm it by the SHA-256 fingerprint shown on the phone's screen.
 *
 * <p>iOS / macOS refuse TLS certificates, even self-signed ones the user would accept, unless they
 * name the host in subjectAltName, carry extendedKeyUsage serverAuth and are valid for at most
 * 825 days; Safari then fails with "the network connection was lost". The certificate therefore
 * lists every address it is reached by and is renewed before it expires.
 */
final class TlsIdentity {
    private static final String ALIAS = "z4motioncam";
    // Only guards the key file inside app-private storage.
    private static final char[] STORE_PASSWORD = "z4motioncam".toCharArray();

    /** Below Apple's 825-day limit. */
    static final int VALIDITY_DAYS = 800;
    private static final long RENEW_BEFORE_MS = 30L * 86_400_000L;
    private static final String SERVER_AUTH = "1.3.6.1.5.5.7.3.1";

    final KeyStore keyStore;
    final X509Certificate certificate;

    private TlsIdentity(KeyStore keyStore, X509Certificate certificate) {
        this.keyStore = keyStore;
        this.certificate = certificate;
    }

    /**
     * Loads the identity from {@code file}; creates a new one when there is none, or when the stored
     * certificate expires soon, lacks what iOS requires, or does not list all of {@code hosts}.
     */
    static TlsIdentity loadOrCreate(File file, List<String> hosts) throws IOException, GeneralSecurityException {
        if (file.isFile()) {
            KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
            InputStream in = new FileInputStream(file);
            try {
                ks.load(in, STORE_PASSWORD);
                Certificate c = ks.getCertificate(ALIAS);
                if (c instanceof X509Certificate && ks.isKeyEntry(ALIAS)
                        && isUsable((X509Certificate) c, hosts, System.currentTimeMillis())) {
                    return new TlsIdentity(ks, (X509Certificate) c);
                }
            } catch (IOException | GeneralSecurityException corrupt) {
                // regenerate below
            } finally {
                in.close();
            }
        }
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048, new SecureRandom());
        KeyPair kp = kpg.generateKeyPair();
        X509Certificate cert = selfSign(kp, "Z4 MotionCam", VALIDITY_DAYS, hosts);
        KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
        ks.load(null, null);
        ks.setKeyEntry(ALIAS, kp.getPrivate(), STORE_PASSWORD, new Certificate[] {cert});
        File tmp = new File(file.getPath() + ".tmp");
        OutputStream out = new FileOutputStream(tmp);
        try {
            ks.store(out, STORE_PASSWORD);
        } finally {
            out.close();
        }
        if (!tmp.renameTo(file)) throw new IOException("cannot save " + file);
        return new TlsIdentity(ks, cert);
    }

    /**
     * Valid for 30+ more days, within Apple's rules, and naming every one of {@code hosts}. Extra
     * names are fine, so a LAN IP that is momentarily unknown does not force a new certificate
     * (a new certificate means a new fingerprint for viewers to check).
     */
    static boolean isUsable(X509Certificate c, List<String> hosts, long now) {
        try {
            if (c.getNotAfter().getTime() - now < RENEW_BEFORE_MS) return false;
            if (c.getNotAfter().getTime() - c.getNotBefore().getTime() > 825L * 86_400_000L) return false;
            List<String> eku = c.getExtendedKeyUsage();
            if (eku == null || !eku.contains(SERVER_AUTH)) return false;
            Set<String> have = new HashSet<>();
            Collection<List<?>> sans = c.getSubjectAlternativeNames();
            if (sans != null) {
                for (List<?> san : sans) have.add(normalize(String.valueOf(san.get(1))));
            }
            Set<String> want = new HashSet<>();
            for (String h : hosts) want.add(normalize(h));
            return have.containsAll(want);
        } catch (GeneralSecurityException e) {
            return false;
        }
    }

    private static String normalize(String host) {
        String h = host.trim().toLowerCase(Locale.US);
        byte[] ip = ipLiteral(h);
        if (ip == null) return h;
        try {
            return InetAddress.getByAddress(ip).getHostAddress();
        } catch (IOException e) {
            return h;
        }
    }

    /** Bytes of an IPv4 / IPv6 literal, or null for a host name (never does a DNS lookup). */
    static byte[] ipLiteral(String host) {
        int[] v4 = NetUtil.parseV4(host);
        if (v4 != null) return new byte[] {(byte) v4[0], (byte) v4[1], (byte) v4[2], (byte) v4[3]};
        if (host.indexOf(':') < 0 || !host.matches("[0-9A-Fa-f:.]+")) return null;
        try {
            return InetAddress.getByName(host).getAddress(); // a literal: parsed, not resolved
        } catch (IOException e) {
            return null;
        }
    }

    SSLServerSocketFactory serverSocketFactory() throws GeneralSecurityException {
        KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        kmf.init(keyStore, STORE_PASSWORD);
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(kmf.getKeyManagers(), null, null);
        return ctx.getServerSocketFactory();
    }

    /** "AB:CD:..." SHA-256 fingerprint, as browsers display it. */
    String fingerprint() {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded());
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < d.length; i++) {
                if (i > 0) sb.append(':');
                sb.append(String.format(Locale.US, "%02X", d[i] & 0xFF));
            }
            return sb.toString();
        } catch (GeneralSecurityException e) {
            return "?";
        }
    }

    // ---- minimal X.509 v3 builder (DER) ----

    static X509Certificate selfSign(KeyPair kp, String commonName, int days, List<String> hosts)
            throws GeneralSecurityException {
        byte[] sigAlg = seq(oid("1.2.840.113549.1.1.11"), new byte[] {0x05, 0x00}); // sha256WithRSAEncryption
        byte[] name = seq(set(seq(oid("2.5.4.3"), tlv(0x0C, commonName.getBytes(java.nio.charset.Charset.forName("UTF-8"))))));
        long now = System.currentTimeMillis();
        byte[] validity = seq(time(new Date(now - 86_400_000L)), time(new Date(now + days * 86_400_000L)));
        byte[] serial = tlv(0x02, new BigInteger(63, new SecureRandom()).add(BigInteger.ONE).toByteArray());
        byte[] critical = {0x01, 0x01, (byte) 0xFF};
        // basicConstraints (critical): CA=false
        byte[] basicConstraints = seq(oid("2.5.29.19"), critical, tlv(0x04, seq()));
        // keyUsage (critical): digitalSignature, keyEncipherment
        byte[] keyUsage = seq(oid("2.5.29.15"), critical, tlv(0x04, new byte[] {0x03, 0x02, 0x05, (byte) 0xA0}));
        // extendedKeyUsage: serverAuth (required by iOS / macOS)
        byte[] extKeyUsage = seq(oid("2.5.29.37"), tlv(0x04, seq(oid(SERVER_AUTH))));
        // subjectAltName: every IP address / DNS name the phone is reached by (iOS ignores the CN)
        ByteArrayOutputStream names = new ByteArrayOutputStream();
        for (String h : hosts) {
            byte[] ip = ipLiteral(h.trim());
            byte[] gn = ip != null ? tlv(0x87, ip) // iPAddress [7]
                    : tlv(0x82, h.trim().toLowerCase(Locale.US).getBytes(java.nio.charset.Charset.forName("US-ASCII"))); // dNSName [2]
            names.write(gn, 0, gn.length);
        }
        byte[] extensions = hosts.isEmpty()
                ? seq(basicConstraints, keyUsage, extKeyUsage)
                : seq(basicConstraints, keyUsage, extKeyUsage, seq(oid("2.5.29.17"), tlv(0x04, tlv(0x30, names.toByteArray()))));
        byte[] tbs = seq(
                tlv(0xA0, tlv(0x02, new byte[] {2})), // version v3
                serial, sigAlg, name, validity, name,
                kp.getPublic().getEncoded(),          // SubjectPublicKeyInfo is already DER
                tlv(0xA3, extensions));
        PrivateKey key = kp.getPrivate();
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initSign(key);
        s.update(tbs);
        byte[] sig = s.sign();
        byte[] bitString = new byte[sig.length + 1];
        System.arraycopy(sig, 0, bitString, 1, sig.length);
        byte[] der = seq(tbs, sigAlg, tlv(0x03, bitString));
        return (X509Certificate) CertificateFactory.getInstance("X.509")
                .generateCertificate(new ByteArrayInputStream(der));
    }

    private static byte[] time(Date d) {
        SimpleDateFormat f = new SimpleDateFormat("yyMMddHHmmss'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return tlv(0x17, f.format(d).getBytes(java.nio.charset.Charset.forName("US-ASCII"))); // UTCTime (valid to 2049)
    }

    static byte[] oid(String dotted) {
        String[] p = dotted.split("\\.");
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(Integer.parseInt(p[0]) * 40 + Integer.parseInt(p[1]));
        for (int i = 2; i < p.length; i++) {
            long v = Long.parseLong(p[i]);
            List<Integer> groups = new ArrayList<>();
            do {
                groups.add((int) (v & 0x7F));
                v >>= 7;
            } while (v > 0);
            for (int j = groups.size() - 1; j >= 0; j--) out.write(groups.get(j) | (j > 0 ? 0x80 : 0));
        }
        return tlv(0x06, out.toByteArray());
    }

    private static byte[] seq(byte[]... parts) {
        return tlv(0x30, concat(parts));
    }

    private static byte[] set(byte[]... parts) {
        return tlv(0x31, concat(parts));
    }

    private static byte[] concat(byte[]... parts) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] p : parts) out.write(p, 0, p.length);
        return out.toByteArray();
    }

    static byte[] tlv(int tag, byte[] value) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(tag);
        int n = value.length;
        if (n < 0x80) {
            out.write(n);
        } else {
            int bytes = n > 0xFFFFFF ? 4 : n > 0xFFFF ? 3 : n > 0xFF ? 2 : 1;
            out.write(0x80 | bytes);
            for (int i = bytes - 1; i >= 0; i--) out.write((n >> (8 * i)) & 0xFF);
        }
        out.write(value, 0, n);
        return out.toByteArray();
    }
}
