import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

import {
  APK_CACHE_PATH,
  APK_MIME,
  checkInstallPermission,
  installApk,
  requestInstallPermission,
  runInAppUpdate,
} from "../src/lib/apk-installer";

/**
 * PR15 的回归测试（应用内下载并安装 APK）。
 *
 * 编号从回归 35 起，接在 pr14 的 34f 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. **下载路径必须是原生 HTTP**（`Filesystem.downloadFile`），不是
 *    `fetch` + blob —— Release 资产 302 到 objects.githubusercontent.com，
 *    那个域没有 CORS 头，WebView 里 fetch 一定失败。
 * 2. **缓存路径与原生侧必须一致** —— 两边各写一个字符串，改一侧忘另一侧
 *    就会变成「下载成功但安装报『安装包还没下载好』」。
 * 3. **首次缺授权时不能自己循环重试** —— `requestPermission` 只是把人送到
 *    系统设置页；跳走之后必须停下来，等用户回来再点。
 * 4. **任何一步失败都要留浏览器出口** —— 原生插件不可用、下载失败、
 *    Release 里没有 APK 资产，都得能走通。
 * 5. 网页构建里不出现任何应用内安装的痕迹。
 *
 * 注意：这里用**静态 import**。Playwright 的 `await import("../src/lib/x")`
 * 不会套用 tsconfig 的 `@/*` 别名，模块内部的 `@/lib/runtime` 会
 * `Cannot find module`；静态 import 走的是另一条解析路径，没问题（pr12
 * 依赖 platform→registry 的别名链，也是靠静态 import 才通的）。
 */

const JAVA_PLUGIN = "android/app/src/main/java/com/videototext/app/ApkInstallerPlugin.java";
const JAVA_ACTIVITY = "android/app/src/main/java/com/videototext/app/MainActivity.java";
const MANIFEST = "android/app/src/main/AndroidManifest.xml";
const FILE_PATHS = "android/app/src/main/res/xml/file_paths.xml";

// 这些常量的值与原生侧 Java 里的字符串必须逐字一致，
// 用断言把这个契约钉在测试里（改一侧忘了另一侧会立刻红）。
test("回归 35：APK 缓存路径与原生插件里的常量一致", async () => {
  expect(APK_CACHE_PATH).toBe("updates/video-to-text-update.apk");
  expect(APK_MIME).toBe("application/vnd.android.package-archive");

  const java = await readFile(JAVA_PLUGIN, "utf8");
  expect(java).toContain(`DEFAULT_PATH = "${APK_CACHE_PATH}"`);
  // 插件名要和 JS 侧 registerPlugin("ApkInstaller") 对得上
  expect(java).toContain('@CapacitorPlugin(name = "ApkInstaller")');
});

test("回归 35b：Android 侧接线齐全（权限、插件注册、FileProvider）", async () => {
  const [manifest, activity, plugin] = await Promise.all([
    readFile(MANIFEST, "utf8"),
    readFile(JAVA_ACTIVITY, "utf8"),
    readFile(JAVA_PLUGIN, "utf8"),
  ]);

  // 没有这条权限，canRequestPackageInstalls() 恒为 false，
  // 带 apk 的 ACTION_VIEW 会被系统直接拒绝
  expect(manifest).toContain("android.permission.REQUEST_INSTALL_PACKAGES");
  // FileProvider 必须是已声明的那一个：content:// 才能跨进程传，
  // file:// 在 targetSdk 36 上会抛 FileUriExposedException
  expect(manifest).toContain("androidx.core.content.FileProvider");
  expect(manifest).toContain('android:authorities="${applicationId}.fileprovider"');

  expect(activity).toContain("registerPlugin(ApkInstallerPlugin.class)");

  // 安装 intent 的三件套，缺一个就打不开安装器
  expect(plugin).toContain("FileProvider.getUriForFile");
  expect(plugin).toContain("FLAG_GRANT_READ_URI_PERMISSION");
  expect(plugin).toContain("FLAG_ACTIVITY_NEW_TASK");
  expect(plugin).toContain(APK_MIME);
  // Android 8+ 的逐应用授权，且不能对老系统谎报「需要授权」
  expect(plugin).toContain("canRequestPackageInstalls");
  expect(plugin).toContain("ACTION_MANAGE_UNKNOWN_APP_SOURCES");
  expect(plugin).toContain("Build.VERSION_CODES.O");
});

test("回归 35c：file_paths.xml 必须覆盖 cache 目录", async () => {
  const paths = await readFile(FILE_PATHS, "utf8");
  // APK 下载进 getCacheDir()/updates/，FileProvider 的路径表里必须有 cache-path
  expect(paths).toContain("cache-path");
});

test("回归 35d：非移动壳环境下所有原生调用都安全降级", async () => {
  // 网页构建 / Node 里没有 Capacitor 插件，这些函数必须返回 null / false
  // 而不是抛异常，否则设置面板一渲染就会崩
  await expect(checkInstallPermission()).resolves.toBeNull();
  await expect(requestInstallPermission()).resolves.toBe(false);
  await expect(installApk()).resolves.toEqual({
    ok: false,
    message: "当前环境不支持应用内安装",
  });
  await expect(runInAppUpdate("https://example.com/x.apk")).resolves.toEqual({
    stage: "failed",
    message: "当前环境不支持应用内安装",
  });
});

test("回归 35e：主按钮是「下载并安装」，且真的调到应用内流程", async () => {
  const src = await readFile("src/components/layout/mobile-update-section.tsx", "utf8");

  // v0.1.7 那个「下载新版本」其实是 openExternal 跳浏览器，必须改掉
  expect(src).toContain("下载并安装");
  expect(src).not.toContain("下载新版本");
  expect(src).toContain("runInAppUpdate");
  expect(src).toContain("checkInstallPermission");
  // 失败时要有浏览器退路，否则用户卡在死按钮上
  expect(src).toContain("openExternal");
});

test("回归 35f：网页构建里不出现应用内安装的痕迹", async ({ page }) => {
  await page.goto("/");
  // 网页构建 IS_MOBILE_SHELL=false，设置面板里不该有这一块
  await expect(page.getByTestId("mobile-update")).toHaveCount(0);
  const html = await page.content();
  expect(html).not.toContain("ApkInstaller");
});
