import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

/**
 * 前端功能锁定 E2E（docs/frontend-features.md 的自动化强制层）。
 *
 * 运行前提：Mock 模式构建（NEXT_PUBLIC_USE_MOCK 默认 true）。
 * 说明：AI 总结面板的章节笔记时间戳（00:00 / 01:32 等）是契约内的合法信息，
 * 时间戳断言一律通过 data-testid 限定在全文正文 / 时间戳列表区域内，避免整页误报。
 */

/** 构造合法且已完成态的 Mock taskId：与 src/lib/mock-api.ts 的 base64url(payload) 编码一致。
 *  startedAt 提前 11 秒（Mock 转写全程 10 秒），确保 GET 直接返回 completed + transcript。 */
function mockTaskId(): string {
  const payload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 };
  return Buffer.from(JSON.stringify(payload)).toString("base64url");
}

const TIMESTAMP_PATTERN = /\d{1,2}:\d{2}(?::\d{2})?/;

test.describe("前端功能锁定协议", () => {
  test("锁定 1：结果页默认全文阅读，正文零时间戳，可切换时间戳定位", async ({ page }) => {
    await page.goto(`/result/${mockTaskId()}`);

    // 默认激活「全文阅读」Tab，正文区域可见
    const articleTab = page.getByRole("tab", { name: "全文阅读" });
    await expect(articleTab).toBeVisible();
    await expect(articleTab).toHaveAttribute("aria-selected", "true");

    // 断言严格限定在 data-testid=fulltext-article 区域内
    const article = page.getByTestId("fulltext-article");
    await expect(article).toBeVisible();
    const articleText = await article.innerText();
    expect(articleText).not.toMatch(TIMESTAMP_PATTERN);
    expect(articleText.length).toBeGreaterThan(50); // 确实渲染了文章内容

    // 切换「时间戳定位」：列表出现且包含时间戳（限定在列表区域内）
    await page.getByRole("tab", { name: "时间戳定位" }).click();
    const list = page.getByTestId("transcript-list");
    await expect(list).toBeVisible();
    await expect(list.getByText(TIMESTAMP_PATTERN).first()).toBeVisible();
    await expect(page.getByText("点击时间可复制")).toBeVisible();
  });

  test("锁定 2：导出 TXT 零时间戳且包含标题", async ({ page }) => {
    await page.goto(`/result/${mockTaskId()}`);
    const article = page.getByTestId("fulltext-article");
    await expect(article).toBeVisible();

    await page.getByRole("button", { name: "导出" }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "纯文本（.txt）" }).click();
    const file = await download;

    const content = readFileSync(await file.path(), "utf-8");
    expect(content).not.toMatch(TIMESTAMP_PATTERN); // 零时间戳
    expect(content).toContain("《"); // 包含标题（书名号包裹）
    expect(content).toContain("来源："); // 元信息行
  });

  test("锁定 3：导出 MD 含 ## 正文 结构且零时间戳", async ({ page }) => {
    await page.goto(`/result/${mockTaskId()}`);
    const article = page.getByTestId("fulltext-article");
    await expect(article).toBeVisible();

    await page.getByRole("button", { name: "导出" }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "Markdown（.md）" }).click();
    const file = await download;

    const content = readFileSync(await file.path(), "utf-8");
    expect(content.startsWith("# ")).toBe(true); // 一级标题
    expect(content).toContain("## 正文"); // 结构化二级标题
    expect(content).toContain("> 来源："); // 引用元信息
    expect(content).not.toMatch(TIMESTAMP_PATTERN); // 零时间戳
  });

  test("锁定 4：首页转写模式默认本地，选云端出现隐私提示，Mock 模式附无实际效果小字", async ({ page }) => {
    await page.goto("/");

    const mode = page.getByTestId("transcribe-mode");
    await expect(mode).toBeVisible();
    // 默认选中「本地」（锁定默认值）
    await expect(page.getByRole("button", { name: "本地" })).toHaveAttribute("aria-pressed", "true");
    // 未选云端时不出现隐私提示；Mock 模式的小字提示始终存在
    await expect(page.getByTestId("cloud-privacy-notice")).toHaveCount(0);
    await expect(page.getByTestId("mock-no-effect-hint")).toContainText("无实际效果");

    // 切换云端：选中态生效且隐私提示必须出现（锁定项）
    await page.getByRole("button", { name: "云端" }).click();
    await expect(page.getByRole("button", { name: "云端" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("cloud-privacy-notice")).toContainText("上传至第三方");
  });

  test("锁定 5：字幕来源任务显示来源 Badge，ASR 来源任务不显示", async ({ page }) => {
    // 字幕来源的 Mock 任务（payload 携带 src 标记）
    const subtitlePayload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000, src: "subtitle_ai" };
    const subtitleTaskId = Buffer.from(JSON.stringify(subtitlePayload)).toString("base64url");
    await page.goto(`/result/${subtitleTaskId}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("source-badge")).toContainText("来源：B站字幕");

    // 普通（ASR 来源）任务：严禁出现来源 Badge
    await page.goto(`/result/${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("source-badge")).toHaveCount(0);
  });
});
