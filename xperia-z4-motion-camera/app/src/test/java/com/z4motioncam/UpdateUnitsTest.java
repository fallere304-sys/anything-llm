package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Arrays;
import java.util.Map;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/** UpdateManifest, UpdateSchedule, UpdateStore, ApkCheck, InstallGuide, ClockWatch and the new settings. */
public class UpdateUnitsTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private static final String SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    private static UpdateManifest parse(String json) throws UpdateException {
        return UpdateManifest.parse(json.getBytes(StandardCharsets.UTF_8), "https://example.com/dir/update.json");
    }

    private static String json(String extra) {
        return "{\"versionCode\":12,\"versionName\":\"1.12\",\"apk\":\"a.apk\",\"sha256\":\"" + SHA + "\",\"size\":50000" + extra + "}";
    }

    private static void rejects(String j, UpdateException.Kind kind) {
        try {
            parse(j);
            fail(j);
        } catch (UpdateException e) {
            assertEquals(j, kind, e.kind);
        }
    }

    // ---- manifest ----

    @Test
    public void parsesAManifest() throws Exception {
        UpdateManifest m = parse(json(",\"notes\":\"bug fixes\",\"package\":\"com.z4motioncam\""));
        assertEquals(12, m.versionCode);
        assertEquals("1.12", m.versionName);
        assertEquals("https://example.com/dir/a.apk", m.apkUrl);
        assertEquals(SHA, m.sha256);
        assertEquals(50000, m.size);
        assertEquals("bug fixes", m.notes);
    }

    @Test
    public void absoluteApkUrlIsKept() throws Exception {
        UpdateManifest m = parse(json("").replace("a.apk", "https://cdn.example.org/x/b.apk"));
        assertEquals("https://cdn.example.org/x/b.apk", m.apkUrl);
    }

    @Test
    public void shaIsNormalizedToLowerCase() throws Exception {
        assertEquals(SHA, parse(json("").replace(SHA, SHA.toUpperCase())).sha256);
    }

    @Test
    public void rejectsBadManifests() {
        rejects("", UpdateException.Kind.BAD_MANIFEST);
        rejects("not json", UpdateException.Kind.BAD_MANIFEST);
        rejects(json("").replace("\"versionCode\":12,", ""), UpdateException.Kind.BAD_MANIFEST);
        rejects(json("").replace("12", "0"), UpdateException.Kind.BAD_MANIFEST);
        rejects(json("").replace(SHA, "abc"), UpdateException.Kind.BAD_MANIFEST);
        rejects(json("").replace("50000", "100"), UpdateException.Kind.BAD_MANIFEST);
        rejects(json("").replace("50000", "999999999999"), UpdateException.Kind.BAD_MANIFEST);
        rejects(json("").replace("\"a.apk\"", "\"\""), UpdateException.Kind.BAD_MANIFEST);
        rejects(json(",\"package\":\"com.evil\""), UpdateException.Kind.BAD_MANIFEST);
    }

    @Test
    public void apkOverPlainHttpIsRefused() {
        rejects(json("").replace("a.apk", "http://example.com/a.apk"), UpdateException.Kind.NOT_HTTPS);
    }

    @Test
    public void tooBigManifestIsRefused() {
        byte[] big = new byte[UpdateManifest.MAX_JSON_BYTES + 1];
        Arrays.fill(big, (byte) ' ');
        try {
            UpdateManifest.parse(big, "https://example.com/u.json");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.BAD_MANIFEST, e.kind);
        }
    }

    @Test
    public void textFieldsAreCleaned() throws Exception {
        UpdateManifest m = parse(json(",\"notes\":\"line1\\nline2\\u0007x\""));
        assertFalse(m.notes.contains("\n"));
        assertFalse(m.notes.contains("\u0007"));
        StringBuilder many = new StringBuilder();
        for (int i = 0; i < 1000; i++) many.append('あ');
        assertEquals(400, parse(json(",\"notes\":\"" + many + "\"")).notes.length());
    }

    @Test
    public void manifestRoundTripsThroughJson() throws Exception {
        UpdateManifest m = parse(json(",\"notes\":\"n\""));
        UpdateManifest again = UpdateManifest.parse(m.toJson().toString().getBytes(StandardCharsets.UTF_8), m.apkUrl);
        assertEquals(m.apkUrl, again.apkUrl);
        assertEquals(m.sha256, again.sha256);
        assertEquals(m.versionCode, again.versionCode);
    }

    @Test
    public void httpsUrlCheck() {
        assertTrue(UpdateHttp.isHttpsUrl("https://example.com/a.json"));
        assertTrue(UpdateHttp.isHttpsUrl("HTTPS://Example.com:8443/x"));
        assertFalse(UpdateHttp.isHttpsUrl("http://example.com/a.json"));
        assertFalse(UpdateHttp.isHttpsUrl("ftp://example.com"));
        assertFalse(UpdateHttp.isHttpsUrl("https://"));
        assertFalse(UpdateHttp.isHttpsUrl(""));
        assertFalse(UpdateHttp.isHttpsUrl(null));
        assertFalse(UpdateHttp.isHttpsUrl("//example.com/x"));
    }

    @Test
    public void contentRangeParsing() {
        assertTrue(UpdateHttp.rangeStartsAt("bytes 100-999/1000", 100));
        assertFalse(UpdateHttp.rangeStartsAt("bytes 0-999/1000", 100));
        assertFalse(UpdateHttp.rangeStartsAt(null, 0));
        assertFalse(UpdateHttp.rangeStartsAt("items 1-2/3", 1));
        assertFalse(UpdateHttp.rangeStartsAt("bytes */1000", 0));
    }

    // ---- schedule ----

    @Test
    public void firstCheckIsDue() {
        assertTrue(UpdateSchedule.due(1_000, 0, 0, 0));
    }

    @Test
    public void checksOnceADay() {
        long t = 1_000_000_000_000L;
        assertFalse(UpdateSchedule.due(t + 23 * 3600_000L, t, t, 0));
        assertTrue(UpdateSchedule.due(t + 24 * 3600_000L, t, t, 0));
        assertEquals(t + UpdateSchedule.INTERVAL_MS, UpdateSchedule.nextMs(t + 1000, t, t, 0));
        assertEquals(0, UpdateSchedule.nextMs(t + UpdateSchedule.INTERVAL_MS, t, t, 0));
    }

    @Test
    public void failuresBackOff() {
        assertEquals(30 * 60_000L, UpdateSchedule.retryDelayMs(1));
        assertEquals(60 * 60_000L, UpdateSchedule.retryDelayMs(2));
        assertEquals(120 * 60_000L, UpdateSchedule.retryDelayMs(3));
        assertEquals(6 * 3600_000L, UpdateSchedule.retryDelayMs(50));
        long t = 1_000_000_000_000L;
        assertFalse(UpdateSchedule.due(t + 29 * 60_000L, 0, t, 1));
        assertTrue(UpdateSchedule.due(t + 30 * 60_000L, 0, t, 1));
        assertFalse(UpdateSchedule.due(t + 59 * 60_000L, 0, t, 2));
    }

    @Test
    public void aClockSetBackNeverFreezesTheSchedule() {
        long t = 1_000_000_000_000L;
        assertTrue(UpdateSchedule.due(t - 10 * 24 * 3600_000L, t, t, 0));
        assertTrue(UpdateSchedule.due(t - 3600_000L, 0, t, 3));
    }

    // ---- store ----

    @Test
    public void metaRoundTripAndMatch() throws Exception {
        UpdateStore s = new UpdateStore(tmp.newFolder("u"));
        UpdateManifest m = parse(json(""));
        s.writeMeta(12, m);
        UpdateStore.Meta meta = s.readMeta(12);
        assertTrue(meta.matches(m));
        assertNull(s.readMeta(13));
        UpdateManifest other = parse(json("").replace("50000", "50001"));
        assertFalse(meta.matches(other));
    }

    @Test
    public void damagedMetaIsIgnored() throws Exception {
        UpdateStore s = new UpdateStore(tmp.newFolder("u2"));
        java.nio.file.Files.write(new File(s.dir(), "update-5.meta").toPath(), "only one line".getBytes());
        assertNull(s.readMeta(5));
        java.nio.file.Files.write(new File(s.dir(), "update-6.meta").toPath(), "u\ns\nnotanumber\n".getBytes());
        assertNull(s.readMeta(6));
    }

    @Test
    public void cleanupKeepsOnlyTheWantedVersion() throws Exception {
        UpdateStore s = new UpdateStore(tmp.newFolder("u3"));
        for (int v : new int[] {12, 13}) {
            new File(s.dir(), "update-" + v + ".apk").createNewFile();
            new File(s.dir(), "update-" + v + ".meta").createNewFile();
        }
        s.cleanup(11, 13, System.currentTimeMillis());
        assertFalse(s.ready(12).exists());
        assertTrue(s.ready(13).exists());
    }

    @Test
    public void futureDatedPartsAreDropped() throws Exception {
        UpdateStore s = new UpdateStore(tmp.newFolder("u4"));
        File part = s.part(13);
        part.createNewFile();
        part.setLastModified(System.currentTimeMillis() + 5L * 24 * 3600_000L); // clock was wrong when it was written
        s.cleanup(11, -1, System.currentTimeMillis());
        assertFalse(part.exists());
    }

    @Test
    public void hugeVersionNumbersInNamesDoNotCrash() throws Exception {
        UpdateStore s = new UpdateStore(tmp.newFolder("u5"));
        new File(s.dir(), "update-99999999999999.apk").createNewFile();
        s.cleanup(11, -1, System.currentTimeMillis());
        assertEquals(-1, s.newestReady(11));
    }

    // ---- ApkCheck ----

    @Test
    public void sha256OfKnownText() {
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                ApkCheck.sha256Hex("abc".getBytes(StandardCharsets.US_ASCII)));
    }

    @Test
    public void signerSetsMustBeEqualAndNonEmpty() {
        assertTrue(ApkCheck.sameSigners(new HashSet<>(Arrays.asList("a")), new HashSet<>(Arrays.asList("a"))));
        assertFalse(ApkCheck.sameSigners(new HashSet<>(Arrays.asList("a")), new HashSet<>(Arrays.asList("b"))));
        assertFalse(ApkCheck.sameSigners(new HashSet<String>(), new HashSet<String>()));
        assertFalse(ApkCheck.sameSigners(null, new HashSet<>(Arrays.asList("a"))));
        assertFalse(ApkCheck.sameSigners(new HashSet<>(Arrays.asList("a")), new HashSet<>(Arrays.asList("a", "b"))));
    }

    @Test
    public void tinyAndMissingFilesFailTheFormatCheck() throws Exception {
        File small = tmp.newFile("small.apk");
        java.nio.file.Files.write(small.toPath(), new byte[100]);
        try {
            ApkCheck.checkFormat(small, 0);
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.BAD_FORMAT, e.kind);
        }
    }

    @Test
    public void aRealLookingApkPassesTheFormatCheck() throws Exception {
        File f = tmp.newFile("ok.apk");
        byte[] apk = UpdateFixture.apk("com.z4motioncam", 12, "aa", 1);
        java.nio.file.Files.write(f.toPath(), apk);
        ApkCheck.checkFormat(f, apk.length);
        try {
            ApkCheck.checkFormat(f, apk.length + 1);
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.BAD_FORMAT, e.kind);
        }
    }

    // ---- guide ----

    @Test
    public void unknownSourcesAdvice() {
        assertNull(InstallGuide.advice(22, true, false));
        assertTrue(InstallGuide.advice(22, false, false).contains("提供元不明のアプリ"));
        assertNull(InstallGuide.advice(26, false, true));
        assertTrue(InstallGuide.advice(26, true, false).contains("不明なアプリのインストール"));
        assertNull(InstallGuide.advice(25, true, false));
    }

    // ---- clock watch ----

    @Test
    public void clockWatchNoticesJumpsButNotDrift() {
        ClockWatch w = new ClockWatch();
        assertEquals(0, w.observe(1_000_000, 500));
        assertEquals(0, w.observe(1_015_000, 15_500)); // 15 s on both clocks
        assertEquals(0, w.observe(1_075_000 + 30_000, 75_500)); // 30 s of drift: ignored
        long jump = w.observe(1_075_000 + 30_000 + 15_000 + 3_600_000L, 90_500);
        assertTrue(jump > 3_500_000L);
        long back = w.observe(1_000_000, 105_500);
        assertTrue(back < -3_000_000L);
        assertEquals(0, w.observe(1_015_000, 120_500));
    }

    @Test
    public void clockJumpIsDescribed() {
        assertTrue(ClockWatch.describe(3_600_000L).contains("1時間0分進みました"));
        assertTrue(ClockWatch.describe(-5 * 60_000L).contains("5分戻りました"));
        assertTrue(ClockWatch.describe(2L * 24 * 3600_000L).contains("2日"));
    }

    // ---- new settings ----

    private static String check(String... kv) {
        Map<String, String> cur = new HashMap<>();
        for (SettingsSpec.Field f : SettingsSpec.FIELDS) cur.put(f.key, f.def);
        Map<String, String> in = new HashMap<>();
        for (int i = 0; i < kv.length; i += 2) in.put(kv[i], kv[i + 1]);
        return SettingsSpec.validate(in, cur, new HashMap<String, String>());
    }

    @Test
    public void acceptsTheNewSettings() {
        assertNull(check("detect_mode", "person", "person_sens", "3"));
        assertNull(check("low_batt_pause", "20", "low_batt_resume", "60"));
        assertNull(check("low_batt_pause", "0"));
        assertNull(check("quiet_start", "23:00", "quiet_end", "1:00", "quiet_keep_pct", "90", "quiet_enabled", "false"));
        assertNull(check("update_url", "https://example.com/z4/update.json", "update_auto", "false"));
        assertNull(check("update_sha256", SHA.toUpperCase()));
        assertNull(check("update_sha256", ""));
        assertNull(check("update_url", ""));
    }

    @Test
    public void rejectsBadNewSettings() {
        assertTrue(check("detect_mode", "face").contains("detect_mode"));
        assertTrue(check("person_sens", "4").contains("person_sens"));
        assertTrue(check("low_batt_pause", "51").contains("low_batt_pause"));
        assertTrue(check("low_batt_pause", "30", "low_batt_resume", "33").contains("5%"));
        assertTrue(check("quiet_start", "25:00").contains("時刻"));
        assertTrue(check("quiet_end", "abc").contains("時刻"));
        assertTrue(check("quiet_keep_pct", "10").contains("quiet_keep_pct"));
        assertTrue(check("update_url", "http://example.com/u.json").contains("https"));
        assertTrue(check("update_url", "https://exa mple.com/u.json").contains("https"));
        assertTrue(check("update_url", "javascript:alert(1)").contains("https"));
        assertTrue(check("update_sha256", "xyz").contains("SHA-256"));
    }

    @Test
    public void normalizesTimesAndHashes() {
        Map<String, String> out = new HashMap<>();
        Map<String, String> in = new HashMap<>();
        in.put("quiet_start", "3:05");
        in.put("update_sha256", "AB:CD " + SHA.substring(4));
        Map<String, String> cur = new HashMap<>();
        for (SettingsSpec.Field f : SettingsSpec.FIELDS) cur.put(f.key, f.def);
        assertNull(SettingsSpec.validate(in, cur, out));
        assertEquals("03:05", out.get("quiet_start"));
        assertEquals("abcd" + SHA.substring(4), out.get("update_sha256"));
    }

    @Test
    public void newDefaultsMatchTheRequest() {
        assertEquals("motion", SettingsSpec.find("detect_mode").def);
        assertEquals("18", SettingsSpec.find("low_batt_pause").def);
        assertEquals("50", SettingsSpec.find("low_batt_resume").def);
        assertEquals("03:00", SettingsSpec.find("quiet_start").def);
        assertEquals("04:00", SettingsSpec.find("quiet_end").def);
        assertEquals("80", SettingsSpec.find("quiet_keep_pct").def);
        assertEquals("true", SettingsSpec.find("quiet_enabled").def);
    }

    @Test
    public void newListValuesMatchAndroidResources() throws Exception {
        String xml = new String(java.nio.file.Files.readAllBytes(new File("app/src/main/res/values/strings.xml").toPath()), "UTF-8");
        String[][] pairs = {{"detect_mode", "detect_mode_values"}, {"person_sens", "person_sens_values"}};
        for (String[] p : pairs) {
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("<string-array name=\"" + p[1] + "\">(.*?)</string-array>",
                    java.util.regex.Pattern.DOTALL).matcher(xml);
            assertTrue(p[1], m.find());
            java.util.List<String> values = new java.util.ArrayList<>();
            java.util.regex.Matcher item = java.util.regex.Pattern.compile("<item>(.*?)</item>").matcher(m.group(1));
            while (item.find()) values.add(item.group(1));
            org.junit.Assert.assertArrayEquals(p[0], values.toArray(new String[0]), SettingsSpec.find(p[0]).values);
        }
    }

    @Test
    public void preferencesXmlHasEveryNewKey() throws Exception {
        String xml = new String(java.nio.file.Files.readAllBytes(new File("app/src/main/res/xml/preferences.xml").toPath()), "UTF-8");
        for (SettingsSpec.Field f : SettingsSpec.FIELDS) {
            assertTrue(f.key, xml.contains("android:key=\"" + f.key + "\""));
        }
    }

    @Test
    public void defaultUpdateUrlIsHttpsAndHasNoPrivateAddress() throws Exception {
        String xml = new String(java.nio.file.Files.readAllBytes(new File("app/src/main/res/values/strings.xml").toPath()), "UTF-8");
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("name=\"default_update_url\"[^>]*>([^<]*)<").matcher(xml);
        assertTrue(m.find());
        assertTrue(UpdateHttp.isHttpsUrl(m.group(1)));
    }
}
