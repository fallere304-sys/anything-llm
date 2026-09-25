package com.z4motioncam;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import javax.net.ssl.SSLSocketFactory;

/**
 * Investigates the home network from the phone itself: LAN address, the public IPv4 seen from the
 * Internet, the router's own WAN IPv4 (via UPnP) and a global IPv6. Comparing the two IPv4s tells
 * whether the router really holds a global address (port forwarding works) or sits behind carrier
 * NAT / an IPv4-over-IPv6 service (it does not).
 */
final class NetworkDiagnostics {
    enum V4 {
        /** The router's WAN address is the public address: port forwarding works. */
        GLOBAL_ON_ROUTER,
        /** Router WAN is a private address: another router sits in front of it. */
        DOUBLE_NAT,
        /** Router WAN is in 100.64/10: the provider shares one IPv4 among customers. */
        CGNAT,
        /** WAN and public differ / router has no IPv4 (MAP-E, DS-Lite, upstream NAT). */
        SHARED_OR_TUNNELED,
        /** The router did not answer UPnP, so its WAN address is unknown. */
        UNKNOWN,
        /** No Internet connection (public address lookup failed). */
        OFFLINE
    }

    static final class Result {
        String localV4;
        String gateway;
        String publicV4;
        boolean upnp;
        String routerWanV4;
        String upnpError;
        String globalV6;
        V4 v4;
        long checkedAt;

        boolean v4Reachable() {
            return v4 == V4.GLOBAL_ON_ROUTER;
        }

        String summary() {
            switch (v4) {
                case GLOBAL_ON_ROUTER:
                    return "ルーターにグローバルIPv4アドレスが割り当てられています。外出先から視聴できます（ポート開放が必要）。";
                case DOUBLE_NAT:
                    return "ルーターのWAN側がプライベートアドレス（" + routerWanV4 + "）です。上位にもう1台ルーター（ONU一体型ルーターなど）があります。";
                case CGNAT:
                    return "プロバイダーがIPv4アドレスを複数の契約者で共有しています（CGNAT: " + routerWanV4 + "）。IPv4では外から接続できません。";
                case SHARED_OR_TUNNELED:
                    return "ルーターのWAN側IPv4（" + (routerWanV4 == null || routerWanV4.isEmpty() ? "なし" : routerWanV4)
                            + "）と実際の公開IPv4（" + publicV4 + "）が違います。IPv6 IPoE（v6プラス/transix等）か上位NATの可能性が高く、IPv4での公開は制限されます。";
                case UNKNOWN:
                    return "ルーターがUPnPに応答しないため、グローバルIPが割り当てられているか自動では判定できません。";
                default:
                    return "インターネットに接続できていません。";
            }
        }

        List<String> advice(int remotePort) {
            List<String> a = new ArrayList<>();
            switch (v4) {
                case GLOBAL_ON_ROUTER:
                    a.add("設定で「外出先からの視聴」をオンにし、8文字以上のパスワードを設定してください。");
                    a.add("UPnPでTCP " + remotePort + " 番を自動で開放します。失敗する場合はルーターの管理画面で TCP " + remotePort
                            + " → " + localV4 + ":" + remotePort + " を転送（ポートフォワード）してください。");
                    a.add("グローバルIPは変わることがあるため、DuckDNS（無料）の設定を推奨します。");
                    break;
                case DOUBLE_NAT:
                    a.add("上位ルーターでも TCP " + remotePort + " を下位ルーターへ転送するか、下位ルーターをブリッジ（APモード）にしてください。");
                    break;
                case SHARED_OR_TUNNELED:
                    a.add("v6プラス等では、プロバイダーが割り当てたポート範囲内でのみ開放できます。ルーターの管理画面で「利用可能なポート」を確認し、その番号を外部ポートに設定してください。");
                    break;
                case UNKNOWN:
                    a.add("ルーターの管理画面でWAN側（インターネット側）IPv4アドレスを確認してください。公開IPv4（"
                            + (publicV4 == null ? "取得失敗" : publicV4) + "）と同じならグローバルIPです。");
                    a.add("同じなら、ルーターで TCP " + remotePort + " → " + localV4 + ":" + remotePort + " を手動で転送してください。");
                    break;
                default:
                    break;
            }
            if (globalV6 != null) {
                a.add("IPv6のグローバルアドレス（" + globalV6 + "）があります。ルーターのIPv6パケットフィルターで TCP "
                        + remotePort + " の着信を許可すれば、IPv6回線のスマホから接続できます。");
            }
            if (!v4Reachable() && globalV6 == null && v4 != V4.UNKNOWN) {
                a.add("直接の公開ができない環境です。ルーターのVPNサーバー機能を使い、VPN経由でLAN内のURLを開く方法を検討してください。");
            }
            return a;
        }

        String toJson(int remotePort) {
            StringBuilder advice = new StringBuilder();
            for (String s : advice(remotePort)) {
                if (advice.length() > 0) advice.append(',');
                advice.append(HttpServer.jsonString(s));
            }
            return "{\"localV4\":" + str(localV4) + ",\"gateway\":" + str(gateway)
                    + ",\"publicV4\":" + str(publicV4) + ",\"upnp\":" + upnp
                    + ",\"routerWanV4\":" + str(routerWanV4) + ",\"upnpError\":" + str(upnpError)
                    + ",\"globalV6\":" + str(globalV6) + ",\"verdict\":\"" + v4 + "\""
                    + ",\"v4Reachable\":" + v4Reachable() + ",\"summary\":" + str(summary())
                    + ",\"advice\":[" + advice + "],\"checkedAt\":" + checkedAt + "}";
        }

        private static String str(String s) {
            return s == null ? "null" : HttpServer.jsonString(s);
        }
    }

    private NetworkDiagnostics() {}

    static V4 classify(String publicV4, boolean upnp, String routerWanV4) {
        if (publicV4 == null) return V4.OFFLINE;
        if (!upnp) return V4.UNKNOWN;
        if (NetUtil.isCgnatV4(routerWanV4)) return V4.CGNAT;
        if (NetUtil.isPrivateV4(routerWanV4)) return V4.DOUBLE_NAT;
        if (publicV4.equals(routerWanV4) && NetUtil.isPublicV4(routerWanV4)) return V4.GLOBAL_ON_ROUTER;
        return V4.SHARED_OR_TUNNELED;
    }

    /** Runs all checks (a few seconds of network I/O; call off the main thread). */
    static Result run(String gateway, SSLSocketFactory tls) {
        Result r = new Result();
        r.localV4 = NetUtil.localIpv4();
        r.gateway = gateway;
        r.globalV6 = NetUtil.globalIpv6();
        r.publicV4 = NetUtil.publicIpv4(tls);
        try {
            Upnp.Gateway g = Upnp.discover(3000);
            if (g != null) {
                r.routerWanV4 = Upnp.externalIp(g);
                r.upnp = true;
            } else {
                r.upnpError = "UPnP対応ルーターが見つかりません";
            }
        } catch (IOException e) {
            r.upnpError = Upnp.describe(e);
        }
        r.v4 = classify(r.publicV4, r.upnp, r.routerWanV4);
        r.checkedAt = System.currentTimeMillis();
        return r;
    }
}
