package com.rps.board;

import android.annotation.SuppressLint;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

/**
 * RPS 板块看板 - WebView 套壳
 * 主数据源: 阿里云服务器(http, 国内快); 互备数据源: GitHub Pages(https, 更稳定)
 * 主源加载失败/超时 → 自动切换备用源
 */
public class MainActivity extends AppCompatActivity {

    private static final String PRIMARY_URL = "http://47.99.178.225:8000/";
    private static final String FALLBACK_URL = "https://huse11.github.io/rps-board/";
    private static final long LOAD_TIMEOUT_MS = 25_000; // 主源 25 秒无响应视为不可用

    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private boolean loadedOk = false;       // 当前源已成功加载
    private boolean triedFallback = false;  // 是否已切换到备用源
    private final Runnable timeoutTask = new Runnable() {
        @Override
        public void run() {
            if (!loadedOk) {
                tryFallback();
            }
        }
    };

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
                loadedOk = true;
                handler.removeCallbacks(timeoutTask);
                swipeRefresh.setRefreshing(false);
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                if (!triedFallback) {
                    handler.removeCallbacks(timeoutTask);
                    handler.postDelayed(timeoutTask, LOAD_TIMEOUT_MS);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request != null && request.isForMainFrame() && !loadedOk) {
                    tryFallback();
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
                if (request != null && request.isForMainFrame() && !loadedOk
                        && errorResponse != null && errorResponse.getStatusCode() >= 500) {
                    tryFallback();
                }
            }
        });

        swipeRefresh.setOnRefreshListener(() -> {
            handler.removeCallbacks(timeoutTask);
            loadedOk = false;
            webView.reload();
            handler.postDelayed(timeoutTask, LOAD_TIMEOUT_MS);
        });

        loadPrimary();
    }

    private void loadPrimary() {
        loadedOk = false;
        triedFallback = false;
        webView.loadUrl(PRIMARY_URL);
    }

    /** 主源不可用 → 切换备用源(仅一次) */
    private void tryFallback() {
        if (triedFallback) return;
        triedFallback = true;
        handler.removeCallbacks(timeoutTask);
        Toast.makeText(this, "主服务器不可用，已切换备用数据源", Toast.LENGTH_SHORT).show();
        webView.loadUrl(FALLBACK_URL);
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
        handler.removeCallbacksAndMessages(null);
        webView.destroy();
        super.onDestroy();
    }
}
