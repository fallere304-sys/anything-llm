package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.net.InetAddress;
import java.nio.file.Files;
import java.security.cert.X509Certificate;
import java.util.List;
import org.junit.Test;

public class NetUtilTest {
    @Test
    public void parsesIpv4Literals() {
        assertEquals(4, NetUtil.parseV4("203.0.113.5").length);
        assertNull(NetUtil.parseV4("256.1.1.1"));
        assertNull(NetUtil.parseV4("1.2.3"));
        assertNull(NetUtil.parseV4("a.b.c.d"));
        assertNull(NetUtil.parseV4(""));
        assertNull(NetUtil.parseV4(null));
    }

    @Test
    public void classifiesIpv4() {
        assertTrue(NetUtil.isPrivateV4("192.168.1.10"));
        assertTrue(NetUtil.isPrivateV4("172.20.0.1"));
        assertFalse(NetUtil.isPrivateV4("172.32.0.1"));
        assertTrue(NetUtil.isCgnatV4("100.64.0.1"));
        assertTrue(NetUtil.isCgnatV4("100.127.255.254"));
        assertFalse(NetUtil.isCgnatV4("100.128.0.1"));
        assertTrue(NetUtil.isPublicV4("8.8.8.8"));
        assertTrue(NetUtil.isPublicV4("126.10.20.30"));
        assertFalse(NetUtil.isPublicV4("10.0.0.1"));
        assertFalse(NetUtil.isPublicV4("100.100.1.1"));
        assertFalse(NetUtil.isPublicV4("0.0.0.0"));
        assertFalse(NetUtil.isPublicV4("127.0.0.1"));
        assertFalse(NetUtil.isPublicV4("169.254.1.1"));
    }

    @Test
    public void onlyHomeNetworkAddressesAreLocal() throws Exception {
        assertTrue(NetUtil.isLocalAddress(InetAddress.getByName("127.0.0.1")));
        assertTrue(NetUtil.isLocalAddress(InetAddress.getByName("192.168.0.5")));
        assertTrue(NetUtil.isLocalAddress(InetAddress.getByName("10.1.2.3")));
        assertTrue(NetUtil.isLocalAddress(InetAddress.getByName("fe80::1")));
        assertTrue(NetUtil.isLocalAddress(InetAddress.getByName("fd12:3456::1")));
        assertTrue(NetUtil.isLocalAddress(InetAddress.getByName("::1")));
        assertFalse(NetUtil.isLocalAddress(InetAddress.getByName("8.8.8.8")));
        assertFalse(NetUtil.isLocalAddress(InetAddress.getByName("100.64.1.1")));
        assertFalse("global IPv6 is the Internet", NetUtil.isLocalAddress(InetAddress.getByName("2400:4051::1")));
        assertFalse(NetUtil.isLocalAddress(null));
    }

    @Test
    public void loadsBundledRootCertificates() throws Exception {
        String pem = new String(Files.readAllBytes(new File("app/src/main/assets/cacerts.pem").toPath()), "UTF-8");
        List<X509Certificate> roots = NetUtil.parsePem(pem);
        assertEquals(7, roots.size());
        assertTrue(roots.get(0).getSubjectX500Principal().getName().contains("ISRG Root X1"));
        assertNotNull(NetUtil.clientTls(roots));
    }
}
