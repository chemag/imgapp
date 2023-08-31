package com.facebook.imgapp.utils;

import android.content.Context;
import android.util.Log;
import android.os.Bundle;
import android.os.Environment;

import java.nio.file.Files;

public class CliSettings {
    private final static String TAG = "imgapp.clisettings";

    public static final String ENCODE = "encode";
    public static final String DECODE = "decode";
    public static final String INPUT = "input";
    public static final String OUTPUT = "output";
    public static final String WIDTH = "width";
    public static final String HEIGHT = "height";
    public static final String WORKDIR = "workdir";

    private static String mWorkDir = "/sdcard/";

    public static void setWorkDir(Context context, Bundle mExtraData) {
        if (mExtraData != null && mExtraData.containsKey(CliSettings.WORKDIR)) {
            // 1. if user requested something, that is it
            mWorkDir = mExtraData.getString(CliSettings.WORKDIR);
        } else if (Files.isWritable(Environment.getExternalStorageDirectory().toPath())) {
            // 2. use Environment.getExternalStorageDirectory().getPath() if writable
            mWorkDir = Environment.getExternalStorageDirectory().getPath();
        } else if (Files.isWritable(context.getFilesDir().toPath())) {
            // 3. use Context.getFilesDir() if writable
            mWorkDir = context.getFilesDir().getPath();
        } else {
            // 4. no valid path to write: use the default
            ;
        }
        Log.d(TAG, "workdir: " + mWorkDir);
    }

    public static String getWorkDir() {
        return mWorkDir;
    }
}
