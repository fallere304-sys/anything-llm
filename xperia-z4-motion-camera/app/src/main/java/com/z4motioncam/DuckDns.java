package com.z4motioncam;

import java.io.IOException;
import java.net.URLEncoder;

import javax.net.ssl.SSLSocketFactory;

/** DuckDNS update API: the A record (the home's global IP) and the TXT record used by Let's Encrypt. */
final class DuckDns implements Acme.Dns {
    static final String UPDATE_URL = "https://www.duckdns.org/update";
    private static final int TIMEOUT_MS = 8000;

    private final String updateUrl;
    private final String domain;
    private final String token;
    private final SSLSocketFactory tls;

    DuckDns(String updateUrl, String domain, String token, SSLSocketFactory tls) {
        this.updateUrl = updateUrl;
        this.domain = domain;
        this.token = token;
        this.tls = tls;
    }

    /** Points the name at {@code ip}; null lets DuckDNS use the address the request comes from. */
    boolean updateIp(String ip) throws IOException {
        return get("&ip=" + (ip == null ? "" : enc(ip))).startsWith("OK");
    }

    @Override
    public void setTxt(String value) throws IOException {
        if (!get("&txt=" + enc(value) + "&verbose=true").startsWith("OK")) {
            throw new Acme.AcmeException("DuckDNS に確認用の値を設定できません（ドメイン名とトークンを確認）", null);
        }
    }

    @Override
    public void clearTxt() throws IOException {
        get("&txt=removed&clear=true");
    }

    private String get(String params) throws IOException {
        String url = updateUrl + "?domains=" + enc(domain) + "&token=" + enc(token) + params;
        try {
            return NetUtil.httpGet(url, TIMEOUT_MS, tls).trim();
        } catch (IOException e) {
            throw new Acme.AcmeException("DuckDNS に接続できません", e.getMessage());
        }
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (IOException e) {
            return s;
        }
    }
}
