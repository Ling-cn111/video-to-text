import { expect, test } from "@playwright/test";

import { cloudUnconfiguredMessage, summarizeUnconfiguredMessage } from "../src/lib/capability-hint";

/**
 * P0 修复的回归测试。
 *
 * 编号用「回归 N」而不是「锁定 N」：锁定编号属于 docs/frontend-features.md 的
 * 功能锁定协议，新增锁定项需要先改文档并逐条验收；这里只是本次修复的自动化回归，
 * 不占用锁定编号。
 *
 * 这三条都是「用户看得见、但过去没人断言」的行为，容易在后续重构里悄悄退化：
 * 1. 限流 429 必须给出可预期的等待，而不是笼统的「出了点问题」
 * 2. 刷新结果页不得重复调用 LLM（每次刷新都是真金白银）
 * 3. 转写模式选择刷新后保留（用户改过一次不该被重置）
 *
 * 运行前提同 locked-features.spec.ts：Mock 模式构建（NEXT_PUBLIC_USE_MOCK 默认 true）。
 */

/** 已完成态 Mock taskId：startedAt 提前 11 秒，确保 GET 直接返回 completed。 */
function mockTaskId(): string {
  const payload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 };
  return Buffer.from(JSON.stringify(payload)).toString("base64url");
}

test.describe("P0 回归", () => {
  test("回归 1：429 限流显示「请求太频繁」并给出冷却提示", async ({ page }) => {
    // 拦截总结请求，模拟后端限流中间件的响应形状
    await page.route("**/api/summarize", (route) =>
      route.fulfill({
        status: 429,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "RATE_LIMITED", message: "请求太频繁，请稍后再试" },
        }),
      }),
    );

    await page.goto(`/result?id=${mockTaskId()}`);

    // 文字稿不受总结失败影响（P0 降级：总结失败不拖垮整页）
    const article = page.getByTestId("fulltext-article");
    await expect(article).toBeVisible();
    expect((await article.innerText()).length).toBeGreaterThan(50);

    // 错误卡用的是专属标题，而不是兜底的「出了点问题」
    const errorCard = page.getByTestId("summary-error");
    await expect(errorCard).toBeVisible();
    await expect(errorCard).toContainText("请求太频繁");
    await expect(errorCard).not.toContainText("出了点问题");

    // 冷却提示存在，且「重试」在冷却期内禁用（重试也是 POST，点了必然再 429）
    await expect(page.getByTestId("rate-limit-hint")).toBeVisible();
    await expect(page.getByTestId("rate-limit-hint")).toContainText("秒后可重试");
  });

  test("回归 2：刷新结果页不重复调用 summarize（不重复计费）", async ({ page }) => {
    const summarizeRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/summarize")) summarizeRequests.push(request.url());
    });

    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("key-points")).toBeVisible();
    const afterFirstLoad = summarizeRequests.length;
    expect(afterFirstLoad).toBe(1);

    // 刷新走 recover() 路径：旧实现会再传一遍整份文字稿、再调一次 LLM
    await page.reload();
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("key-points")).toBeVisible();

    expect(summarizeRequests.length).toBe(afterFirstLoad);
    // 缓存确实写进了 sessionStorage（而不是靠别的路径偶然命中）
    const cachedKeys = await page.evaluate(() =>
      Object.keys(sessionStorage).filter((key) => key.startsWith("summary:")),
    );
    expect(cachedKeys.length).toBeGreaterThan(0);
  });

  test("回归 3：转写模式选择刷新后保留", async ({ page }) => {
    await page.goto("/");
    const local = page.getByRole("button", { name: "本地", exact: true });
    const cloud = page.getByRole("button", { name: "云端", exact: true });

    // 默认本地（锁定项 §6）
    await expect(local).toHaveAttribute("aria-pressed", "true");

    await cloud.click();
    await expect(cloud).toHaveAttribute("aria-pressed", "true");
    expect(await page.evaluate(() => localStorage.getItem("engine"))).toBe("cloud");

    // 刷新后仍为云端；旧实现会退回默认本地
    await page.reload();
    await expect(cloud).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("mode-description")).toContainText("云端模式");
  });
});

test.describe("未配置提示的文案分环境", () => {
  test("本地给配置指引，公网不暴露内部实现细节", () => {
    const localHosts = ["localhost", "127.0.0.1"];
    const publicHosts = ["video-to-text-six.vercel.app", "example.com"];

    for (const host of localHosts) {
      expect(cloudUnconfiguredMessage(host)).toContain("setup.bat");
      expect(summarizeUnconfiguredMessage(host)).toContain("DEEPSEEK_API_KEY");
    }

    for (const host of publicHosts) {
      // 公网访客既没有仓库也没有脚本：出现这些词只会让站点看起来像坏了
      expect(cloudUnconfiguredMessage(host)).not.toMatch(/setup\.bat|CLOUD_ASR_API_KEY/);
      expect(summarizeUnconfiguredMessage(host)).not.toMatch(/\.env\.local|DEEPSEEK_API_KEY/);
      // 但仍要说清楚「还能做什么」
      expect(cloudUnconfiguredMessage(host)).toContain("本地模式");
      expect(summarizeUnconfiguredMessage(host)).toContain("文字稿");
    }
  });
});
