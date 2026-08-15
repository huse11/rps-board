package com.rps.board;

import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.KeyEvent;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.FileProvider;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONObject;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * RPS 板块看板 - WebView 套壳
 * 唯一数据源: 阿里云服务器 (http://47.99.178.225:8000)
 * 下拉刷新: 默认启用; 前端切到"每日复盘"页(内容长,下滑浏览易误触)时通过 JS bridge 禁用, 其他页恢复
 * 自动更新: 启动时检查服务器 static/app/version.json, 版本号更高则弹窗下载安装
 */
public class MainActivity extends AppCompatActivity {

    private static final String PRIMARY_URL = "http://47.99.178.225:8000/";
    private static final String UPDATE_URL = "http://47.99.178.225:8000/static/app/version.json";
    private static final int VERSION_CODE = BuildConfig.VERSION_CODE;

    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webView);
        swipeRefresh = findViewById(R.id.swipeRefresh);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                swipeRefresh.setRefreshing(false);
            }
        });

        // JS bridge: 前端 switchRecTab 切到"每日复盘"时禁用下拉刷新, 其他 Tab 恢复
        webView.addJavascriptInterface(new RefreshBridge(), "Android");

        swipeRefresh.setOnRefreshListener(() -> webView.reload());

        webView.loadUrl(PRIMARY_URL);

        checkUpdate();
    }

    /** 前端可调: Android.setSwipeRefreshEnabled(false/true) */
    private class RefreshBridge {
        @JavascriptInterface
        public void setSwipeRefreshEnabled(final boolean enabled) {
            runOnUiThread(() -> swipeRefresh.setEnabled(enabled));
        }
    }

    // ===== 自动更新: 检查 version.json → 弹窗 → 下载 → 安装 =====

    private void checkUpdate() {
        new Thread(() -> {
            try {
                URL url = new URL(UPDATE_URL);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(8000);
                conn.setRequestProperty("Cache-Control", "no-cache");
                String body = readAll(conn.getInputStream());
                conn.disconnect();
                JSONObject jo = new JSONObject(body);
                final int remoteCode = jo.getInt("versionCode");
                final String remoteName = jo.optString("versionName", "");
                final String apkUrl = jo.getString("url");
                final String note = jo.optString("note", "");
                // 已忽略的版本号: 点「以后再说」后记住, 同一版本不再重复弹窗(发新版本时仍会提示)
                final int ignored = getSharedPreferences("update_prefs", MODE_PRIVATE)
                        .getInt("ignored_version", 0);
                if (remoteCode > VERSION_CODE && remoteCode > ignored) {
                    runOnUiThread(() -> showUpdateDialog(remoteName, note, apkUrl, remoteCode));
                }
            } catch (Exception ignored) {
                // 网络异常/无新版本: 静默忽略
            }
        }).start();
    }

    /** 点「以后再说」: 记住该版本号, 后续启动不再弹窗(仅当出现更高版本时才再次提示) */
    private void rememberIgnoredVersion(int versionCode) {
        getSharedPreferences("update_prefs", MODE_PRIVATE).edit()
                .putInt("ignored_version", versionCode).apply();
    }

    private void showUpdateDialog(String versionName, String note, String apkUrl, int remoteCode) {
        String msg = (note == null || note.isEmpty())
                ? "发现新版本，是否立即更新？"
                : note + "\n\n是否立即更新？";
        new AlertDialog.Builder(this)
                .setTitle("发现新版本 v" + versionName)
                .setMessage(msg)
                .setPositiveButton("立即更新", (d, w) -> downloadAndInstall(apkUrl))
                .setNegativeButton("以后再说", (d, w) -> rememberIgnoredVersion(remoteCode))
                .show();
    }

    private void downloadAndInstall(String apkUrl) {
        try {
            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            File dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
            if (dir == null) return;
            if (!dir.exists()) dir.mkdirs();
            final File apk = new File(dir, "rps-board-" + System.currentTimeMillis() + ".apk");

            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(apkUrl));
            req.setDestinationUri(Uri.fromFile(apk));
            req.setMimeType("application/vnd.android.package-archive");
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            final long downloadId = dm.enqueue(req);

            BroadcastReceiver receiver = new BroadcastReceiver() {
                @Override
                public void onReceive(Context c, Intent i) {
                    if (i.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) == downloadId) {
                        installApk(apk);
                        try {
                            unregisterReceiver(this);
                        } catch (Exception ignored) {
                        }
                    }
                }
            };
            registerReceiver(receiver, new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE));
        } catch (Exception ignored) {
        }
    }

    private void installApk(File apk) {
        try {
            Uri fileUri = FileProvider.getUriForFile(this, getPackageName() + ".fileprovider", apk);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(fileUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception ignored) {
        }
    }

    private static String readAll(InputStream in) throws Exception {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) != -1) bos.write(buf, 0, n);
        return new String(bos.toByteArray(), StandardCharsets.UTF_8);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        webView.destroy();
        super.onDestroy();
    }
}
