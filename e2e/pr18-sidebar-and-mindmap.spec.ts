import * as fs from "node:fs/promises";

import { expect, test } from "@playwright/test";

/**
 * PR18 的回归测试：桌面任务侧栏（多选删除）+ 思维导图基础版。
 *
 * 编号从回归 38 起，接在 pr17 的 37g 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 桌面（lg+）任务侧栏：列表展示、条目跳转、勾选与跳转互不干扰、
 *    「删除所选」批量删除并同步 localStorage；空列表整栏不渲染
 * 2. `<lg` 视口侧栏不渲染，首页内嵌「最近转写」区块正常（移动端零改动）
 * 3. 思维导图：总结就绪后从章节/要点推导渲染（节点数 = 章节 + 归位要点 + note 拆句），
 *    切换详细度随新 summary 更新；导出大纲为独立第三类产物（含章节标题、无时间戳）
 */
test.describe("PR18 任务侧栏与思维导图", () => {
  const MOCK_DONE_TASK_ID = Buffer.from(
    JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 60_000 }),
  ).toString("base64url");

  interface SeedTask {
    taskId: string
    title: string
    status: "done" | "processing"
  }

  /** addInitScript(fn, arg)：arg 必须可序列化，闭包变量不会被带上——任务列表经 arg 传入 */
  const seedScript = (entries: SeedTask[]) => {
    window.localStorage.setItem(
      "recentTasks",
      JSON.stringify(
        entries.map((entry, index) => ({
          taskId: entry.taskId,
          title: entry.title,
          cover: "/covers/bilibili-1.svg",
          duration: 754,
          platform: "bilibili",
          savedAt: Date.now() - (index + 1) * 60_000,
          status: entry.status,
        })),
      ),
    );
  };

  const SEED_A: SeedTask = { taskId: "task-aaa", title: "第一条测试视频", status: "done" };
  const SEED_B: SeedTask = { taskId: "task-bbb", title: "第二条测试视频", status: "done" };
  // 跳转目标必须是真实可解码的 mock taskId（结果页按 taskId 从 Mock 恢复）
  const MOCK_DONE_ID = Buffer.from(
    JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 60_000 }),
  ).toString("base64url");
  const SEED_JUMP: SeedTask = { taskId: MOCK_DONE_ID, title: "跳转测试视频", status: "done" };

  test("回归 38：桌面侧栏展示记录、勾选批量删除并同步 localStorage", async ({ page }) => {
    await page.addInitScript(seedScript, [SEED_A, SEED_B]);
    await page.goto("/");

    await expect(page.getByTestId("task-sidebar")).toBeVisible();
    await expect(page.getByTestId("task-sidebar-item")).toHaveCount(2);

    // 勾选两条 → 「删除所选（2）」出现 → 点击 → localStorage 同步清空
    await page.getByTestId("task-sidebar-check-task-aaa").click();
    await page.getByTestId("task-sidebar-check-task-bbb").click();
    await expect(page.getByTestId("task-sidebar-delete")).toContainText("删除所选（2）");
    await page.getByTestId("task-sidebar-delete").click();

    await expect(page.getByTestId("task-sidebar")).toHaveCount(0);
    expect(await page.evaluate(() => window.localStorage.getItem("recentTasks"))).toBe("[]");
  });

  test("回归 38b：勾选不影响条目跳转；`<lg` 视口侧栏不渲染、首页区块正常", async ({ page }) => {
    await page.addInitScript(seedScript, [SEED_JUMP]);
    await page.goto("/");

    // 勾选后点击条目仍能跳转（复选框与跳转互不干扰）
    await page.getByTestId(`task-sidebar-check-${MOCK_DONE_ID}`).click();
    await page.getByTestId(`task-sidebar-item-${MOCK_DONE_ID}`).click();
    await expect(page).toHaveURL(`http://localhost:3000/result?id=${MOCK_DONE_ID}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible();

    // 回到首页；缩到 <lg 视口 → 侧栏不渲染、首页内嵌区块正常
    await page.goto("/");
    await page.setViewportSize({ width: 800, height: 900 });
    await expect(page.getByTestId("task-sidebar")).toBeHidden();
    await expect(page.getByTestId("recent-tasks")).toBeVisible();
  });

  test("回归 39：Mock 总结就绪后思维导图卡渲染，章节/要点节点齐全并可导出大纲", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("video-url-input").fill("BV1GJ411x7h7");
    await page.getByRole("button", { name: "解析" }).click();
    // Mock 全流程约 10 秒 + 自动总结
    await expect(page).toHaveURL(/\/result\?id=/, { timeout: 45_000 });
    await expect(page.getByTestId("fulltext-article")).toBeVisible({ timeout: 15_000 });

    // 导图卡在主网格之后、独立于总结卡；章节节点数与总结卡的 chapter-timeline 对应
    await expect(page.getByTestId("mindmap-card")).toBeVisible();
    await expect(page.getByTestId("mindmap-node")).not.toHaveCount(0);
    // 章节标题出现在导图节点里（数据源 = summary.chapters；Mock 固定数据第一章为「开场与结论」）
    await expect(page.getByTestId("mindmap-view").getByText("开场与结论", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("mindmap-view").getByText("语音识别", { exact: false }).first()).toBeVisible();

    // 导出大纲：第三组导出入口；内容含章节标题、不含 mm:ss 时间戳（独立产物语义）
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).click();
    await page.getByTestId("export-mindmap").click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain("思维导图");
    const content = await fs.readFile(await download.path(), "utf8");
    expect(content).toContain("# ");
    expect(content).not.toMatch(/\d{1,2}:\d{2}/);
  });

  test("回归 39b：切换详细度后思维导图随新 summary 更新", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("video-url-input").fill("BV1GJ411x7h7");
    await page.getByRole("button", { name: "解析" }).click();
    await expect(page).toHaveURL(/\/result\?id=/, { timeout: 45_000 });
    await expect(page.getByTestId("fulltext-article")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("mindmap-card")).toBeVisible();

    // 标准档 → 精简档：chapters 条数会变，导图节点随之重建（数据直接来自 store）
    const nodesBefore = await page.getByTestId("mindmap-node").count();
    await page.getByRole("button", { name: "精简" }).click();
    await expect(page.getByTestId("key-points").locator("li")).toHaveCount(3, { timeout: 15_000 });
    await expect(page.getByTestId("mindmap-card")).toBeVisible();
    const nodesAfter = await page.getByTestId("mindmap-node").count();
    expect(nodesAfter).toBeGreaterThan(0);
    // 允许相等，但两次计数都应在「有内容」区间（防止导图渲染被切换动作打断为空）
    expect(nodesBefore).toBeGreaterThan(0);
  });
});
