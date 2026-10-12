package com.classwizstudio.calculator;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.view.View;
import android.graphics.Color;
import android.os.Build;
import android.view.Window;

public class MainActivity extends Activity {
  private WebView webView;
  @Override public void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    Window w = getWindow();
    w.setStatusBarColor(Color.rgb(10,16,24));
    w.setNavigationBarColor(Color.rgb(7,11,18));
    if (Build.VERSION.SDK_INT >= 23) {
      w.getDecorView().setSystemUiVisibility(0);
    }
    webView = new WebView(this);
    webView.setBackgroundColor(Color.rgb(7,11,18));
    webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
    webView.getSettings().setJavaScriptEnabled(true);
    webView.getSettings().setDomStorageEnabled(true);
    webView.getSettings().setAllowFileAccess(true);
    webView.getSettings().setAllowContentAccess(false);
    webView.getSettings().setSupportZoom(false);
    webView.getSettings().setBuiltInZoomControls(false);
    webView.setWebViewClient(new WebViewClient());
    webView.setWebChromeClient(new WebChromeClient());
    setContentView(webView);
    webView.loadUrl("file:///android_asset/index.html");
  }
  @Override public void onBackPressed() {
    if (webView != null && webView.canGoBack()) webView.goBack(); else super.onBackPressed();
  }
}
