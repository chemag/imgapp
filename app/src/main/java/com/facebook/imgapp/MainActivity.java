package com.facebook.imgapp;

import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Bitmap.CompressFormat;
import android.graphics.ColorSpace;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Process;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import android.widget.TableLayout;
import android.widget.Toast;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;

import com.facebook.imgapp.utils.CliSettings;
import com.facebook.imgapp.utils.Various;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.ArrayList;


// MainActivity: This is the activity run from the CLI.
// $ adb shell am start -W -e <key1> <val1> -e <key2> <val2> com.facebook.imgapp/.MainActivity
public class MainActivity extends AppCompatActivity {
    private final static String TAG = "imgapp.main";
    private final Object mTestLockObject = new Object();
    int mUIHoldtimeSec = 0;
    boolean mLayoutDone = false;
    TableLayout mTable;
    private Bundle mInputParameters;


    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // 1. android app glue
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_visualize);

        // 2. get list of non-granted permissions
        String[] permissions = Various.retrieveNotGrantedPermissions(this);
        if (permissions != null && permissions.length > 0) {
            int REQUEST_ALL_PERMISSIONS = 0x4562;
            try {
                ActivityCompat.requestPermissions(this, permissions, REQUEST_ALL_PERMISSIONS);
            } catch (java.lang.RuntimeException ex) {
                // some devices (e.g. headless) may not have the permission UI,
                // which gets triggered here whether the permission is already
                // granted or not. In that case, we can avoid punting, in case
                // the user already granted the permissions using the CLI
                // (adb shell pm grant ...)
                Log.w(TAG, "no access to permissions UI.");
            }
        }

        // 3. read input parameters
        if (!getInputParameters()) {
            Log.e(TAG, "no input parameters: activity must run from CLI");
            Toast.makeText(this, "no input parameters: activity must run from CLI,", Toast.LENGTH_LONG).show();
            // System.exit(-1);
        }

        // 4. set external storage directory
        CliSettings.setWorkDir(this, mInputParameters);

        // 5. check permission strategy
        if (Build.VERSION.SDK_INT >= 30 && !Environment.isExternalStorageManager()) {
            Log.d(TAG, "Check ExternalStorageManager");
            // request the external storage manager permission
            Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
            Uri uri = Uri.fromParts("package", getPackageName(), null);
            intent.setData(uri);
            try {
                startActivity(intent);
            } catch (android.content.ActivityNotFoundException ex) {
                Log.e(TAG, "No activity found for handling the permission intent: " + ex.getLocalizedMessage());
                // System.exit(-1);
                Toast.makeText(this, "Missing MANAGE_APP_ALL_FILES_ACCESS_PERMISSION request,", Toast.LENGTH_LONG).show();
            }
        }
        mTable = findViewById(R.id.viewTable);
        Log.d(TAG, "Passed all permission checks");

        // 6. run the test in a separate thread to (a) avoid UI thread blocking
        // and (b) allow functionality in the test that is not allowed in the
        // UI thread
        (new Thread(new Runnable() {
            @Override
            public void run() {
                performImageCodecTest();
                Log.d(TAG, "Test done");
                exit();
            }
        })).start();
    }


    public void exit() {
        Log.d(TAG, "exit: Finish and remove");
        finishAndRemoveTask();
        Process.killProcess(Process.myPid());
        Log.d(TAG, "EXIT");
    }

    /**
     * Check if a test has fired up this activity.
     *
     * @return true if extra settings are available
     */
    private boolean getInputParameters() {
        Intent intent = getIntent();
        Bundle bundle = intent.getExtras();
        if (bundle != null) {
            mInputParameters = bundle;
            return true;
        }

        return false;
    }

    private Bitmap readRawFileToBitmap(String inputPath, int width, int height) {
        // 1. read the input raw file
        byte[] bytes = null;
        try {
            File file = new File(inputPath);
            int length = (int) file.length();
            FileInputStream fis = new FileInputStream(file);
            bytes = new byte[length];
            fis.read(bytes);
            // String test_path_contents = new String(bytes);
        } catch (FileNotFoundException e1) { 
            e1.printStackTrace(); 
        } catch (IOException e1) { 
            e1.printStackTrace(); 
        } 

        // 2. convert the raw byte array to a bitmap
        // https://stackoverflow.com/a/5636912
        Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(bytes));
        return bitmap;
    }

    private BitmapFactory.Options getBitmapFactoryOptions() {
        BitmapFactory.Options options = new BitmapFactory.Options();
        if (mInputParameters.containsKey(CliSettings.INPREFERREDCOLORSPACE)) {
            if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.O) {
                Log.d(TAG, "getBitmapFactoryOptions(): build version (" + android.os.Build.VERSION.SDK_INT + ") does not support inPreferredColorSpace");
            } else {
                String name = mInputParameters.getString(CliSettings.INPREFERREDCOLORSPACE);
                try {
                    ColorSpace.Named value = ColorSpace.Named.valueOf(name);
                    options.inPreferredColorSpace = ColorSpace.get(value);
                } catch (IllegalArgumentException e1) {
                    Log.e(TAG, "getBitmapFactoryOptions(): invalid inPreferredColorSpace parameter: " + name);
                    exit();
                }
            }
        }
        return options;
    }

    private Bitmap readEncodedFileToBitmap(String inputPath) {
        // https://stackoverflow.com/a/19172326
        Bitmap bitmap = BitmapFactory.decodeFile(inputPath, getBitmapFactoryOptions());
        return bitmap;
    }

    private boolean writeBitmapToEncodedFile(Bitmap bitmap, String outputPath) {
        // get the compress format
        CompressFormat compressFormat = null;
        if (! mInputParameters.containsKey(CliSettings.COMPRESSFORMAT)) {
            // default value
            compressFormat = CompressFormat.PNG;
        } else {
            String compressFormatName = null;
            try {
                compressFormatName = mInputParameters.getString(CliSettings.COMPRESSFORMAT);
                compressFormat = CompressFormat.valueOf(compressFormatName);
            } catch (IllegalArgumentException e1) {
                Log.e(TAG, "writeBitmapToEncodedFile(): invalid CompressFormat parameter: " + compressFormatName);
                exit();
            }
        }
        // get the compress quality
        int compressQuality = -1;
        if (! mInputParameters.containsKey(CliSettings.COMPRESSQUALITY)) {
            // default value
            compressQuality = 0;
        } else {
            String compressQualityStr = mInputParameters.getString(CliSettings.COMPRESSQUALITY, "0");
            try {
                compressQuality = Integer.parseInt(compressQualityStr);
            } catch (java.lang.NumberFormatException ex) {
                Log.e(TAG, "error: invalid compressQuality parameter: " + compressQualityStr);
                return false;
            }
        }
        // https://stackoverflow.com/a/7780289
        // 1. encode bitmap into byte array
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        bitmap.compress(compressFormat, compressQuality, bos);
        byte[] byteArray = bos.toByteArray();

        // 2. write byte array to file
        try {
            FileOutputStream stream = new FileOutputStream(outputPath); 
            stream.write(byteArray); 
        } catch (FileNotFoundException e1) { 
            e1.printStackTrace(); 
        } catch (IOException e1) { 
            e1.printStackTrace(); 
        } 
        return true;
    }

    private boolean writeBitmapToRawFile(Bitmap bitmap, String outputPath) {
        int width = bitmap.getWidth();
        int height = bitmap.getHeight();
        int numberOfPixels = width * height;

        // create output streams
        FileOutputStream fileOutputStream = null;
        BufferedOutputStream bufferedOutputStream = null;
        Log.d(TAG, "writeBitmapToRawFile(bitmap: " + width + "x" + height + ", outputPath: " + outputPath + ")");
        try {
            fileOutputStream = new FileOutputStream(outputPath);
            bufferedOutputStream = new BufferedOutputStream(fileOutputStream);

            // write all the bits using packed RGBA
            for (int y = 0; y < height; y++) {
                for (int x = 0; x < width; x++) {
                    int pixel = bitmap.getPixel(x, y);
                    int red = (pixel >> 16) & 0xff;
                    int green = (pixel >> 8) & 0xff;
                    int blue = pixel & 0xff;
                    int alpha = (pixel >> 24) & 0xff;
                    bufferedOutputStream.write(red);
                    bufferedOutputStream.write(green);
                    bufferedOutputStream.write(blue);
                    bufferedOutputStream.write(alpha);
                }
            }
            // clean up
            bufferedOutputStream.flush();
            bufferedOutputStream.close();
        } catch (Exception e) {
            e.printStackTrace();
        }
        return true;
    }


    /**
     * Run everything found in the bundle data
     * and exit.
     */
    private void performImageCodecTest() {
        // we need a single encode or decode function
        if ((! mInputParameters.containsKey(CliSettings.ENCODE)) && (! mInputParameters.containsKey(CliSettings.DECODE))) {
            Log.e(TAG, "error: need to specify either a \"encode\" or a \"decode\" parameter");
            return;
        }
        if ((mInputParameters.containsKey(CliSettings.ENCODE)) && (mInputParameters.containsKey(CliSettings.DECODE))) {
            Log.e(TAG, "error: need to specify only one parameter in \"encode\" and \"decode\"");
            return;
        }

        // we need an input file
        if (! mInputParameters.containsKey(CliSettings.INPUT)) {
            Log.e(TAG, "error: need to specify an \"input\" paramter");
            return;
        }
        String inputPath = mInputParameters.getString(CliSettings.INPUT);

        // we need an output file
        if (! mInputParameters.containsKey(CliSettings.OUTPUT)) {
            Log.e(TAG, "error: need to specify an \"output\" paramter");
            return;
        }
        String outputPath = mInputParameters.getString(CliSettings.OUTPUT);

        if (mInputParameters.containsKey(CliSettings.ENCODE)) {
            // for raw images, we need both width and height
            if ((! mInputParameters.containsKey(CliSettings.WIDTH)) || (! mInputParameters.containsKey(CliSettings.HEIGHT))) {
                Log.e(TAG, "error: need to specify both a \"width\" and a \"height\" parameter");
                return;
            }
            int width = 0;
            int height = 0;
            String widthStr = mInputParameters.getString(CliSettings.WIDTH, "0");
            try {
                width = Integer.parseInt(widthStr);
            } catch (java.lang.NumberFormatException ex) {
                Log.e(TAG, "error: invalid width parameter: " + widthStr);
                return;
            }
            String heightStr = mInputParameters.getString(CliSettings.HEIGHT, "0");
            try {
                height = Integer.parseInt(heightStr);
            } catch (java.lang.NumberFormatException ex) {
                Log.e(TAG, "error: invalid height parameter: " + heightStr);
                return;
            }
            Log.d(TAG, "performImageCodecTest: encoding " + inputPath + " (" + width + "x" + height + ") into " + outputPath);
            // 1. read raw file into bitmap
            Bitmap bitmap = readRawFileToBitmap(inputPath, width, height);
            // 2. write bitmap into encoded file
            writeBitmapToEncodedFile(bitmap, outputPath);

        } else {  // decode
            // 1. read encoded file into bitmap
            Log.d(TAG, "performImageCodecTest: decoding " + inputPath + " into " + outputPath);
            Bitmap bitmap = readEncodedFileToBitmap(inputPath);
            if (bitmap == null) {
                Log.e(TAG, "error: cannot read/decode " + inputPath);
                return;
            }
            // 2. write bitmap into raw file
            writeBitmapToRawFile(bitmap, outputPath);
        }
    }
}
