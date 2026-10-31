package com.videototext.app;

import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import androidx.core.content.FileProvider;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;

/**
 * 在 App 内下载 APK 并拉起系统安装器。
 *
 * ## 为什么不能真的「自动安装」
 *
 * Android 从设计上就不允许普通应用静默安装自己：唯一的静默路径是设备管理员
 * （Device Owner / 企业 MDM 场景），个人应用拿不到这个身份。所以能做到的极限是
 * **把「去浏览器下 → 找文件 → 点开」这三步收进 App**，最后一步「是否安装」必须
 * 由系统安装器自己弹窗问用户。
 *
 * 这不只是权限问题：允许应用替用户按下「安装」，就等于允许它偷偷换成任何东西。
 *
 * ## 为什么下载在 JS 侧、只有安装在这里
 *
 * 下载走 `@capacitor/filesystem` 的 `downloadFile`（原生 HTTP，不受 WebView 的
 * CORS 限制 —— GitHub Release 会 302 到 objects.githubusercontent.com，那个域
 * 没有 CORS 头，浏览器里 fetch 会失败）。它把文件写进
 * {@code getCacheDir()/updates/}，然后只把路径交给本插件。
 *
 * 本插件不碰网络、不碰版本判断，职责单一到只有一个「把这个缓存文件交给安装器」。
 * 版本比较、要不要装，全在 JS 侧（`src/lib/mobile-update.ts`），那里能单测。
 *
 * ## FileProvider 而不是 file:// URI
 *
 * `targetSdkVersion 36` 上，`file://` URI 跨进程传递会直接触发
 * `FileUriExposedException`（Nougat 起收紧）。必须用 content:// + 临时读权限，
 * 而 content:// 的提供者就是 `AndroidManifest.xml` 里已经声明好的
 * `${applicationId}.fileprovider`（`res/xml/file_paths.xml` 的 `cache-path` 已覆盖
 * cache 根目录，`updates/` 子目录自然在内）。
 */
@CapacitorPlugin(name = "ApkInstaller")
public class ApkInstallerPlugin extends Plugin {

    /** 与 JS 侧 `APK_CACHE_PATH` 必须一致 */
    private static final String DEFAULT_PATH = "updates/video-to-text-update.apk";

    /**
     * 询问当前能否安装（Android 8+ 需要用户在系统设置里给本应用开
     * 「安装未知应用」）。JS 侧据此决定按钮该显示「下载并安装」还是「去授权」。
     */
    @PluginMethod
    public void canInstall(PluginCall call) {
        JSObject ret = new JSObject();
        boolean needs = needsUnknownSourcesPermission();
        ret.put("allowed", !needs);
        ret.put("needsPermission", needs);
        call.resolve(ret);
    }

    /**
     * 跳到系统设置页让用户给本应用开「安装未知应用」。
     *
     * 用 `ACTION_MANAGE_UNKNOWN_APP_SOURCES` 带 package URI：直接落在本应用的
     * 开关上，而不是让用户在一长串应用列表里自己找。低版本（< 8.0）没有这个
     * 开关，直接返回（那些版本安装时不需要授权）。
     */
    @PluginMethod
    public void requestPermission(PluginCall call) {
        if (!needsUnknownSourcesPermission()) {
            call.resolve();
            return;
        }
        try {
            Intent intent = new Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + getContext().getPackageName())
            );
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception error) {
            // 少数定制 ROM 没有这个 Activity，退回上一级列表页
            try {
                Intent fallback = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES);
                fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                getContext().startActivity(fallback);
                call.resolve();
            } catch (Exception second) {
                call.reject("打不开系统设置页，请手动在系统设置里允许本应用安装应用", "NO_SETTINGS_ACTIVITY");
            }
        }
    }

    /**
     * 拉起系统安装器。用户点「安装」后才真正替换旧版本。
     *
     * `path` 缺省用 `DEFAULT_PATH`；文件必须已由 JS 侧下载好。
     */
    @PluginMethod
    public void install(PluginCall call) {
        String path = call.getString("path", DEFAULT_PATH);
        File file = new File(getContext().getCacheDir(), path);

        if (!file.exists()) {
            // 下载失败或缓存被系统清理了。让用户重下一遍，而不是弹一个看不懂的异常。
            call.reject("安装包还没下载好，请重新下载", "APK_NOT_DOWNLOADED");
            return;
        }
        if (needsUnknownSourcesPermission()) {
            // 这里不自己跳设置，交由 JS 侧引导（用户可能只是想看看进度）。
            call.reject("请先在系统设置里允许本应用安装应用", "NEEDS_INSTALL_PERMISSION");
            return;
        }

        try {
            Uri uri = FileProvider.getUriForFile(
                getContext(),
                getContext().getPackageName() + ".fileprovider",
                file
            );
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(uri, "application/vnd.android.package-archive");
            // 安装器是另一个进程，必须显式给读权限，否则它打不开这个 content://
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            // 从 Activity 之外的 context 启动必须带 NEW_TASK
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception error) {
            call.reject("拉起安装器失败：" + error.getMessage(), "INSTALL_FAILED");
        }
    }

    /** 只有 Android 8.0（API 26）起才有「安装未知应用」这个逐应用开关 */
    private boolean needsUnknownSourcesPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return false;
        return !getContext().getPackageManager().canRequestPackageInstalls();
    }
}
