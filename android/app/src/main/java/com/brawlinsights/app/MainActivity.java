package com.brawlinsights.app;

import android.annotation.SuppressLint;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.res.Configuration;
import android.content.res.Resources;
import android.graphics.Bitmap;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.LocaleList;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebBackForwardList;
import android.webkit.WebHistoryItem;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.activity.OnBackPressedCallback;
import androidx.core.content.FileProvider;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;
import com.google.android.gms.ads.MobileAds;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.util.Locale;

public class MainActivity extends BridgeActivity {

    private static final String TAG = "BrawlInsights";
    private static final String OFFLINE_ASSET_URL = "file:///android_asset/public/offline.html";
    private static final long CONNECTIVITY_POLL_MS = 500;
    private static final long IMAGE_SHARE_MAX_BYTES = 20L * 1024 * 1024;
    // iOS の iPad 判定に相当。sw600dp 以上をタブレットとして縦横両対応にする。
    private static final int TABLET_SMALLEST_WIDTH_DP = 600;
    // 切断直後の偽オンラインや、失敗した loadUrl の打ち消しを無視する
    private static final long ONLINE_RECOVERY_DEBOUNCE_MS = 3000;
    // バックグラウンド復帰時の VALIDATED 欠落などを、実切断と誤認しない
    private static final long OFFLINE_CONFIRM_MS = 1000;

    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback networkCallback;
    private boolean isNetworkAvailable = true;
    // true の間はリモート URL を開かず、オフライン画面を維持する
    private boolean wantOfflinePage = false;
    private boolean hasSuccessfullyLoadedApp = false;
    private boolean offlineConfirmScheduled = false;
    private boolean isResumed = false;
    // onLost 後もしばらく getActiveNetwork() が同じ Network を返すため、その間は切断扱いにする
    private volatile Network ignoredLostNetwork;
    private long suppressOnlineReloadUntil;
    private long lastOfflineLoadAt;
    private OnBackPressedCallback backPressedCallback;
    private final Handler connectivityHandler = new Handler(Looper.getMainLooper());
    private final Runnable connectivityCheck = new Runnable() {
        @Override
        public void run() {
            boolean hasLink = hasInternetCapability();
            boolean validated = isValidatedConnected();
            if (isNetworkAvailable && !hasLink) {
                applyNetworkState(false, false);
            } else if (hasLink) {
                cancelScheduledOffline();
                if (!isNetworkAvailable && validated) {
                    // デバウンス後の復帰だけ拾う。切断直後の偽オンラインは applyNetworkState 側で無視する。
                    applyNetworkState(true, false);
                }
            }
            connectivityHandler.postDelayed(this, CONNECTIVITY_POLL_MS);
        }
    };
    private final Runnable confirmOffline = this::commitOffline;

    @Override
    protected void attachBaseContext(Context newBase) {
        // WebView の Accept-Language は LocaleList.getDefault() に従う。
        // 端末の先頭言語だけを渡し、2番目以降（例: 英語UI + 日本語）で誤判定しないようにする。
        super.attachBaseContext(applySystemLocale(newBase));
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        applyScreenOrientation();
        applySystemBarAppearance(true);
        isNetworkAvailable = hasInternetCapability();
        disableWebViewOverscroll();
        installJavascriptInterface();
        registerWebViewForAds();
        installWebViewClient();
        installSystemBackHandler();
        startNetworkMonitoring();
        if (!isNetworkAvailable) {
            showOfflinePage(true);
        }
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        applyScreenOrientation();
    }

    private void applyScreenOrientation() {
        boolean isTablet = getResources().getConfiguration().smallestScreenWidthDp >= TABLET_SMALLEST_WIDTH_DP;
        if (isTablet) {
            setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_FULL_USER);
        } else {
            setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
        }
    }

    private void applySystemBarAppearance(boolean lightBackground) {
        Window window = getWindow();
        if (window == null) {
            return;
        }
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(window, window.getDecorView());
        controller.setAppearanceLightStatusBars(lightBackground);
        controller.setAppearanceLightNavigationBars(lightBackground);
    }

    @Override
    public void onResume() {
        super.onResume();
        isResumed = true;
        // バックグラウンド中の onLost で残った無視フラグは、復帰時に再判定する
        ignoredLostNetwork = null;
        boolean hasLink = hasInternetCapability();
        boolean validated = isValidatedConnected();
        if (!hasLink) {
            applyNetworkState(false, false);
        } else {
            cancelScheduledOffline();
            if (validated) {
                applyNetworkState(true, false);
            }
        }
        connectivityHandler.removeCallbacks(connectivityCheck);
        connectivityHandler.post(connectivityCheck);
    }

    @Override
    public void onPause() {
        isResumed = false;
        cancelScheduledOffline();
        connectivityHandler.removeCallbacks(connectivityCheck);
        super.onPause();
    }

    @Override
    public void onDestroy() {
        connectivityHandler.removeCallbacks(connectivityCheck);
        cancelScheduledOffline();
        stopNetworkMonitoring();
        super.onDestroy();
    }

    private static Context applySystemLocale(Context base) {
        Locale systemLocale;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            systemLocale = Resources.getSystem().getConfiguration().getLocales().get(0);
        } else {
            systemLocale = Resources.getSystem().getConfiguration().locale;
        }

        String language = systemLocale != null ? systemLocale.getLanguage() : "";
        Locale appLocale = "ja".equals(language) ? Locale.JAPANESE : Locale.ENGLISH;
        Locale.setDefault(appLocale);

        Configuration config = new Configuration(base.getResources().getConfiguration());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            // Locale.setDefault だけでは LocaleList.getDefault() が更新されない。
            // WebView の Accept-Language は LocaleList を見るため、先頭言語だけにする。
            LocaleList localeList = new LocaleList(appLocale);
            LocaleList.setDefault(localeList);
            config.setLocales(localeList);
        } else {
            config.setLocale(appLocale);
        }
        return base.createConfigurationContext(config);
    }

    private void disableWebViewOverscroll() {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) {
            return;
        }
        // Pixel など Android 12 以降は端でコンテンツ全体が伸びる。
        // ネイティブの AdMob バナーは動かないため、タブバーとの間に隙間ができる。
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
        if (webView.getParent() instanceof View parent) {
            parent.setOverScrollMode(View.OVER_SCROLL_NEVER);
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void installJavascriptInterface() {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) {
            return;
        }
        webView.addJavascriptInterface(new OfflineBridge(), "BrawlInsightsNative");
    }

    private void registerWebViewForAds() {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) {
            return;
        }
        try {
            // AdSense/AdMob が WebView 内でアプリシグナルを使えるようにする（Google ポリシー）
            MobileAds.registerWebView(webView);
            Log.i(TAG, "WebView registered with MobileAds for WebView API for Ads.");
        } catch (RuntimeException e) {
            Log.e(TAG, "Failed to register WebView with MobileAds", e);
        }
    }

    private void installWebViewClient() {
        if (getBridge() == null || getBridge().getWebView() == null) {
            return;
        }
        getBridge().setWebViewClient(new BridgeWebViewClient(getBridge()) {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                // file:// や localhost はアプリ内で開く。
                // super を呼ぶと Capacitor が server.url とホストが違うと判断し外部ブラウザを起動する。
                if (isOfflineOrLocalUrl(uri)) {
                    return false;
                }
                if (wantOfflinePage && request.isForMainFrame()) {
                    showOfflinePage(true);
                    return true;
                }
                return super.shouldOverrideUrlLoading(view, request);
            }

            @Deprecated
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri uri = url != null ? Uri.parse(url) : null;
                if (isOfflineOrLocalUrl(uri)) {
                    return false;
                }
                if (wantOfflinePage) {
                    showOfflinePage(true);
                    return true;
                }
                return super.shouldOverrideUrlLoading(view, url);
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                if (wantOfflinePage && url != null && !isOfflinePageUrl(url)) {
                    Log.i(TAG, "Blocked remote page start while offline: " + url);
                    showOfflinePage(true);
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (wantOfflinePage && url != null && !isOfflinePageUrl(url)) {
                    // 標準エラー画面がオフライン HTML を上書きした場合のやり直し
                    Log.i(TAG, "Non-offline page finished while wanting offline. Reloading offline page.");
                    showOfflinePage(true);
                    return;
                }
                if (!wantOfflinePage && url != null && (url.startsWith("http://") || url.startsWith("https://"))) {
                    hasSuccessfullyLoadedApp = true;
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request != null && request.isForMainFrame()
                    && handleMainFrameLoadFailure(request.getUrl(), error, null, 0)) {
                    return;
                }
                super.onReceivedError(view, request, error);
            }

            @Deprecated
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                Uri uri = failingUrl != null ? Uri.parse(failingUrl) : null;
                if (handleMainFrameLoadFailure(uri, null, description, errorCode)) {
                    return;
                }
                super.onReceivedError(view, errorCode, description, failingUrl);
            }
        });
    }

    private void installSystemBackHandler() {
        // Android 15 以降は予測型「戻る」がデフォルトで Activity 終了になる。
        // 縁スワイプは残し、WebView の履歴があればページ戻りにする。
        backPressedCallback = new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                handleSystemBack();
            }
        };
        getOnBackPressedDispatcher().addCallback(this, backPressedCallback);
    }

    private void handleSystemBack() {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null || wantOfflinePage) {
            leaveAppViaSystemBack();
            return;
        }
        if (goBackInAppIfPossible(webView)) {
            return;
        }
        webView.evaluateJavascript(
            "(function(){"
                + "if(!window.BrawlInsightsSmartBack)return 'exit';"
                + "var fallback=window.BrawlInsightsSmartBack.resolveFallbackUrl(null);"
                + "if(!fallback)return 'exit';"
                + "var current=(location.pathname||'/').replace(/\\/+$/,'')||'/';"
                + "var fallbackPath;"
                + "try{fallbackPath=new URL(fallback,location.origin).pathname.replace(/\\/+$/,'')||'/';}"
                + "catch(e){return 'exit';}"
                + "if(fallbackPath!==current){window.location.href=fallback;return 'navigated';}"
                + "return 'exit';"
                + "})()",
            value -> {
                if (value != null && value.contains("navigated")) {
                    return;
                }
                leaveAppViaSystemBack();
            }
        );
    }

    private boolean goBackInAppIfPossible(WebView webView) {
        if (!webView.canGoBack()) {
            return false;
        }
        WebBackForwardList list = webView.copyBackForwardList();
        int currentIndex = list.getCurrentIndex();
        int steps = 0;
        for (int i = currentIndex - 1; i >= 0; i--) {
            steps++;
            WebHistoryItem item = list.getItemAtIndex(i);
            if (item == null) {
                continue;
            }
            String url = item.getUrl();
            if (url == null) {
                continue;
            }
            Uri uri = Uri.parse(url);
            if (isOfflineOrLocalUrl(uri)) {
                continue;
            }
            if (url.startsWith("http://") || url.startsWith("https://")) {
                webView.goBackOrForward(-steps);
                return true;
            }
        }
        return false;
    }

    private void leaveAppViaSystemBack() {
        if (backPressedCallback != null) {
            backPressedCallback.setEnabled(false);
        }
        getOnBackPressedDispatcher().onBackPressed();
        if (backPressedCallback != null) {
            backPressedCallback.setEnabled(true);
        }
    }

    private void startNetworkMonitoring() {
        connectivityManager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (connectivityManager == null) {
            return;
        }

        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                boolean sameLostNetwork = network.equals(ignoredLostNetwork);
                if (sameLostNetwork && SystemClock.elapsedRealtime() < suppressOnlineReloadUntil) {
                    return;
                }
                ignoredLostNetwork = null;
                runOnUiThread(() -> handleLinkOrValidationChange(!sameLostNetwork));
            }

            @Override
            public void onLosing(Network network, int maxMsToLive) {
                ignoredLostNetwork = network;
                runOnUiThread(() -> {
                    Network active = connectivityManager != null ? connectivityManager.getActiveNetwork() : null;
                    if (active == null || active.equals(network)) {
                        applyNetworkState(false, false);
                    }
                });
            }

            @Override
            public void onLost(Network network) {
                ignoredLostNetwork = network;
                runOnUiThread(() -> {
                    Network active = connectivityManager != null ? connectivityManager.getActiveNetwork() : null;
                    if (active == null || active.equals(network)) {
                        applyNetworkState(false, false);
                    } else {
                        handleLinkOrValidationChange(true);
                    }
                });
            }

            @Override
            public void onUnavailable() {
                runOnUiThread(() -> applyNetworkState(false, false));
            }

            @Override
            public void onCapabilitiesChanged(Network network, NetworkCapabilities networkCapabilities) {
                if (network.equals(ignoredLostNetwork)) {
                    return;
                }
                runOnUiThread(() -> handleLinkOrValidationChange(false));
            }
        };

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                connectivityManager.registerDefaultNetworkCallback(networkCallback);
            } else {
                NetworkRequest request = new NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .build();
                connectivityManager.registerNetworkCallback(request, networkCallback);
            }
        } catch (RuntimeException e) {
            Log.e(TAG, "Failed to register network callback", e);
        }
    }

    private void stopNetworkMonitoring() {
        if (connectivityManager == null || networkCallback == null) {
            return;
        }
        try {
            connectivityManager.unregisterNetworkCallback(networkCallback);
        } catch (RuntimeException ignored) {
            // 未登録や二重解除は無視する
        }
        networkCallback = null;
    }

    private void handleLinkOrValidationChange(boolean allowImmediateOnline) {
        if (hasInternetCapability()) {
            cancelScheduledOffline();
            if (isValidatedConnected()) {
                applyNetworkState(true, allowImmediateOnline);
            }
            return;
        }
        applyNetworkState(false, false);
    }

    private void applyNetworkState(boolean available, boolean allowImmediateOnline) {
        if (!available) {
            if (!isResumed) {
                return;
            }
            scheduleOffline();
            return;
        }

        cancelScheduledOffline();
        if (!allowImmediateOnline && SystemClock.elapsedRealtime() < suppressOnlineReloadUntil) {
            Log.i(TAG, "Ignoring online flicker after disconnect.");
            return;
        }
        if (available == isNetworkAvailable && hasSuccessfullyLoadedApp && !wantOfflinePage) {
            return;
        }
        isNetworkAvailable = true;
        Log.i(TAG, "Network came back online.");
        loadInitialPage();
    }

    private void scheduleOffline() {
        if (!isNetworkAvailable && wantOfflinePage) {
            return;
        }
        if (offlineConfirmScheduled) {
            return;
        }
        offlineConfirmScheduled = true;
        Log.i(TAG, "Network looks offline. Confirming in " + OFFLINE_CONFIRM_MS + "ms.");
        connectivityHandler.postDelayed(confirmOffline, OFFLINE_CONFIRM_MS);
    }

    private void cancelScheduledOffline() {
        if (!offlineConfirmScheduled) {
            return;
        }
        offlineConfirmScheduled = false;
        connectivityHandler.removeCallbacks(confirmOffline);
        Log.i(TAG, "Cancelled pending offline screen.");
    }

    private void commitOffline() {
        offlineConfirmScheduled = false;
        if (!isResumed) {
            return;
        }
        if (hasInternetCapability()) {
            Log.i(TAG, "Offline unconfirmed; link is back.");
            return;
        }
        if (!isNetworkAvailable && wantOfflinePage) {
            return;
        }
        isNetworkAvailable = false;
        hasSuccessfullyLoadedApp = false;
        suppressOnlineReloadUntil = SystemClock.elapsedRealtime() + ONLINE_RECOVERY_DEBOUNCE_MS;
        Log.i(TAG, "Network became offline.");
        showOfflinePage(true);
    }

    private boolean isCurrentlyConnected() {
        return isValidatedConnected();
    }

    private boolean isValidatedConnected() {
        return hasInternetCapability(true);
    }

    private boolean hasInternetCapability() {
        return hasInternetCapability(false);
    }

    private boolean hasInternetCapability(boolean requireValidated) {
        ConnectivityManager manager = connectivityManager;
        if (manager == null) {
            manager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        }
        if (manager == null) {
            return true;
        }
        Network network = manager.getActiveNetwork();
        if (network == null) {
            return false;
        }
        if (network.equals(ignoredLostNetwork)) {
            return false;
        }
        NetworkCapabilities capabilities = manager.getNetworkCapabilities(network);
        if (capabilities == null) {
            return false;
        }
        if (!capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) {
            return false;
        }
        if (requireValidated && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
        }
        return true;
    }

    private void loadInitialPage() {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) {
            return;
        }
        if (!isCurrentlyConnected()) {
            applyNetworkState(false, false);
            return;
        }
        String url = getBridge().getServerUrl();
        if (url == null || url.isEmpty()) {
            url = getBridge().getAppUrl();
        }
        if (url == null || url.isEmpty()) {
            Log.e(TAG, "Server URL not found. Cannot load initial page.");
            return;
        }
        wantOfflinePage = false;
        hasSuccessfullyLoadedApp = false;
        Log.i(TAG, "Loading initial server URL: " + url);
        webView.loadUrl(url);
    }

    private void showOfflinePage(boolean force) {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) {
            return;
        }
        wantOfflinePage = true;
        String current = webView.getUrl();
        if (!force && current != null && isOfflinePageUrl(current)) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        if (force && isOfflinePageUrl(current) && now - lastOfflineLoadAt < 400) {
            return;
        }
        lastOfflineLoadAt = now;
        Log.i(TAG, "Showing local offline page.");
        webView.loadUrl(OFFLINE_ASSET_URL);
    }

    private boolean handleMainFrameLoadFailure(Uri uri, WebResourceError error, String description, int errorCode) {
        if (isOfflineOrLocalUrl(uri)) {
            return false;
        }
        boolean abort = isAbortError(error, description);
        boolean networkFailure = isNetworkError(error)
            || isNetworkErrorCode(errorCode)
            || isNetworkErrorDescription(description);
        if (wantOfflinePage && (abort || networkFailure)) {
            showOfflinePage(true);
            return true;
        }
        if (!abort && !networkFailure) {
            return false;
        }
        // 復帰時の abort / NETWORK_CHANGED は、回線がありページ表示済みなら無視する
        if (hasSuccessfullyLoadedApp && hasInternetCapability()) {
            Log.i(TAG, "Ignoring transient main-frame error while network is present.");
            return true;
        }
        if (abort && !networkFailure && hasInternetCapability()) {
            return true;
        }
        Log.i(TAG, "Main frame load failed. Scheduling offline page.");
        applyNetworkState(false, false);
        return true;
    }

    private boolean isOfflinePageUrl(String url) {
        return url != null && url.contains("offline.html");
    }

    private boolean isOfflineOrLocalUrl(Uri uri) {
        if (uri == null) {
            return false;
        }
        String scheme = uri.getScheme();
        if ("file".equals(scheme) || "data".equals(scheme) || "about".equals(scheme) || "blob".equals(scheme)) {
            return true;
        }
        if ("capacitor".equals(scheme) || isLocalhost(uri)) {
            return true;
        }
        return isOfflinePageUrl(uri.toString());
    }

    private boolean isLocalhost(Uri uri) {
        String host = uri.getHost();
        return "localhost".equals(host) || "127.0.0.1".equals(host);
    }

    private boolean isAbortError(WebResourceError error, String description) {
        String text = description;
        if (text == null && error != null && error.getDescription() != null) {
            text = error.getDescription().toString();
        }
        return text != null && (text.contains("ERR_CONNECTION_ABORTED") || text.contains("ERR_ABORTED"));
    }

    private boolean isNetworkError(WebResourceError error) {
        if (error == null) {
            return false;
        }
        if (isNetworkErrorCode(error.getErrorCode())) {
            return true;
        }
        CharSequence description = error.getDescription();
        if (description != null && isNetworkErrorDescription(description.toString())) {
            return true;
        }
        return error.getErrorCode() == WebViewClient.ERROR_UNKNOWN && !isCurrentlyConnected();
    }

    private boolean isNetworkErrorDescription(String text) {
        if (text == null) {
            return false;
        }
        return text.contains("ERR_INTERNET_DISCONNECTED")
            || text.contains("ERR_NAME_NOT_RESOLVED")
            || text.contains("ERR_ADDRESS_UNREACHABLE")
            || text.contains("ERR_CONNECTION_REFUSED")
            || text.contains("ERR_CONNECTION_ABORTED")
            || text.contains("ERR_ABORTED")
            || text.contains("ERR_CONNECTION_TIMED_OUT")
            || text.contains("ERR_TIMED_OUT")
            || text.contains("ERR_NETWORK_CHANGED");
    }

    private boolean isNetworkErrorCode(int errorCode) {
        return errorCode == WebViewClient.ERROR_HOST_LOOKUP
            || errorCode == WebViewClient.ERROR_CONNECT
            || errorCode == WebViewClient.ERROR_TIMEOUT
            || errorCode == WebViewClient.ERROR_IO
            || errorCode == WebViewClient.ERROR_PROXY_AUTHENTICATION;
    }

    public class OfflineBridge {
        @JavascriptInterface
        public void retry() {
            runOnUiThread(() -> {
                suppressOnlineReloadUntil = 0;
                ignoredLostNetwork = null;
                if (isCurrentlyConnected()) {
                    isNetworkAvailable = true;
                    loadInitialPage();
                } else {
                    isNetworkAvailable = false;
                    showOfflinePage(true);
                }
            });
        }

        @JavascriptInterface
        public void openPlayStore() {
            runOnUiThread(MainActivity.this::openPlayStoreListing);
        }

        @JavascriptInterface
        public void setLightSystemBars(boolean lightBackground) {
            runOnUiThread(() -> applySystemBarAppearance(lightBackground));
        }

        @JavascriptInterface
        public void shareImageFromUrl(String imageUrl, String filename, String dialogTitle) {
            new Thread(() -> shareDownloadedImage(imageUrl, filename, dialogTitle), "share-image").start();
        }
    }

    private void openPlayStoreListing() {
        String packageName = getPackageName();
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=" + packageName));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (ActivityNotFoundException e) {
            try {
                Intent intent = new Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse("https://play.google.com/store/apps/details?id=" + packageName)
                );
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (RuntimeException ex) {
                Log.e(TAG, "Failed to open Play Store", ex);
            }
        }
    }

    private void shareDownloadedImage(String imageUrl, String filename, String dialogTitle) {
        try {
            File file = downloadImageToCache(imageUrl, filename);
            runOnUiThread(() -> {
                try {
                    startImageShareSheet(file, filename, dialogTitle);
                    notifyJsEvent("bi-native-image-share-ok");
                } catch (RuntimeException e) {
                    Log.e(TAG, "Failed to start image share sheet", e);
                    notifyJsEvent("bi-native-image-share-fail");
                }
            });
        } catch (Exception e) {
            Log.e(TAG, "Failed to download image for sharing: " + imageUrl, e);
            notifyJsEvent("bi-native-image-share-fail");
        }
    }

    private File downloadImageToCache(String imageUrl, String filename) throws IOException {
        URI uri;
        try {
            uri = URI.create(imageUrl);
        } catch (IllegalArgumentException e) {
            throw new IOException("Invalid image URL", e);
        }
        String scheme = uri.getScheme();
        if (!"https".equals(scheme) && !"http".equals(scheme)) {
            throw new IOException("Unsupported image URL scheme: " + scheme);
        }

        String safeName = sanitizeShareFilename(filename);
        URL url = uri.toURL();
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(30000);
        connection.setInstanceFollowRedirects(true);
        connection.setRequestProperty("Accept", "image/*");
        try {
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) {
                throw new IOException("HTTP " + code);
            }
            File outFile = new File(getCacheDir(), safeName);
            try (InputStream in = connection.getInputStream();
                 FileOutputStream out = new FileOutputStream(outFile)) {
                byte[] buffer = new byte[8192];
                long total = 0;
                int read;
                while ((read = in.read(buffer)) != -1) {
                    total += read;
                    if (total > IMAGE_SHARE_MAX_BYTES) {
                        throw new IOException("Image too large to share");
                    }
                    out.write(buffer, 0, read);
                }
            }
            return outFile;
        } finally {
            connection.disconnect();
        }
    }

    private void startImageShareSheet(File file, String filename, String dialogTitle) {
        Uri uri = FileProvider.getUriForFile(this, getPackageName() + ".fileprovider", file);
        String title = (dialogTitle == null || dialogTitle.isEmpty()) ? "Save Image" : dialogTitle;
        Intent shareIntent = new Intent(Intent.ACTION_SEND);
        shareIntent.setType(imageMimeType(filename));
        shareIntent.putExtra(Intent.EXTRA_STREAM, uri);
        shareIntent.setClipData(ClipData.newRawUri(title, uri));
        shareIntent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);

        Intent chooser = Intent.createChooser(shareIntent, title);
        chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        startActivity(chooser);
    }

    private void notifyJsEvent(String eventName) {
        runOnUiThread(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) {
                return;
            }
            webView.evaluateJavascript(
                "window.dispatchEvent(new CustomEvent('" + eventName + "'))",
                null
            );
        });
    }

    private static String sanitizeShareFilename(String filename) {
        String name = filename == null ? "" : filename.trim();
        int slash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        if (slash >= 0) {
            name = name.substring(slash + 1);
        }
        name = name.replaceAll("[^a-zA-Z0-9._-]", "_");
        if (name.isEmpty() || name.startsWith(".")) {
            return "profile-image.png";
        }
        if (name.length() > 80) {
            name = name.substring(name.length() - 80);
        }
        return name;
    }

    private static String imageMimeType(String filename) {
        String lower = filename == null ? "" : filename.toLowerCase(Locale.US);
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
            return "image/jpeg";
        }
        if (lower.endsWith(".webp")) {
            return "image/webp";
        }
        return "image/png";
    }
}
