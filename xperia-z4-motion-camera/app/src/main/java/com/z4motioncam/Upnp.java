package com.z4motioncam;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.Charset;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Minimal UPnP Internet Gateway Device client: finds the router, asks for its WAN IPv4 and opens /
 * closes one TCP port mapping. Only what remote viewing needs; no eventing, no XML library.
 */
final class Upnp {
    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final String[] SEARCH_TARGETS = {
            "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
            "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
            "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1",
    };

    /** A WANIPConnection / WANPPPConnection service of the router. */
    static final class Gateway {
        final String controlUrl;
        final String serviceType;

        Gateway(String controlUrl, String serviceType) {
            this.controlUrl = controlUrl;
            this.serviceType = serviceType;
        }
    }

    /** A SOAP fault from the router (e.g. 718 ConflictInMappingEntry). */
    static final class UpnpException extends IOException {
        private static final long serialVersionUID = 1L;
        final int code;

        UpnpException(int code, String description) {
            super("UPnP " + code + (description == null ? "" : " " + description));
            this.code = code;
        }
    }

    private Upnp() {}

    /** Multicasts an SSDP search and returns the first router offering port mapping, or null. */
    static Gateway discover(int timeoutMs) throws IOException {
        Set<String> locations = new LinkedHashSet<>();
        DatagramSocket socket = new DatagramSocket();
        try {
            socket.setSoTimeout(timeoutMs);
            InetAddress group = InetAddress.getByName("239.255.255.250");
            for (String st : SEARCH_TARGETS) {
                byte[] msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                        + "MAN: \"ssdp:discover\"\r\nMX: 2\r\nST: " + st + "\r\n\r\n").getBytes(UTF8);
                socket.send(new DatagramPacket(msg, msg.length, group, 1900));
            }
            byte[] buf = new byte[2048];
            long deadline = System.currentTimeMillis() + timeoutMs;
            while (System.currentTimeMillis() < deadline) {
                DatagramPacket p = new DatagramPacket(buf, buf.length);
                try {
                    socket.receive(p);
                } catch (SocketTimeoutException e) {
                    break;
                }
                String loc = header(new String(p.getData(), 0, p.getLength(), UTF8), "location");
                if (loc != null) locations.add(loc);
            }
        } finally {
            socket.close();
        }
        for (String loc : locations) {
            try {
                Gateway g = parseDescription(NetUtil.httpGet(loc, 4000, null), loc);
                if (g != null) return g;
            } catch (IOException ignored) {
                // not a usable device description
            }
        }
        return null;
    }

    static String header(String response, String name) {
        for (String line : response.split("\r?\n")) {
            int c = line.indexOf(':');
            if (c > 0 && line.substring(0, c).trim().equalsIgnoreCase(name)) return line.substring(c + 1).trim();
        }
        return null;
    }

    /** Finds the WAN connection service in a device description. */
    static Gateway parseDescription(String xml, String location) throws IOException {
        String base = tag(xml, "URLBase");
        if (base == null || base.isEmpty()) base = location;
        Matcher m = Pattern.compile("<(?:\\w+:)?service>(.*?)</(?:\\w+:)?service>", Pattern.DOTALL).matcher(xml);
        while (m.find()) {
            String svc = m.group(1);
            String type = tag(svc, "serviceType");
            String control = tag(svc, "controlURL");
            if (type == null || control == null) continue;
            if (type.contains(":WANIPConnection:") || type.contains(":WANPPPConnection:")) {
                return new Gateway(new URL(new URL(base), control).toString(), type);
            }
        }
        return null;
    }

    static String externalIp(Gateway g) throws IOException {
        return tag(soap(g, "GetExternalIPAddress", ""), "NewExternalIPAddress");
    }

    static void addPortMapping(Gateway g, int externalPort, int internalPort, String internalClient,
                               int leaseSeconds, String description) throws IOException {
        soap(g, "AddPortMapping", "<NewRemoteHost></NewRemoteHost>"
                + "<NewExternalPort>" + externalPort + "</NewExternalPort>"
                + "<NewProtocol>TCP</NewProtocol>"
                + "<NewInternalPort>" + internalPort + "</NewInternalPort>"
                + "<NewInternalClient>" + internalClient + "</NewInternalClient>"
                + "<NewEnabled>1</NewEnabled>"
                + "<NewPortMappingDescription>" + description + "</NewPortMappingDescription>"
                + "<NewLeaseDuration>" + leaseSeconds + "</NewLeaseDuration>");
    }

    static void deletePortMapping(Gateway g, int externalPort) throws IOException {
        soap(g, "DeletePortMapping", "<NewRemoteHost></NewRemoteHost>"
                + "<NewExternalPort>" + externalPort + "</NewExternalPort><NewProtocol>TCP</NewProtocol>");
    }

    /** Opens the mapping, retrying with a permanent lease for routers that only support that. */
    static void openPort(Gateway g, int port, String internalClient) throws IOException {
        try {
            addPortMapping(g, port, port, internalClient, 3600, "Z4MotionCam");
        } catch (UpnpException e) {
            if (e.code != 725) throw e; // 725 OnlyPermanentLeasesSupported
            addPortMapping(g, port, port, internalClient, 0, "Z4MotionCam");
        }
    }

    static String soap(Gateway g, String action, String args) throws IOException {
        String body = "<?xml version=\"1.0\"?>\r\n"
                + "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                + "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>"
                + "<u:" + action + " xmlns:u=\"" + g.serviceType + "\">" + args + "</u:" + action + ">"
                + "</s:Body></s:Envelope>";
        byte[] bytes = body.getBytes(UTF8);
        HttpURLConnection c = (HttpURLConnection) new URL(g.controlUrl).openConnection();
        try {
            c.setConnectTimeout(4000);
            c.setReadTimeout(4000);
            c.setDoOutput(true);
            c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "text/xml; charset=\"utf-8\"");
            c.setRequestProperty("SOAPAction", "\"" + g.serviceType + "#" + action + "\"");
            c.setFixedLengthStreamingMode(bytes.length);
            OutputStream out = c.getOutputStream();
            out.write(bytes);
            out.close();
            int code = c.getResponseCode();
            if (code / 100 == 2) return NetUtil.readLimited(c.getInputStream(), 64 * 1024);
            InputStream err = c.getErrorStream();
            String fault = err == null ? "" : NetUtil.readLimited(err, 64 * 1024);
            String ec = tag(fault, "errorCode");
            int n = -1;
            try {
                if (ec != null) n = Integer.parseInt(ec.trim());
            } catch (NumberFormatException ignored) {
                // keep -1
            }
            throw new UpnpException(n, ec == null ? "HTTP " + code : tag(fault, "errorDescription"));
        } finally {
            c.disconnect();
        }
    }

    /** Text of the first element with this local name (namespace prefix ignored). */
    static String tag(String xml, String name) {
        Matcher m = Pattern.compile("<(?:\\w+:)?" + Pattern.quote(name) + "(?:\\s[^>]*)?>(.*?)</(?:\\w+:)?"
                + Pattern.quote(name) + ">", Pattern.DOTALL | Pattern.CASE_INSENSITIVE).matcher(xml);
        return m.find() ? m.group(1).trim() : null;
    }

    static String describe(IOException e) {
        if (e instanceof UpnpException) {
            switch (((UpnpException) e).code) {
                case 718: return "そのポートは別の機器が使用中です（718）";
                case 606: case 401: return "ルーターがポート開放を許可していません（" + ((UpnpException) e).code + "）";
                case 724: return "外部ポートと内部ポートを同じにできません（724）";
                default: break;
            }
        }
        String m = e.getMessage();
        return m == null ? e.getClass().getSimpleName() : m.toLowerCase(Locale.US).contains("timed out") ? "応答なし" : m;
    }
}
