package com.z4motioncam;

import static org.junit.Assert.assertEquals;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class Mp4InfoTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private static byte[] box(String type, byte[] payload) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        DataOutputStream d = new DataOutputStream(b);
        d.writeInt(8 + payload.length);
        d.writeBytes(type);
        d.write(payload);
        return b.toByteArray();
    }

    private static byte[] mvhd(int version, long timescale, long duration) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        DataOutputStream d = new DataOutputStream(b);
        d.writeInt(version << 24);
        if (version == 1) {
            d.writeLong(0);
            d.writeLong(0);
            d.writeInt((int) timescale);
            d.writeLong(duration);
        } else {
            d.writeInt(0);
            d.writeInt(0);
            d.writeInt((int) timescale);
            d.writeInt((int) duration);
        }
        d.write(new byte[80]); // rest of mvhd
        return box("mvhd", b.toByteArray());
    }

    private File file(byte[]... boxes) throws IOException {
        File f = tmp.newFile();
        FileOutputStream out = new FileOutputStream(f);
        for (byte[] b : boxes) out.write(b);
        out.close();
        return f;
    }

    @Test
    public void readsDurationWithMoovAtTheEnd() throws IOException {
        // MediaMuxer layout: ftyp, mdat (large), moov last.
        File f = file(box("ftyp", new byte[16]), box("mdat", new byte[5000]),
                box("moov", concat(mvhd(0, 1000, 135_500), box("trak", new byte[40]))));
        assertEquals(135_500, Mp4Info.durationMs(f));
    }

    @Test
    public void readsVersion1Header() throws IOException {
        File f = file(box("ftyp", new byte[16]), box("moov", mvhd(1, 90_000, 90_000L * 185)));
        assertEquals(185_000, Mp4Info.durationMs(f));
    }

    @Test
    public void unknownForBrokenFiles() throws IOException {
        assertEquals(-1, Mp4Info.durationMs(file(new byte[] {1, 2, 3})));
        assertEquals(-1, Mp4Info.durationMs(file(box("ftyp", new byte[16]), box("mdat", new byte[10]))));
        assertEquals(-1, Mp4Info.durationMs(new File(tmp.getRoot(), "missing.mp4")));
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] c = new byte[a.length + b.length];
        System.arraycopy(a, 0, c, 0, a.length);
        System.arraycopy(b, 0, c, a.length, b.length);
        return c;
    }
}
