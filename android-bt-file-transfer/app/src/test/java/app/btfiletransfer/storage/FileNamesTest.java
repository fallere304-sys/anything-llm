package app.btfiletransfer.storage;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class FileNamesTest {
    @Test
    public void stripsPathsAndTraversal() {
        assertEquals("passwd", FileNames.sanitize("../../etc/passwd", "x"));
        assertEquals("evil.exe", FileNames.sanitize("C:\\Windows\\..\\evil.exe", "x"));
        assertEquals("x", FileNames.sanitize("..", "x"));
        assertEquals("x", FileNames.sanitize("   ", "x"));
        assertEquals("x", FileNames.sanitize(null, "x"));
        assertEquals("bashrc", FileNames.sanitize(".bashrc", "x"));
    }

    @Test
    public void replacesReservedCharacters() {
        assertEquals("a_b_c_.txt", FileNames.sanitize("a:b*c\u0001.txt", "x"));
        assertEquals("資料 (最終).pdf", FileNames.sanitize("資料 (最終).pdf", "x"));
    }

    @Test
    public void truncatesKeepingExtension() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 300; i++) sb.append('あ');
        String s = FileNames.sanitize(sb + ".docx", "x");
        assertEquals(FileNames.MAX_LENGTH, s.length());
        assertTrue(s.endsWith(".docx"));
    }

    @Test
    public void numbersDuplicates() {
        assertEquals("a (2).txt", FileNames.numbered("a.txt", 2));
        assertEquals("README (3)", FileNames.numbered("README", 3));
    }
}
