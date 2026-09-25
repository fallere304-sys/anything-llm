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
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLServerSocketFactory;

/**
 * The HTTPS server's key and self-signed certificate. Generated once on the phone (Android has no
 * public certificate builder, so the X.509 DER is assembled here) and kept in app-private storage.
 * Viewers confirm it by the SHA-256 fingerprint shown on the phone's screen.
 */
final class TlsIdentity {
    private static final String ALIAS = "z4motioncam";
    // Only guards the key file inside app-private storage.
    private static final char[] STORE_PASSWORD = "z4motioncam".toCharArray();

    final KeyStore keyStore;
    final X509Certificate certificate;

    private TlsIdentity(KeyStore keyStore, X509Certificate certificate) {
        this.keyStore = keyStore;
        this.certificate = certificate;
    }

    /** Loads the identity from {@code file}, creating it on first use. */
    static TlsIdentity loadOrCreate(File file) throws IOException, GeneralSecurityException {
        KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
        if (file.isFile()) {
            InputStream in = new FileInputStream(file);
            try {
                ks.load(in, STORE_PASSWORD);
                Certificate c = ks.getCertificate(ALIAS);
                if (c instanceof X509Certificate && ks.isKeyEntry(ALIAS)) {
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
        X509Certificate cert = selfSign(kp, "Z4 MotionCam", 3650);
        ks = KeyStore.getInstance(KeyStore.getDefaultType());
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

    static X509Certificate selfSign(KeyPair kp, String commonName, int days) throws GeneralSecurityException {
        byte[] sigAlg = seq(oid("1.2.840.113549.1.1.11"), new byte[] {0x05, 0x00}); // sha256WithRSAEncryption
        byte[] name = seq(set(seq(oid("2.5.4.3"), tlv(0x0C, commonName.getBytes(java.nio.charset.Charset.forName("UTF-8"))))));
        long now = System.currentTimeMillis();
        byte[] validity = seq(time(new Date(now - 86_400_000L)), time(new Date(now + days * 86_400_000L)));
        byte[] serial = tlv(0x02, new BigInteger(63, new SecureRandom()).add(BigInteger.ONE).toByteArray());
        // basicConstraints (critical): CA=false
        byte[] basicConstraints = seq(oid("2.5.29.19"), new byte[] {0x01, 0x01, (byte) 0xFF}, tlv(0x04, seq()));
        byte[] tbs = seq(
                tlv(0xA0, tlv(0x02, new byte[] {2})), // version v3
                serial, sigAlg, name, validity, name,
                kp.getPublic().getEncoded(),          // SubjectPublicKeyInfo is already DER
                tlv(0xA3, seq(basicConstraints)));
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
