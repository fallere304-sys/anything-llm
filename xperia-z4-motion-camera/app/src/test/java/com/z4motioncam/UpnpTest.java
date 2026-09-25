package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

public class UpnpTest {
    private static final String DESCRIPTION = "<?xml version=\"1.0\"?><root xmlns=\"urn:schemas-upnp-org:device-1-0\">"
            + "<device><deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>"
            + "<serviceList><service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType>"
            + "<controlURL>/l3f</controlURL></service></serviceList>"
            + "<deviceList><device><deviceList><device><serviceList><service>"
            + "<serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>"
            + "<serviceId>urn:upnp-org:serviceId:WANIPConn1</serviceId>"
            + "<controlURL>/upnp/control/WANIPConn1</controlURL>"
            + "</service></serviceList></device></deviceList></device></deviceList></device></root>";

    private HttpServer router;
    private final List<String> requests = new ArrayList<>();
    private boolean permanentOnly = true;

    @Before
    public void setUp() throws IOException {
        router = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        router.createContext("/upnp/control/WANIPConn1", (HttpExchange ex) -> {
            String action = ex.getRequestHeaders().getFirst("SOAPAction");
            String body = read(ex.getRequestBody());
            requests.add(action + " " + body);
            if (action.contains("#GetExternalIPAddress")) {
                reply(ex, 200, "<s:Envelope><s:Body><u:GetExternalIPAddressResponse>"
                        + "<NewExternalIPAddress>126.1.2.3</NewExternalIPAddress>"
                        + "</u:GetExternalIPAddressResponse></s:Body></s:Envelope>");
            } else if (action.contains("#AddPortMapping") && permanentOnly && !body.contains("<NewLeaseDuration>0<")) {
                reply(ex, 500, "<s:Envelope><s:Body><s:Fault><detail><UPnPError>"
                        + "<errorCode>725</errorCode><errorDescription>OnlyPermanentLeasesSupported</errorDescription>"
                        + "</UPnPError></detail></s:Fault></s:Body></s:Envelope>");
            } else {
                reply(ex, 200, "<s:Envelope><s:Body/></s:Envelope>");
            }
        });
        router.start();
    }

    @After
    public void tearDown() {
        router.stop(0);
    }

    private static String read(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[1024];
        int n;
        while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }

    private static void reply(HttpExchange ex, int code, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.sendResponseHeaders(code, b.length);
        OutputStream o = ex.getResponseBody();
        o.write(b);
        o.close();
    }

    private Upnp.Gateway gateway() throws IOException {
        String location = "http://127.0.0.1:" + router.getAddress().getPort() + "/desc.xml";
        return Upnp.parseDescription(DESCRIPTION, location);
    }

    @Test
    public void findsWanConnectionService() throws IOException {
        Upnp.Gateway g = gateway();
        assertEquals("urn:schemas-upnp-org:service:WANIPConnection:1", g.serviceType);
        assertEquals("http://127.0.0.1:" + router.getAddress().getPort() + "/upnp/control/WANIPConn1", g.controlUrl);
        assertNull(Upnp.parseDescription("<root><service><serviceType>x</serviceType></service></root>", "http://a/"));
    }

    @Test
    public void readsExternalIp() throws IOException {
        assertEquals("126.1.2.3", Upnp.externalIp(gateway()));
        assertTrue(requests.get(0).startsWith("\"urn:schemas-upnp-org:service:WANIPConnection:1#GetExternalIPAddress\""));
    }

    @Test
    public void opensPortFallingBackToPermanentLease() throws IOException {
        Upnp.openPort(gateway(), 8443, "192.168.1.20");
        assertEquals(2, requests.size());
        assertTrue(requests.get(0).contains("<NewLeaseDuration>3600</NewLeaseDuration>"));
        assertTrue(requests.get(1).contains("<NewLeaseDuration>0</NewLeaseDuration>"));
        assertTrue(requests.get(1).contains("<NewInternalClient>192.168.1.20</NewInternalClient>"));
        assertTrue(requests.get(1).contains("<NewExternalPort>8443</NewExternalPort>"));
    }

    @Test
    public void reportsSoapFaults() throws IOException {
        try {
            Upnp.addPortMapping(gateway(), 8443, 8443, "192.168.1.20", 3600, "x");
            fail();
        } catch (Upnp.UpnpException e) {
            assertEquals(725, e.code);
        }
    }

    @Test
    public void parsesSsdpHeaders() {
        String resp = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\nLocation: http://192.168.1.1:5000/rootDesc.xml\r\n\r\n";
        assertEquals("http://192.168.1.1:5000/rootDesc.xml", Upnp.header(resp, "location"));
        assertEquals("a", Upnp.tag("<x:Foo attr=\"1\">a</x:Foo>", "foo"));
    }
}
