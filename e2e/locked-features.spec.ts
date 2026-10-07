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

  test("锁定 5：来源 Badge 按 transcriptSource 显示（字幕 / 语音识别 / 缺省不显示）", async ({ page }) => {
    // 字幕来源 → 「来源：B站字幕」
    const subtitlePayload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000, src: "subtitle_ai" };
    await page.goto(`/result/${Buffer.from(JSON.stringify(subtitlePayload)).toString("base64url")}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("source-badge")).toHaveText(/来源：B站字幕/);

    // ASR 来源 → 「来源：语音识别」（用户可据此区分字幕快路径与否）
    const asrPayload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000, src: "asr" };
    await page.goto(`/result/${Buffer.from(JSON.stringify(asrPayload)).toString("base64url")}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("source-badge")).toHaveText(/来源：语音识别/);

    // 缺省（无来源信息）→ 不显示 Badge
    await page.goto(`/result/${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("source-badge")).toHaveCount(0);
  });

  test("锁定 6：AI 总结面板渲染带时间戳的 keyPoints 与带时间区间的章节", async ({ page }) => {
    await page.goto(`/result/${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    // 核心要点：至少一条带时间戳徽章（keyPoints.time 指向原 transcript）
    const keyPoints = page.getByTestId("key-points");
    await expect(keyPoints).toBeVisible();
    expect(await keyPoints.locator("li").count()).toBeGreaterThanOrEqual(3);
    await expect(keyPoints.getByText(TIMESTAMP_PATTERN).first()).toBeVisible();

    // 章节笔记：时间区间（mm:ss–mm:ss）渲染
    const chapters = page.getByTestId("chapter-timeline");
    await expect(chapters).toBeVisible();
    await expect(chapters.getByText(/\d{1,2}:\d{2}–\d{1,2}:\d{2}/).first()).toBeVisible();
  });

  test("锁定 7：首页模式标识随构建模式渲染（Mock 显示 / 真实隐藏）", async ({ page }) => {
    // VTT_EXPECT_MODE=real 时在真实模式构建上运行（CI 第二阶段）；缺省按 Mock 构建断言
    const expectReal = process.env.VTT_EXPECT_MODE === "real";
    await page.goto("/");

    const badge = page.getByTestId("mock-mode-badge");
    const note = page.getByTestId("mock-mode-note");
    if (expectReal) {
      // 真实模式：严禁出现任何 Mock 标识
      await expect(badge).toHaveCount(0);
      await expect(note).toHaveCount(0);
    } else {
      await expect(badge).toContainText("演示模式 · Mock 数据");
      await expect(note).toBeVisible();
    }
    // 历史硬编码文案不得回退（两种模式都不出现）
    await expect(page.getByText("前端 Demo · Mock 数据演示")).toHaveCount(0);
  });

  test("锁定 8：总结失败降级——文字稿可见 + 错误卡与重试 + 导出同源", async ({ page }) => {
    // 注入总结失败的 Mock 任务（sumFail 标记 → /api/summarize 返回错误）
    const failPayload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000, sumFail: 1 };
    const failTaskId = Buffer.from(JSON.stringify(failPayload)).toString("base64url");
    await page.goto(`/result/${failTaskId}`);

    // 文字稿照常渲染（默认全文阅读 Tab、零时间戳）——总结失败不得拖垮主体
    const article = page.getByTestId("fulltext-article");
    await expect(article).toBeVisible();
    const articleText = await article.innerText();
    expect(articleText.length).toBeGreaterThan(50);
    expect(articleText).not.toMatch(TIMESTAMP_PATTERN);

    // 总结区：错误卡 + 重试按钮（不再整页错误/骨架）
    await expect(page.getByTestId("summary-error")).toBeVisible();
    await expect(page.getByRole("button", { name: "重试总结" })).toBeVisible();
    await expect(page.getByTestId("fulltext-article")).toBeVisible(); // 错误卡出现后文字稿仍在

    // 导出同源回归（summary 为 null 的新边界）：TXT/MD 与页面全文视图同源、零时间戳
    await page.getByRole("button", { name: "导出" }).click();
    const txtDownload = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "纯文本（.txt）" }).click();
    const txt = readFileSync(await (await txtDownload).path(), "utf-8");
    expect(txt).not.toMatch(TIMESTAMP_PATTERN);
    expect(txt).toContain("《");

    await page.getByRole("button", { name: "导出" }).click();
    const mdDownload = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "Markdown（.md）" }).click();
    const md = readFileSync(await (await mdDownload).path(), "utf-8");
    expect(md).toContain("## 正文");
    expect(md).not.toMatch(TIMESTAMP_PATTERN);

    // 三者同源：页面全文视图的首段（前 20 字）必须出现在 TXT 导出中
    const firstParagraph = articleText
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 10);
    expect(firstParagraph).toBeTruthy();
    expect(txt).toContain(firstParagraph!.slice(0, 20));

    // 进度页路径（pollTask 降级，用户主路径）：从 /processing 自然流转，同样降级不整页报错
    const pollPayload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 9_000, sumFail: 1 };
    const pollTaskId = Buffer.from(JSON.stringify(pollPayload)).toString("base64url");
    await page.goto(`/processing/${pollTaskId}`);
    await page.waitForURL(/\/result\//, { timeout: 20_000 });
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    await expect(page.getByTestId("summary-error")).toBeVisible();
    await expect(page.getByRole("button", { name: "重试总结" })).toBeVisible();
  });
});
