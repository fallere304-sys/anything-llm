package app.btfiletransfer.obex;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;

/** クライアントとサーバをパイプで直結し、RFCOMM ソケットの代わりにして往復させる。 */
public class ObexLoopbackTest {

    static final class Received {
        String name;
        String type;
        long length;
        final ByteArrayOutputStream data = new ByteArrayOutputStream();
        boolean committed;
        boolean aborted;
    }

    static final class Recorder implements ObexPushServer.Handler {
        final List<Received> files = new ArrayList<>();
        int rejectWith;

        @Override
        public ObexPushServer.Sink onPutStart(String name, String mimeType, long length) throws IOException {
            if (rejectWith != 0) throw new ObexException(rejectWith, "test reject");
            final Received r = new Received();
            r.name = name;
            r.type = mimeType;
            r.length = length;
            files.add(r);
            return new ObexPushServer.Sink() {
                @Override
                public void write(byte[] b, int off, int len) {
                    r.data.write(b, off, len);
                }

                @Override
                public void commit() {
                    r.committed = true;
                }

                @Override
                public void abort() {
                    r.aborted = true;
                }
            };
        }
    }

    /** サーバをバックグラウンドで動かし、接続済みクライアントを返す。 */
    private static ObexPushClient startPair(Recorder rec, int serverMax, int clientMax,
                                            AtomicReference<Throwable> serverError, Thread[] thread)
            throws IOException {
        PipedInputStream serverIn = new PipedInputStream(1 << 17);
        PipedOutputStream clientOut = new PipedOutputStream(serverIn);
        PipedInputStream clientIn = new PipedInputStream(1 << 17);
        PipedOutputStream serverOut = new PipedOutputStream(clientIn);
        final ObexPushServer server = new ObexPushServer(serverIn, serverOut, rec, serverMax);
        thread[0] = new Thread(() -> {
            try {
                server.run();
            } catch (Throwable t) {
                serverError.set(t);
            }
        });
        thread[0].start();
        return new ObexPushClient(clientIn, clientOut, clientMax);
    }

    private static byte[] random(int n, long seed) {
        byte[] b = new byte[n];
        new Random(seed).nextBytes(b);
        return b;
    }

    @Test
    public void sendsMultipleFilesInOneSession() throws Exception {
        Recorder rec = new Recorder();
        AtomicReference<Throwable> err = new AtomicReference<>();
        Thread[] t = new Thread[1];
        // サーバ側の最大パケットを小さくして分割・ネゴシエーションを確認
        ObexPushClient client = startPair(rec, 512, ObexConstants.MAX_PACKET_SIZE, err, t);
        client.connect();
        assertEquals(512, client.negotiatedMaxPacket());

        byte[] a = random(300_000, 1);
        byte[] b = new byte[0];
        byte[] c = random(512 - 6, 3); // ちょうどチャンク境界
        final long[] lastProgress = {0};
        client.put("写真 2024.jpg", "image/jpeg", a.length, new ByteArrayInputStream(a),
                (sent, total) -> lastProgress[0] = sent);
        client.put("empty.txt", null, -1, new ByteArrayInputStream(b), null);
        client.put("😀emoji.bin", "application/octet-stream", c.length, new ByteArrayInputStream(c), null);
        client.disconnect();
        t[0].join(5000);

        if (err.get() != null) throw new AssertionError(err.get());
        assertEquals(a.length, lastProgress[0]);
        assertEquals(3, rec.files.size());

        Received ra = rec.files.get(0);
        assertEquals("写真 2024.jpg", ra.name);
        assertEquals("image/jpeg", ra.type);
        assertEquals(a.length, ra.length);
        assertArrayEquals(a, ra.data.toByteArray());
        assertTrue(ra.committed);

        Received rb = rec.files.get(1);
        assertEquals("empty.txt", rb.name);
        assertEquals(-1, rb.length);
        assertEquals(0, rb.data.size());
        assertTrue(rb.committed);

        Received rc = rec.files.get(2);
        assertEquals("😀emoji.bin", rc.name);
        assertArrayEquals(c, rc.data.toByteArray());
    }

    @Test
    public void rejectionIsReportedToClient() throws Exception {
        Recorder rec = new Recorder();
        rec.rejectWith = ObexConstants.RSP_ENTITY_TOO_LARGE;
        AtomicReference<Throwable> err = new AtomicReference<>();
        Thread[] t = new Thread[1];
        ObexPushClient client = startPair(rec, 1024, 1024, err, t);
        client.connect();
        try {
            client.put("big.iso", null, 10_000, new ByteArrayInputStream(new byte[10_000]), null);
            fail("拒否されるはず");
        } catch (ObexException e) {
            assertEquals(ObexConstants.RSP_ENTITY_TOO_LARGE, e.responseCode);
        }
        // セッションは継続できる
        rec.rejectWith = 0;
        client.put("ok.txt", null, 2, new ByteArrayInputStream(new byte[] {1, 2}), null);
        client.disconnect();
        t[0].join(5000);
        if (err.get() != null) throw new AssertionError(err.get());
        assertEquals(1, rec.files.size());
        assertEquals("ok.txt", rec.files.get(0).name);
    }

    @Test
    public void connectPacketMatchesSpec() throws Exception {
        ByteArrayOutputStream wire = new ByteArrayOutputStream();
        ObexPacket.write(wire, ObexConstants.OP_CONNECT, ObexPacket.connectFields(0xFFFE));
        assertArrayEquals(new byte[] {(byte) 0x80, 0x00, 0x07, 0x10, 0x00, (byte) 0xFF, (byte) 0xFE},
                wire.toByteArray());
    }

    @Test
    public void nameHeaderIsNullTerminatedUtf16() throws Exception {
        ByteArrayOutputStream h = new ByteArrayOutputStream();
        ObexHeaders.writeUnicode(h, ObexConstants.HI_NAME, "a.txt");
        byte[] expected = {0x01, 0x00, 0x0F, 0, 'a', 0, '.', 0, 't', 0, 'x', 0, 't', 0, 0};
        assertArrayEquals(expected, h.toByteArray());
        assertEquals(expected.length, ObexHeaders.unicodeSize("a.txt"));
        assertEquals("a.txt", ObexHeaders.parse(expected, 0, expected.length).name);
    }

    @Test
    public void unknownHeadersAreSkipped() throws Exception {
        ByteArrayOutputStream h = new ByteArrayOutputStream();
        ObexHeaders.writeBytes(h, 0x4C, new byte[] {9, 9, 9}, 0, 3); // Application Parameters
        h.write(0x97); // SRM (1byte ヘッダ)
        h.write(0x01);
        ObexHeaders.writeInt(h, ObexConstants.HI_LENGTH, 0xFFFFFFFFL);
        ObexHeaders.writeUnicode(h, 0x05, "description");
        byte[] raw = h.toByteArray();
        ObexHeaders parsed = ObexHeaders.parse(raw, 0, raw.length);
        assertEquals(0xFFFFFFFFL, parsed.length);
        assertEquals(null, parsed.name);
    }

    @Test(expected = ObexException.class)
    public void truncatedHeaderIsRejected() throws Exception {
        byte[] raw = {0x01, 0x00, 0x20, 0x00};
        ObexHeaders.parse(raw, 0, raw.length);
    }
}
