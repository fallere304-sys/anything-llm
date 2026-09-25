package app.z4share;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** iPhone から到達できそうな LAN 側 IPv4 アドレスを列挙する。 */
final class NetUtil {

    static final class LanAddress {
        final String iface;
        final String ip;

        LanAddress(String iface, String ip) {
            this.iface = iface;
            this.ip = ip;
        }

        /** Z4 自身がテザリング (アクセスポイント) になっている時のインターフェースか。 */
        boolean isHotspot() {
            return iface.startsWith("ap") || iface.startsWith("softap") || iface.startsWith("swlan")
                    || ip.equals("192.168.43.1");
        }
    }

    private NetUtil() {
    }

    static List<LanAddress> lanAddresses() {
        List<LanAddress> wifi = new ArrayList<>();
        List<LanAddress> other = new ArrayList<>();
        List<NetworkInterface> ifaces;
        try {
            ifaces = Collections.list(NetworkInterface.getNetworkInterfaces());
        } catch (SocketException | NullPointerException e) {
            return wifi;
        }
        for (NetworkInterface ni : ifaces) {
            String name = ni.getName().toLowerCase(Locale.US);
            try {
                if (!ni.isUp() || ni.isLoopback() || isCellularOrVirtual(name)) {
                    continue;
                }
            } catch (SocketException e) {
                continue;
            }
            for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                if (a instanceof Inet4Address && a.isSiteLocalAddress()) {
                    LanAddress la = new LanAddress(name, a.getHostAddress());
                    (name.startsWith("wlan") ? wifi : other).add(la);
                }
            }
        }
        wifi.addAll(other);
        return wifi;
    }

    private static boolean isCellularOrVirtual(String name) {
        return name.startsWith("rmnet") || name.startsWith("r_rmnet") || name.startsWith("v4-")
                || name.startsWith("ccmni") || name.startsWith("dummy") || name.startsWith("tun")
                || name.startsWith("p2p") || name.startsWith("lo");
    }
}
