package com.z4motioncam;

import static com.z4motioncam.NetworkDiagnostics.V4.CGNAT;
import static com.z4motioncam.NetworkDiagnostics.V4.DOUBLE_NAT;
import static com.z4motioncam.NetworkDiagnostics.V4.GLOBAL_ON_ROUTER;
import static com.z4motioncam.NetworkDiagnostics.V4.OFFLINE;
import static com.z4motioncam.NetworkDiagnostics.V4.SHARED_OR_TUNNELED;
import static com.z4motioncam.NetworkDiagnostics.V4.UNKNOWN;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class NetworkDiagnosticsTest {
    @Test
    public void classifiesRouterWanAgainstPublicIp() {
        assertEquals(GLOBAL_ON_ROUTER, NetworkDiagnostics.classify("126.1.2.3", true, "126.1.2.3"));
        assertEquals(DOUBLE_NAT, NetworkDiagnostics.classify("126.1.2.3", true, "192.168.1.1"));
        assertEquals(CGNAT, NetworkDiagnostics.classify("126.1.2.3", true, "100.70.1.2"));
        // v6plus / transix: the router reports no or a different IPv4.
        assertEquals(SHARED_OR_TUNNELED, NetworkDiagnostics.classify("126.1.2.3", true, "0.0.0.0"));
        assertEquals(SHARED_OR_TUNNELED, NetworkDiagnostics.classify("126.1.2.3", true, ""));
        assertEquals(SHARED_OR_TUNNELED, NetworkDiagnostics.classify("126.1.2.3", true, "126.9.9.9"));
        assertEquals(UNKNOWN, NetworkDiagnostics.classify("126.1.2.3", false, null));
        assertEquals(OFFLINE, NetworkDiagnostics.classify(null, true, "126.1.2.3"));
    }

    private static NetworkDiagnostics.Result result(NetworkDiagnostics.V4 v4, String v6) {
        NetworkDiagnostics.Result r = new NetworkDiagnostics.Result();
        r.localV4 = "192.168.1.20";
        r.publicV4 = "126.1.2.3";
        r.routerWanV4 = v4 == GLOBAL_ON_ROUTER ? "126.1.2.3" : "0.0.0.0";
        r.upnp = true;
        r.globalV6 = v6;
        r.v4 = v4;
        return r;
    }

    @Test
    public void adviceMatchesVerdict() {
        NetworkDiagnostics.Result ok = result(GLOBAL_ON_ROUTER, null);
        assertTrue(ok.v4Reachable());
        assertTrue(ok.summary().contains("グローバルIPv4"));
        assertTrue(ok.advice(8443).get(1).contains("TCP 8443 → 192.168.1.20:8443"));

        NetworkDiagnostics.Result tunnel = result(SHARED_OR_TUNNELED, null);
        assertFalse(tunnel.v4Reachable());
        assertTrue(String.join("|", tunnel.advice(8443)).contains("VPN"));

        NetworkDiagnostics.Result v6 = result(SHARED_OR_TUNNELED, "2400:4051::20");
        String all = String.join("|", v6.advice(8443));
        assertTrue(all.contains("IPv6"));
        assertFalse("VPN hint only when nothing else works", all.contains("VPN"));
    }

    @Test
    public void jsonIsWellFormed() {
        String j = result(GLOBAL_ON_ROUTER, null).toJson(8443);
        assertTrue(j, j.startsWith("{\"localV4\":\"192.168.1.20\""));
        assertTrue(j.contains("\"verdict\":\"GLOBAL_ON_ROUTER\",\"v4Reachable\":true"));
        assertTrue(j.contains("\"globalV6\":null"));
        assertTrue(j.endsWith(",\"checkedAt\":0}"));
    }
}
