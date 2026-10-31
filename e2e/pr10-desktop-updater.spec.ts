import { expect, test } from "@playwright/test";

import {
  CHECK_TIMEOUT_MS,
  FIRST_CHECK_DELAY_MS,
  RECHECK_INTERVAL_MS,
  RELEASES_PAGE,
  UPDATE_ENDPOINT,
  compareVersions,
  formatBytes,
  isNewerVersion,
  parseVersion,
  progressPercent,
  shouldEnableUpdater,
  truncateNotes,
} from "../src/lib/updater";

/**
 * PR10 的回归测试（桌面端自动更新）。
 *
 * 编号从回归 30 起，接在 pr9 的 29b 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 版本比较**只能**按 `主.次.修订` 数值比，不能按字符串比 ——
 *    `"0.1.10" > "0.1.9"` 字符串比较是 false，会把新版判成旧版，用户永远收不到更新。
 * 2. 解析失败一律「不比当前新」。宁可漏一次更新，也不要因为端点返回了奇怪的
 *    版本号就提示用户装一个坏包。
 * 3. 更新只在桌面壳 + 生产构建下启用。网页端刷新即最新、Android 走 APK 覆盖安装，
 *    在这两处调 Tauri 命令只会抛「command not found」。
 * 4. 下载进度总量未知时返回 null，不能瞎猜一个百分比。
 *
 * 这些是纯函数测试，不需要 Mock 构建，真实模式下同样跑。
 */

test.describe("桌面端自动更新", () => {
  test("回归 30：版本比较按数值而非字符串", () => {
    // 字符串比较下 "0.1.10" < "0.1.9"，这正是最典型的翻车点
    expect(compareVersions("0.1.10", "0.1.9")).toBeGreaterThan(0);
    expect(compareVersions("0.1.9", "0.1.10")).toBeLessThan(0);
    expect(compareVersions("0.2.0", "0.1.99")).toBeGreaterThan(0);
    expect(compareVersions("1.0.0", "0.99.99")).toBeGreaterThan(0);
    expect(compareVersions("0.1.5", "0.1.5")).toBe(0);

    expect(isNewerVersion("0.1.5", "0.1.4")).toBe(true);
    expect(isNewerVersion("0.1.4", "0.1.4")).toBe(false);
    // 降级不算「有更新」：否则回滚发版会让所有用户被提示装旧版
    expect(isNewerVersion("0.1.3", "0.1.4")).toBe(false);
  });

  test("回归 30b：版本号解析与非法输入", () => {
    expect(parseVersion("0.1.5")).toEqual([0, 1, 5]);
    expect(parseVersion("  1.2.3  ")).toEqual([1, 2, 3]);
    // 预发布后缀与构建元数据丢掉（本项目从不发预发布版）
    expect(parseVersion("0.1.5-beta.1")).toEqual([0, 1, 5]);
    expect(parseVersion("0.1.5+abc123")).toEqual([0, 1, 5]);

    // 解析失败一律 0，不能抛
    expect(parseVersion("")).toBeNull();
    expect(parseVersion("v0.1.5")).toBeNull();
    expect(parseVersion("latest")).toBeNull();
    expect(compareVersions("garbage", "0.1.4")).toBe(0);
    expect(isNewerVersion("garbage", "0.1.4")).toBe(false);
    expect(isNewerVersion("0.1.5", "garbage")).toBe(false);
  });

  test("回归 30c：只有桌面壳的生产构建才启用更新", () => {
    expect(shouldEnableUpdater({ isDesktopShell: true, isProduction: true })).toBe(true);

    // 网页端：刷新就是最新版，没有「更新」这个概念
    expect(shouldEnableUpdater({ isDesktopShell: false, isProduction: true })).toBe(false);
    // tauri dev 下的可执行文件没有签名，查到更新也装不上
    expect(shouldEnableUpdater({ isDesktopShell: true, isProduction: false })).toBe(false);
    expect(shouldEnableUpdater({ isDesktopShell: false, isProduction: false })).toBe(false);
  });

  test("回归 30d：进度与体积格式化", () => {
    expect(progressPercent(0, 1000)).toBe(0);
    expect(progressPercent(500, 1000)).toBe(50);
    expect(progressPercent(1000, 1000)).toBe(100);
    // 超出总量时夹到 100，不能显示 120%
    expect(progressPercent(1200, 1000)).toBe(100);

    // 总量未知时返回 null（界面切成不确定态），不能猜
    expect(progressPercent(500)).toBeNull();
    expect(progressPercent(500, 0)).toBeNull();
    expect(progressPercent(500, -1)).toBeNull();

    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(-5)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(2468491)).toBe("2.4 MB");
  });

  test("回归 30e：发布说明截断不会切断在半个字符上", () => {
    expect(truncateNotes(undefined)).toBe("");
    expect(truncateNotes("")).toBe("");
    expect(truncateNotes("短说明")).toBe("短说明");

    const long = "说".repeat(1000);
    const cut = truncateNotes(long, 100);
    // 100 个字符 + 省略号
    expect(Array.from(cut).length).toBe(101);
    expect(cut.endsWith("…")).toBe(true);
  });

  test("回归 30f：端点与下载页指向同一个仓库", () => {
    // 端点是 latest.json，下载页是 Release 列表页 —— 两者必须同仓库，
    // 否则会出现「检查到更新但点进去是另一个项目的 Release」
    expect(UPDATE_ENDPOINT).toContain("Ling-cn111/video-to-text");
    expect(UPDATE_ENDPOINT.endsWith("latest.json")).toBe(true);
    expect(RELEASES_PAGE).toContain("Ling-cn111/video-to-text");
    // 下载页**不能**用 /releases/latest：GitHub 会把它 302 到最新一个非
    // prerelease 的 tag。新包配旧 Release 页面时，用户看到的是旧版本，会以为
    // 「打开发布页这个按钮根本没生效」（v0.1.6 真机反馈原话）。
    expect(RELEASES_PAGE.endsWith("releases")).toBe(true);
    expect(RELEASES_PAGE).not.toContain("releases/latest");

    // 超时必须短于复查间隔，否则上一轮还没超时就开始下一轮，请求会堆起来
    expect(CHECK_TIMEOUT_MS).toBeLessThan(RECHECK_INTERVAL_MS);
  });

  test("回归 30g：网页构建里不渲染「检查更新」入口", async ({ page }) => {
    // 网页端刷新即最新版。渲染一个点了什么都不会发生的按钮，
    // 比不渲染更糟 —— 用户会以为「点了没反应 = 功能坏了」。
    // 这个断言同时守住「别为了省事把 UpdateButton 直接塞进顶栏而不加门禁」。
    await page.goto("/");
    await expect(page.getByTestId("open-api-settings")).toBeVisible();
    await expect(page.getByTestId("check-update")).toHaveCount(0);
  });
});
