package com.z4motioncam;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.Test;

public class SettingsSpecTest {
    private static Map<String, String> current() {
        Map<String, String> m = new HashMap<>();
        for (SettingsSpec.Field f : SettingsSpec.FIELDS) m.put(f.key, f.def);
        return m;
    }

    private static String check(String... kv) {
        Map<String, String> in = new HashMap<>();
        for (int i = 0; i < kv.length; i += 2) in.put(kv[i], kv[i + 1]);
        return SettingsSpec.validate(in, current(), new HashMap<String, String>());
    }

    @Test
    public void acceptsValidValues() {
        assertNull(check("post_record_sec", "180", "fps", "1", "resolution", "1920x1080"));
        assertNull(check("use_sd", "false", "min_free_mb", " 800 ", "remote_port", "5880"));
        assertNull(check("password", "abcdefgh", "external_host", "203.0.113.5", "ddns_domain", "MyBaby.duckdns.org"));
        assertNull(check("segment_min", "0"));  // no split: one file per motion event
        assertNull(check("segment_min", "60"));
        assertEquals("0", SettingsSpec.find("segment_min").def);
    }

    @Test
    public void rejectsInvalidValues() {
        assertTrue(check("post_record_sec", "181").contains("post_record_sec"));
        assertTrue(check("segment_min", "20").contains("segment_min"));
        assertTrue(check("fps", "abc").contains("fps"));
        assertTrue(check("port", "80").contains("1024"));
        assertTrue(check("remote_port", "8080").contains("別の番号"));
        assertTrue(check("password", "short").contains("8文字"));
        assertTrue(check("external_host", "a b").contains("外部アドレス"));
        assertTrue(check("use_sd", "yes").contains("use_sd"));
        assertTrue(check("unknown", "1").contains("不明"));
    }

    @Test
    public void emptySecretKeepsTheCurrentOneAndDashClearsIt() {
        Map<String, String> out = new HashMap<>();
        Map<String, String> in = new HashMap<>();
        in.put("password", "");
        in.put("ddns_token", "-");
        assertNull(SettingsSpec.validate(in, current(), out));
        assertFalse(out.containsKey("password"));
        assertEquals("", out.get("ddns_token"));
    }

    @Test
    public void normalizesInput() {
        Map<String, String> out = new HashMap<>();
        Map<String, String> in = new HashMap<>();
        in.put("ddns_domain", "MyBaby.duckdns.org");
        in.put("min_free_mb", "0800");
        assertNull(SettingsSpec.validate(in, current(), out));
        assertEquals("mybaby", out.get("ddns_domain"));
        assertEquals("800", out.get("min_free_mb"));
    }

    /** The web form's choices must be exactly the phone's (res/values/strings.xml *_values). */
    @Test
    public void listValuesMatchAndroidResources() throws Exception {
        String xml = new String(Files.readAllBytes(new File("app/src/main/res/values/strings.xml").toPath()), "UTF-8");
        String[][] pairs = {{"resolution", "resolution_values"}, {"fps", "fps_values"}, {"rotation", "rotation_values"},
                {"sensitivity", "sensitivity_values"}, {"post_record_sec", "post_record_values"}, {"segment_min", "segment_values"}};
        for (String[] p : pairs) {
            Matcher m = Pattern.compile("<string-array name=\"" + p[1] + "\">(.*?)</string-array>", Pattern.DOTALL).matcher(xml);
            assertTrue(p[1], m.find());
            List<String> values = new ArrayList<>();
            Matcher item = Pattern.compile("<item>(.*?)</item>").matcher(m.group(1));
            while (item.find()) values.add(item.group(1));
            assertArrayEquals(p[0], values.toArray(new String[0]), SettingsSpec.find(p[0]).values);
        }
    }
}
