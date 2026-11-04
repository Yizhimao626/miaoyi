package com.miaoyi.app;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;

/**
 * 喵艺 · Android 入口
 *
 * 【混合内容修复】Capacitor 默认 androidScheme 为 https（页面 https://localhost，
 * 为兼容 navigator.mediaDevices 麦克风），但后端是 http://局域网IP。
 * HTTPS 页面请求 HTTP 资源属于 mixed content，Android WebView 默认阻止
 * （MIXED_CONTENT_NEVER_ALLOW），会导致 fetch / WebSocket / 音频 / 图片
 * 全部失败——"手机端一堆错误"的主要根源之一。
 *
 * 此处显式开启 MIXED_CONTENT_ALWAYS_ALLOW 兜底（无论 Capacitor 内部是否已处理），
 * 确保 App 能访问任意配置的 http:// 后端地址。
 */
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        try {
            WebView webView = getBridge().getWebView();
            if (webView != null) {
                // 【App 更新】Capacitor 默认无 DownloadListener，WebView 内触发的
                // APK 下载会无反应。这里把下载交给系统浏览器处理（系统浏览器支持
                // 下载/安装），无需引入额外插件。
                webView.setDownloadListener((url, userAgent, contentDisposition,
                        mimeType, contentLength) -> {
                    try {
                        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(intent);
                    } catch (Exception e) {
                        // 无可用浏览器时静默
                    }
                });
            }
        } catch (Exception e) {
            // 桥接尚未就绪时静默（不影响 Capacitor 默认行为）
        }
    }

    @Override
    public void onResume() {
        super.onResume();
        try {
            WebView webView = getBridge().getWebView();
            if (webView != null) {
                WebSettings settings = webView.getSettings();
                // 允许 HTTPS 页面加载 HTTP 资源（后端为 http://IP:8001）
                settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
                // 确保 localStorage / DOM 存储可用（WebView 默认开启，显式兜底）
                settings.setDomStorageEnabled(true);
            }
        } catch (Exception e) {
            // 兜底失败不应崩溃，Capacitor 自身配置也可能已处理
        }
    }
}
