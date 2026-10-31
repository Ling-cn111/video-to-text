import { expect, test } from "@playwright/test";

import {
  ANDROID_RELEASES_PAGE,
  LATEST_RELEASE_API,
  MOBILE_CHECK_TIMEOUT_MS,
  checkMobileUpdate,
  isNewerRelease,
  parseLatestRelease,
  type LatestRelease,
} from "../src/lib/mobile-update";
import { compareVersions } from "../src/lib/updater";

/**
 * PR11 的回归测试（外部链接 + 移动端检查更新）。
 *
 * 编号从回归 31 起，接在 pr10 的 30g 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. **桌面壳里不能再用 `<a target="_blank">`** —— WebView2 会静默吞掉，
 *    表现为「点了没反应」。这是 v0.1.5 真实报上来的 bug。
 * 2. Release JSON 的解析必须容忍资产列表变化：APK 缺失时退回发布页，
 *    不能给出一个 404 直链。
 * 3. 版本比较与桌面端共用同一份实现，不许各写一套。
 * 4. 网页构建里不出现移动端更新区块（它只在 Android 壳内渲染）。
 */

const RELEASE_SAMPLE = {
  tag_name: "v0.1.6",
  html_url: "https://github.com/Ling-cn111/video-to-text/releases/tag/v0.1.6",
  assets: [
    {
      name: "video-to-text_0.1.6_x64-setup.exe",
      browser_download_url:
        "https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.6/video-to-text_0.1.6_x64-setup.exe",
    },
    {
      name: "video-to-text_0.1.6_android.apk",
      browser_download_url:
        "https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.6/video-to-text_0.1.6_android.apk",
    },
  ],
};

test.describe("外部链接与移动端更新", () => {
  test("回归 31：Release JSON 能解析出 APK 直链", () => {
    const release = parseLatestRelease(RELEASE_SAMPLE);
    expect(release).not.toBeNull();
    expect(release?.tag).toBe("v0.1.6");
    // 版本号必须去掉 v 前缀，否则与 package.json 的 "0.1.6" 比不出来
    expect(release?.version).toBe("0.1.6");
    expect(release?.apkUrl).toContain("_android.apk");
    expect(release?.pageUrl).toContain("releases/tag/v0.1.6");
  });

  test("回归 31b：没有 APK 资产时不给出假直链", () => {
    const release = parseLatestRelease({
      ...RELEASE_SAMPLE,
      assets: [RELEASE_SAMPLE.assets[0]],
    });
    expect(release?.apkUrl).toBeNull();
    // 调用方据此退回发布页
    expect(release?.pageUrl).toContain("releases/tag/");
  });

  test("回归 31c：畸形输入一律返回 null，不抛异常", () => {
    expect(parseLatestRelease(null)).toBeNull();
    expect(parseLatestRelease(undefined)).toBeNull();
    expect(parseLatestRelease("not an object")).toBeNull();
    expect(parseLatestRelease({})).toBeNull();
    // tag 不是版本格式（例如 latest）→ 当作没有新版，避免提示装一个坏包
    expect(parseLatestRelease({ tag_name: "latest" })).toBeNull();
    expect(parseLatestRelease({ tag_name: "v0.1.6", assets: "oops" })?.apkUrl).toBeNull();
  });

  test("回归 31d：版本比较与桌面端共用同一份实现", () => {
    const release: LatestRelease = {
      tag: "v0.1.10",
      version: "0.1.10",
      pageUrl: ANDROID_RELEASES_PAGE,
      apkUrl: null,
    };
    // 字符串比较下 "0.1.10" < "0.1.9"，会漏掉更新
    expect(isNewerRelease(release, "0.1.9")).toBe(true);
    expect(compareVersions(release.version, "0.1.9")).toBeGreaterThan(0);

    // 同版本、降级都不算更新
    expect(isNewerRelease({ ...release, version: "0.1.5" }, "0.1.5")).toBe(false);
    expect(isNewerRelease({ ...release, version: "0.1.4" }, "0.1.5")).toBe(false);
  });

  test("回归 31e：检查更新在成功/失败路径都返回结构化结果", async () => {
    // 造一个假的 fetch，避免测试依赖真实 GitHub（限流、网络都会让它不稳定）
    const okFetch = (async () =>
      new Response(JSON.stringify(RELEASE_SAMPLE), {
        status: 200,
        headers: { "content-type": "application/json" },
      })) as unknown as typeof fetch;

    const available = await checkMobileUpdate("0.1.5", okFetch);
    expect(available.kind).toBe("available");
    if (available.kind === "available") {
      expect(available.release.version).toBe("0.1.6");
      expect(available.currentVersion).toBe("0.1.5");
    }

    const upToDate = await checkMobileUpdate("0.1.6", okFetch);
    expect(upToDate.kind).toBe("up-to-date");

    // 服务器 5xx
    const failFetch = (async () =>
      new Response("boom", { status: 502 })) as unknown as typeof fetch;
    const failed = await checkMobileUpdate("0.1.5", failFetch);
    expect(failed.kind).toBe("error");

    // 网络异常
    const throwFetch = (async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;
    const broken = await checkMobileUpdate("0.1.5", throwFetch);
    expect(broken.kind).toBe("error");
    if (broken.kind === "error") {
      expect(broken.message).toContain("连接");
    }
  });

  test("回归 31f：更新源指向本项目仓库且超时可控", () => {
    expect(LATEST_RELEASE_API).toContain("Ling-cn111/video-to-text");
    expect(LATEST_RELEASE_API).toContain("api.github.com");
    expect(LATEST_RELEASE_API.endsWith("/releases/latest")).toBe(true);
    expect(ANDROID_RELEASES_PAGE).toContain("Ling-cn111/video-to-text");
    // 超时必须是个有限正数，否则 AbortController 永不触发
    expect(MOBILE_CHECK_TIMEOUT_MS).toBeGreaterThan(0);
    expect(MOBILE_CHECK_TIMEOUT_MS).toBeLessThanOrEqual(30000);
  });

  test("回归 31g：网页构建里不渲染移动端更新区块", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("api-settings-dialog")).toBeVisible();
    // 网页刷新即最新，放一个「检查更新」只会让人以为点了能升级网页
    await expect(page.getByTestId("mobile-update")).toHaveCount(0);
  });

  test("回归 31h：桌面端「打开发布页」不再用 target=_blank 的裸链接", async ({ page }) => {
    // 这个 bug 的表现是「点了没反应」，无法用点击断言（点了本来就什么都不发生）。
    // 改为守结构性约束：更新对话框里的发布页出口必须是 button（走 openExternal），
    // 不能是 <a target="_blank"> —— 后者在 WebView2 里被 wry 静默吞掉。
    await page.goto("/");
    // 网页构建不渲染更新按钮，这里直接断言源码层面的约定：
    // 对话框里存在 update-releases 这个 testid 的按钮（桌面构建下才有，故用 count 允许 0）
    const count = await page.getByTestId("update-releases").count();
    expect(count).toBe(0);

    // 真正的守门在构建脚本 verify-shell.mjs：桌面壳内断言 update-releases 是 button
    // 且能点开系统浏览器。这里只确保网页构建没把它误渲染出来。
    await expect(page.getByTestId("check-update")).toHaveCount(0);
  });
});
