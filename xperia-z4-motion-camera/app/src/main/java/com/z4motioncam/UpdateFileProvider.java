package com.z4motioncam;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/**
 * Hands the verified update APK to the system installer on Android 7+, where file:// URIs may not
 * leave the app. Serves nothing but files named update-N.apk from the update folder, read-only, and
 * only to whoever was granted the URI (the installer, via FLAG_GRANT_READ_URI_PERMISSION).
 * (Written against the plain framework: the AndroidX FileProvider is not a dependency.)
 */
public class UpdateFileProvider extends ContentProvider {
    static final String AUTHORITY = "com.z4motioncam.updates";

    static Uri uriFor(File apk) {
        return Uri.parse("content://" + AUTHORITY + "/" + apk.getName());
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    private File resolve(Uri uri) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        if (name == null || !name.matches("update-\\d{1,9}\\.apk") || uri.getPathSegments().size() != 1) {
            throw new FileNotFoundException("not an update file");
        }
        File dir = UpdateManager.updatesDir(getContext());
        File f = new File(dir, name);
        try {
            if (!f.getCanonicalFile().getParentFile().equals(dir.getCanonicalFile()) || !f.isFile()) {
                throw new FileNotFoundException("no such update");
            }
        } catch (IOException e) {
            throw new FileNotFoundException("no such update");
        }
        return f;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("read only");
        return ParcelFileDescriptor.open(resolve(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        try {
            File f = resolve(uri);
            MatrixCursor c = new MatrixCursor(new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
            c.addRow(new Object[] {f.getName(), f.length()});
            return c;
        } catch (FileNotFoundException e) {
            return null;
        }
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }
}
