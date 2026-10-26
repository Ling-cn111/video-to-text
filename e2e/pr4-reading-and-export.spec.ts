import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { buildCues, buildSrt, buildVtt, toSrtTime, toVttTime } from "../src/lib/subtitle-export";
import { buildPlainText } from "../src/lib/export";
import type { TranscriptItem, VideoInfo } from "../src/lib/types";

/**
 * PR4 的回归测试（P1-9 阅读体验 / P1-10 导出字幕 / P1-11 总结详细度）。
 *
 * 编号沿用「回归 N」，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 这一组测试真正想守住的不变量：
 * 1. 字幕文件必须带时间轴，且格式能被播放器解析（序号 / `-->` / 毫秒分隔符 / WEBVTT 头）
 * 2. 全文导出必须仍然零时间戳 —— 新增字幕导出后最容易发生的退化，就是有人顺手把
 *    时间戳塞进了 buildPlainText，让「阅读产物」变成半份字幕
 * 3. 阅读工具（目录 / 搜索 / 字号）不得改变正文 textContent：页面正文、复制内容、
 *    TXT 导出三者同源是锁定项 §2 的前提
 * 4. 切换总结详细度必须真的换一份结果，而不是只换按钮的选中态
 *
 * 运行前提同 locked-features.spec.ts：Mock 模式构建（NEXT_PUBLIC_USE_MOCK 默认 true）。
 */

/** 已完成态 Mock taskId：startedAt 提前 11 秒，确保 GET 直接返回 completed。 */
function mockTaskId(): string {
  const payload = { v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 };
  return Buffer.from(JSON.stringify(payload)).toString("base64url");
}

const TIMESTAMP_PATTERN = /\d{1,2}:\d{2}(?::\d{2})?/;
/** SRT / VTT 时间码：00:00:12,500 或 00:00:12.500 */
const SRT_TIME = /^\d{2}:\d{2}:\d{2},\d{3}$/;
const VTT_TIME = /^\d{2}:\d{2}:\d{2}\.\d{3}$/;

/** 触发一次导出并返回文件内容（文件名一并返回，用于断言扩展名） */
async function downloadVia(
  page: import("@playwright/test").Page,
  itemTestId: string,
): Promise<{ name: string; content: string }> {
  await page.getByRole("button", { name: "导出" }).click();
  const download = page.waitForEvent("download");
  await page.getByTestId(itemTestId).click();
  const file = await download;
  return { name: file.suggestedFilename(), content: readFileSync(await file.path(), "utf-8") };
}

test.describe("P1-10 导出字幕", () => {
  test("回归 7：SRT 带序号与 `-->` 时间轴，文件名标「字幕」", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    const { name, content } = await downloadVia(page, "export-srt");

    // 文件名与「文字稿」区分开：下载列表里一眼能认出哪份是字幕
    expect(name).toContain("字幕");
    expect(name.endsWith(".srt")).toBe(true);

    // 结构：序号行 + 时间轴行 + 文本行，条目间空行
    const blocks = content.trim().split("\n\n");
    expect(blocks.length).toBeGreaterThan(3);
    expect(blocks[0].split("\n")[0]).toBe("1");

    const timeLine = blocks[0].split("\n")[1];
    const [start, end] = timeLine.split(" --> ");
    expect(start).toMatch(SRT_TIME);
    expect(end).toMatch(SRT_TIME);

    // 字幕必须带时间戳 —— 这与 TXT/MD 的零时间戳铁律相反，是刻意的
    expect(content).toMatch(TIMESTAMP_PATTERN);
    expect(content).toContain("-->");

    // 每条字幕的结束时间必须晚于开始时间（写反了播放器会直接不显示）
    for (const block of blocks) {
      const lines = block.split("\n");
      const [rawStart, rawEnd] = lines[1].split(" --> ");
      const toSeconds = (value: string) => {
        const [hms, ms] = value.split(",");
        const [h, m, s] = hms.split(":").map(Number);
        return h * 3600 + m * 60 + s + Number(ms) / 1000;
      };
      expect(toSeconds(rawEnd)).toBeGreaterThan(toSeconds(rawStart));
    }
  });

  test("回归 7b：VTT 以 WEBVTT 开头，时间码用点号", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    const { name, content } = await downloadVia(page, "export-vtt");
    expect(name.endsWith(".vtt")).toBe(true);

    // WEBVTT 头必须在第一行，否则浏览器 / 播放器会整份拒绝
    expect(content.startsWith("WEBVTT")).toBe(true);

    const blocks = content.trim().split("\n\n");
    // 头之后可能有 NOTE 块，跳过不含 `-->` 的块
    const cue = blocks.find((block) => block.includes("-->"));
    expect(cue).toBeTruthy();
    const [start, end] = cue!.split("\n").find((line) => line.includes("-->"))!.split(" --> ");
    expect(start).toMatch(VTT_TIME);
    expect(end).toMatch(VTT_TIME);
    // VTT 时间轴不允许 SRT 的逗号毫秒分隔符（只在时间轴上断言，避免正文里的逗号误报）
    for (const line of content.split("\n").filter((item) => item.includes("-->"))) {
      expect(line).not.toMatch(/\d,\d{3}/);
    }
  });

  test("回归 7c：新增字幕导出后，TXT/MD 仍然零时间戳", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    const txt = await downloadVia(page, "export-txt");
    expect(txt.content).not.toMatch(TIMESTAMP_PATTERN);
    expect(txt.content).toContain("《");

    const md = await downloadVia(page, "export-md");
    expect(md.content).not.toMatch(TIMESTAMP_PATTERN);
    expect(md.content).toContain("## 正文");
  });
});

test.describe("P1-10 字幕时间轴（纯函数）", () => {
  const transcript: TranscriptItem[] = [
    { time: 0, text: "第一句" },
    { time: 4, text: "第二句" },
    { time: 9, text: "第三句" },
  ];

  test("回归 7d：时间码格式与毫秒进位", () => {
    expect(toSrtTime(0)).toBe("00:00:00,000");
    expect(toSrtTime(12.5)).toBe("00:00:12,500");
    expect(toSrtTime(3725.25)).toBe("01:02:05,250");
    expect(toVttTime(12.5)).toBe("00:00:12.500");
  });

  test("回归 7e：结束时间取下一条起点，最后一条用视频时长兜底", () => {
    const cues = buildCues(transcript, 30);
    expect(cues).toHaveLength(3);
    expect(cues[0].start).toBe(0);
    expect(cues[0].end).toBe(4); // 下一条的起点
    expect(cues[2].end).toBe(30); // 兜底到时长，而不是停在 9
    // 时间必须单调不减，否则播放器行为不可预期
    for (let index = 1; index < cues.length; index += 1) {
      expect(cues[index].start).toBeGreaterThanOrEqual(cues[index - 1].start);
    }
  });

  test("回归 7f：duration 缺失时最后一条至少比起点晚 1 秒", () => {
    // duration=0 是「不知道时长」的合法输入（老数据 / 元信息缺失）
    const cues = buildCues(transcript, 0);
    const last = cues[cues.length - 1];
    expect(last.end).toBeGreaterThan(last.start);
  });

  test("回归 7g：超长句被拆成多条，且文字不丢失", () => {
    const long: TranscriptItem[] = [{ time: 0, text: "这是一句非常长的字幕文本".repeat(12) }];
    const cues = buildCues(long, 60);
    expect(cues.length).toBeGreaterThan(1);
    // 拆分只切分、不丢字：拼回去应与原文一致
    expect(cues.map((cue) => cue.text).join("")).toBe(long[0].text);
    for (const cue of cues) {
      expect(cue.end).toBeGreaterThan(cue.start);
    }
  });

  test("回归 7h：buildSrt / buildVtt 与页面导出同源", () => {
    const video: VideoInfo = {
      title: "测试视频",
      cover: "/covers/bilibili-1.svg",
      duration: 30,
      platform: "bilibili",
      videoId: "BV1GJ411x7h7",
    };
    const srt = buildSrt(transcript, 30);
    expect(srt.split("\n\n")[0].startsWith("1\n")).toBe(true);

    const vtt = buildVtt(transcript, video);
    expect(vtt.startsWith("WEBVTT")).toBe(true);
    expect(vtt).toContain("测试视频"); // NOTE 里带标题，便于事后辨认

    // 阅读产物同源：buildPlainText 不含任何时间戳
    const plain = buildPlainText({ video, paragraphs: ["第一段。", "第二段。"] });
    expect(plain).not.toMatch(TIMESTAMP_PATTERN);
  });
});

test.describe("P1-9 阅读体验", () => {
  test("回归 8：目录可跳转、搜索命中高亮、字号可切换并记忆", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    const article = page.getByTestId("fulltext-article");
    await expect(article).toBeVisible();

    // 目录：Mock 数据带章节，因此目录必须出现
    const toc = page.getByTestId("article-toc");
    await expect(toc).toBeVisible();
    const tocButtons = toc.getByRole("button");
    expect(await tocButtons.count()).toBeGreaterThan(1);
    await tocButtons.nth(1).click(); // 点击不报错即可（滚动是视觉行为，不做像素断言）

    // 搜索：命中时给出计数，且正文 textContent 不因高亮而变化
    const beforeText = await article.innerText();
    const search = page.getByTestId("article-search");
    await search.fill("时间戳");
    await expect(page.getByTestId("article-search-result")).toContainText("找到");
    expect(await article.innerText()).toBe(beforeText);

    // 未命中时明确说「没有」，而不是静默无反应
    await search.fill("这段文字肯定不存在于任何一段里");
    await expect(page.getByTestId("article-search-result")).toContainText("没有段落包含");

    // 清除搜索后正文恢复原样
    await search.fill("");
    await expect(page.getByTestId("article-search-result")).toHaveCount(0);
    expect(await article.innerText()).toBe(beforeText);

    // 字号：默认「中」，切到「大」后段落类名与 localStorage 同步变化
    const fontGroup = page.getByTestId("article-font-size");
    await expect(fontGroup.getByRole("button", { name: "中" })).toHaveAttribute("aria-pressed", "true");
    await fontGroup.getByRole("button", { name: "大" }).click();
    await expect(fontGroup.getByRole("button", { name: "大" })).toHaveAttribute("aria-pressed", "true");
    expect(await page.evaluate(() => localStorage.getItem("readingFontSize"))).toBe("lg");
    await expect(article.locator("p").first()).toHaveClass(/text-\[17px\]/);

    // 刷新后仍是「大」：字号是阅读偏好，不该每次重设
    await page.reload();
    await expect(page.getByTestId("article-font-size").getByRole("button", { name: "大" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("回归 8b：时间戳列表高度自适应，短稿不留大片空白", async ({ page }) => {
    // 用抖音示例（10 句，内容高度 < 上限）：固定高度的旧实现会在这里留出几百 px 空白
    const shortTaskId = Buffer.from(
      JSON.stringify({ v: "7301234567890123456", p: "douyin", s: Date.now() - 11_000 }),
    ).toString("base64url");
    await page.goto(`/result?id=${shortTaskId}`);
    await page.getByRole("tab", { name: "时间戳定位" }).click();
    const list = page.getByTestId("transcript-list");
    await expect(list).toBeVisible();

    // max-h 必须落在视口（真正滚动的元素）上：加在 Root 上会让长稿被裁掉且滚不动
    const viewport = list.locator("[data-radix-scroll-area-viewport]");
    await expect(viewport).toHaveCount(1);
    const viewportClass = (await viewport.getAttribute("class")) ?? "";
    expect(viewportClass).toContain("max-h-");
    expect(viewportClass).not.toMatch(/(^|\s)h-\[\d+px\]/);

    // 内容没超过上限：列表按内容高度，明显小于 420 的旧固定值
    const box = await list.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeLessThan(420);
  });
});

test.describe("P1-11 总结详细度", () => {
  test("回归 9：切换档位真的换一份结果，并记住选择", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    const points = page.getByTestId("key-points");
    await expect(points).toBeVisible();

    // 默认 standard（与后端 DEFAULT_DETAIL 一致）
    const switcher = page.getByTestId("summary-detail-switch");
    await expect(switcher.getByTestId("summary-detail-standard")).toHaveAttribute("aria-pressed", "true");

    const standardCount = await points.locator("li").count();
    expect(standardCount).toBeGreaterThan(3);

    // 精简：要点条数必须真的变少（Mock 按 MOCK_DETAIL_MAX_POINTS 裁剪，与后端上限一致）
    await switcher.getByTestId("summary-detail-brief").click();
    await expect(switcher.getByTestId("summary-detail-brief")).toHaveAttribute("aria-pressed", "true");
    await expect(points.locator("li")).toHaveCount(3);
    expect(await page.evaluate(() => localStorage.getItem("summaryDetail"))).toBe("brief");

    // 切回标准：从缓存读回，条数恢复
    await switcher.getByTestId("summary-detail-standard").click();
    await expect(points.locator("li")).toHaveCount(standardCount);
    expect(await page.evaluate(() => localStorage.getItem("summaryDetail"))).toBe("standard");
  });

  test("回归 9b：档位选择刷新后保留（用户偏好）", async ({ page }) => {
    await page.goto(`/result?id=${mockTaskId()}`);
    await expect(page.getByTestId("key-points")).toBeVisible();

    await page.getByTestId("summary-detail-brief").click();
    await expect(page.getByTestId("summary-detail-brief")).toHaveAttribute("aria-pressed", "true");

    await page.reload();
    await expect(page.getByTestId("key-points")).toBeVisible();
    await expect(page.getByTestId("summary-detail-brief")).toHaveAttribute("aria-pressed", "true");
    // 刷新后仍按 brief 渲染（缓存键里带档位，不会拿回 standard 的旧结果）
    await expect(page.getByTestId("key-points").locator("li")).toHaveCount(3);
  });
});
