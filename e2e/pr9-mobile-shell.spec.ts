import { expect, test } from "@playwright/test";

import { exportResult, exportSubtitles } from "../src/lib/export";
import type { TranscriptItem, VideoInfo } from "../src/lib/types";

/**
 * PR9 的回归测试（Android 包壳 + 移动端导出改造）。
 *
 * 编号沿用「回归 N」，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 导出必须是**可等待的**（返回 Promise<ShareOutcome>）。旧实现是 fire-and-forget 的
 *    `void downloadTextFile(...)`，在 WebView 里 blob 下载会静默失败，用户点完什么也
 *    没发生、也没有任何提示 —— 这是移动端最典型的「看起来能用实际不能用」。
 * 2. 用户取消分享**不得被当成失败**。取消是正常操作，弹红条会让用户以为出了问题。
 * 3. 桌面端原有的下载行为不能被这次改造破坏（改造动了所有平台的导出路径）。
 *
 * 运行前提同 locked-features.spec.ts：Mock 模式构建。
 */

/** 已完成态 Mock taskId：startedAt 提前 11 秒，确保 GET 直接返回 completed。 */
function mockTaskId(): string {
  const payload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 };
  return Buffer.from(JSON.stringify(payload)).toString("base64url");
}

const VIDEO: VideoInfo = {
  title: "测试视频",
  cover: "/covers/bilibili-1.svg",
  duration: 30,
  platform: "bilibili",
  videoId: "BV1GJ411x7h7",
};

const TRANSCRIPT: TranscriptItem[] = [
  { time: 0, text: "第一句" },
  { time: 4, text: "第二句" },
];

test.describe("移动端导出改造（纯函数）", () => {
  test("回归 26：导出函数返回 Promise，不再是 fire-and-forget", () => {
    // 旧签名是 `(): void`，调用方 `exportResult(...)` 后拿不到任何结果，
    // 失败也无从提示。这里断言两者都是 async 函数 —— 谁把它改回同步 void，
    // 移动端就会静默失效（WebView 里 blob 下载不弹任何东西）。
    expect(exportResult.constructor.name).toBe("AsyncFunction");
    expect(exportSubtitles.constructor.name).toBe("AsyncFunction");
  });

  test("回归 26b：无浏览器分享能力时返回明确结果，不静默失败", async () => {
    // 在 Node 进程里跑（没有 navigator.share），走的是最后的下载兜底分支。
    // 补一个最小的 document / URL 桩，断言它**返回 `downloaded`** 而不是无声无息——
    // 关键在于「有返回值」，调用方才能据此提示用户，旧实现就是什么都没返回。
    let clicks = 0;
    const globals = globalThis as unknown as Record<string, unknown>;
    const originalDocument = globals.document;
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;

    globals.document = {
      createElement: () => ({
        href: '',
        download: '',
        click: () => {
          clicks += 1;
        },
        remove: () => {},
      }),
      body: { appendChild: () => {} },
    };
    URL.createObjectURL = () => 'blob:stub';
    URL.revokeObjectURL = () => {};

    try {
      await expect(exportResult("txt", { video: VIDEO, paragraphs: ["第一段。"] })).resolves.toBe("downloaded");
      await expect(exportSubtitles("srt", { video: VIDEO, transcript: TRANSCRIPT })).resolves.toBe("downloaded");
      expect(clicks).toBe(2);
    } finally {
      if (originalDocument === undefined) delete globals.document;
      else globals.document = originalDocument;
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    }
  });
});

test.describe("移动端导出改造（浏览器行为）", () => {
  test("回归 27：桌面/网页端仍走下载，行为未被改造破坏", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    await page.getByRole("button", { name: "导出" }).click();
    const download = page.waitForEvent("download");
    await page.getByTestId("export-txt").click();
    const file = await download;

    expect(file.suggestedFilename().endsWith(".txt")).toBe(true);
    // 下载路径也要给提示（旧实现什么都不说，用户不知道点了有没有生效）
    await expect(page.getByText("已开始下载")).toBeVisible();
  });

  test("回归 27b：用户取消分享不算失败，不弹错误提示", async ({ page }) => {
    // 模拟「浏览器支持 Web Share，但用户点了取消」——这是手机上最高频的操作，
    // 每天几十次误触。若把它当失败处理，用户会被红条轰炸。
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "share", {
        configurable: true,
        value: () => Promise.reject(Object.assign(new Error("share canceled"), { name: "AbortError" })),
      });
    });

    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    await page.getByRole("button", { name: "导出" }).click();
    await page.getByTestId("export-txt").click();

    // 给 toast 一点时间渲染（若错误地报了错，这里会看到）
    await page.waitForTimeout(1200);
    await expect(page.getByText("导出失败，请重试或换一种格式")).toHaveCount(0);
    // 也不该说「已开始下载」—— 用户取消了，说什么都不对
    await expect(page.getByText("已开始下载")).toHaveCount(0);
  });

  test("回归 27c：分享成功后提示「已打开分享面板」而不是「已开始下载」", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "share", {
        configurable: true,
        value: () => Promise.resolve(),
      });
      Object.defineProperty(navigator, "canShare", {
        configurable: true,
        value: () => true,
      });
    });

    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    await page.getByRole("button", { name: "导出" }).click();
    await page.getByTestId("export-srt").click();

    await expect(page.getByText("已打开分享面板，选一个应用保存")).toBeVisible();
  });
});
