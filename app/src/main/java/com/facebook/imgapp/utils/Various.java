package com.facebook.imgapp.utils;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.util.Log;
import androidx.core.app.ActivityCompat;
import java.util.ArrayList;

public class Various {
    private final static String TAG = "imgapp.various";

    public static String getFilenameExtension(String filename) {
        int last_dot_location = filename.lastIndexOf('.');
        String extension = (last_dot_location == -1) ? "" : filename.substring(last_dot_location+1);
        return extension;
    }

    public static String[] retrieveNotGrantedPermissions(Context context) {
        ArrayList<String> nonGrantedPerms = new ArrayList<>();
        try {
            String[] manifestPerms = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), PackageManager.GET_PERMISSIONS)
                    .requestedPermissions;
            if (manifestPerms == null || manifestPerms.length == 0) {
                return null;
            }

            for (String permName : manifestPerms) {
                int permission = ActivityCompat.checkSelfPermission(context, permName);
                if (permission != PackageManager.PERMISSION_GRANTED) {
                    nonGrantedPerms.add(permName);
                    Log.d(TAG, "Permission NOT granted: " + permName);
                } else {
                    Log.d(TAG, "Permission granted: " + permName);
                }
            }
        } catch (PackageManager.NameNotFoundException ignored) {
            Log.d(TAG, "PackageManager.NameNotFoundException");
        }
        return nonGrantedPerms.toArray(new String[nonGrantedPerms.size()]);
    }
}
