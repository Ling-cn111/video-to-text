import { expect, test } from "@playwright/test";

import {
  estimateStageRemaining,
  formatRemaining,
  clampMonotonic,
  STAGE_END,
  MIN_WINDOW_MS,
} from "../src/hooks/use-remaining-time";
import { FAST_MS, SLOW_MS, FAST_WINDOW_MS, pollIntervalFor, msUntilSlow } from "../src/lib/poll-rhythm";
import { formatRelativeTime } from "../src/lib/recent-tasks";

/**
 * P1-7 / P1-8 的回归测试（PR3）。
 *
 * 编号沿用「回归 N」，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 覆盖三件事：
 * 4. 最近转写：首页能找回上次的任务（此前 taskId 只活在 URL 里，关页即失联）
 * 5. 进度页给了离开出口，且说清「任务不会停」
 * 6. 剩余时间 / 轮询节奏的时间逻辑（纯函数，无需真实等待）
 */

test.describe("P1-7 最近转写", () => {
  test("回归 4：首页展示上次转写记录并可点进结果页", async ({ page }) => {
    const taskId = Buffer.from(
      JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 }),
    ).toString("base64url");

    // 预置一条已完成记录：模拟「上次转写完关掉页面，今天再打开」
    await page.addInitScript((id) => {
      localStorage.setItem(
        "recentTasks",
        JSON.stringify([
          {
            taskId: id,
            title: "【深度实测】2026 年语音转文字工具横评",
            cover: "/covers/bilibili-1.svg",
            duration: 754,
            platform: "bilibili",
            savedAt: Date.now() - 3_600_000,
            status: "done",
          },
        ]),
      );
    }, taskId);

    await page.goto("/");

    const section = page.getByTestId("recent-tasks");
    await expect(section).toBeVisible();
    await expect(section).toContainText("最近转写");
    // 相对时间：1 小时前的那条不该显示成「刚刚」
    await expect(section).toContainText("1 小时前");

    const item = page.getByTestId("recent-task-item").first();
    await expect(item).toContainText("语音转文字工具横评");
    await expect(item).toContainText("查看结果");

    // 点进去必须能真正恢复到结果页（taskId 有效）
    await item.click();
    await expect(page).toHaveURL(new RegExp(`/result\\?id=${taskId}`));
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
  });

  test("回归 4b：没有记录时首页不渲染该区块（保持单一入口）", async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem("recentTasks"));
    await page.goto("/");
    await expect(page.getByTestId("recent-tasks")).toHaveCount(0);
  });

  test("回归 4c：处理中的记录点进去是进度页而不是结果页", async ({ page }) => {
    const taskId = Buffer.from(
      JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() }),
    ).toString("base64url");

    await page.addInitScript((id) => {
      localStorage.setItem(
        "recentTasks",
        JSON.stringify([
          {
            taskId: id,
            title: "还在转写的任务",
            cover: "/covers/bilibili-1.svg",
            duration: 754,
            platform: "bilibili",
            savedAt: Date.now(),
            status: "processing",
          },
        ]),
      );
    }, taskId);

    await page.goto("/");
    const item = page.getByTestId("recent-task-item").first();
    await expect(item).toContainText("查看进度");
    await item.click();
    await expect(page).toHaveURL(new RegExp(`/processing\\?id=${taskId}`));
  });

  test("回归 4d：脏数据不会把首页渲染崩掉", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("recentTasks", JSON.stringify([{ taskId: 1 }, null, "oops"]));
    });
    await page.goto("/");
    // 形状不合法的条目被过滤掉 → 区块整体不渲染，而不是白屏
    await expect(page.getByRole("heading", { name: /视频链接/ })).toBeVisible();
    await expect(page.getByTestId("recent-tasks")).toHaveCount(0);
  });
});

test.describe("P1-8 进度页离开出口", () => {
  test("回归 5：转写中提供「不等了」出口并说明任务不会停", async ({ page }) => {
    // startedAt 设为现在：Mock 会停在 processing，页面长期处于 transcribing
    const taskId = Buffer.from(
      JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() }),
    ).toString("base64url");

    await page.goto(`/processing?id=${taskId}`);

    const stop = page.getByTestId("stop-waiting");
    await expect(stop).toBeVisible();
    await expect(stop).toContainText("不等了");

    // 必须说清「走了也还在跑」，否则用户会以为离开就白等了
    await expect(page.getByText(/转写会继续跑/)).toBeVisible();
    await expect(page.getByText(/最近转写/)).toBeVisible();

    await stop.click();
    await expect(page).toHaveURL("/");
  });
});

test.describe("P1-8 时间逻辑（纯函数）", () => {
  test("回归 6：剩余时间只估当前阶段，样本不足时不瞎猜", () => {
    const now = 1_000_000;
    // 观察窗口不足：返回 null（宁可不说，也不给一个会卡住的假数字）
    expect(
      estimateStageRemaining({ stage: "asr", startProgress: 40, startAt: now - 1_000 }, 45, now),
    ).toBeNull();

    // 进度没动：同样不猜
    expect(
      estimateStageRemaining({ stage: "asr", startProgress: 40, startAt: now - 10_000 }, 40, now),
    ).toBeNull();

    // asr 阶段：10 秒涨 10 个百分点 → 每点 1 秒；距阶段终点 95 还有 45 点 → 45 秒
    expect(
      estimateStageRemaining({ stage: "asr", startProgress: 40, startAt: now - 10_000 }, 50, now),
    ).toBe(45);

    // 用阶段终点而不是 100%：extract_audio 到 35 就结束了，不能按 100 算
    expect(STAGE_END.extract_audio).toBe(35);
    expect(
      estimateStageRemaining(
        { stage: "extract_audio", startProgress: 15, startAt: now - 10_000 },
        25,
        now,
      ),
    ).toBe(10);
  });

  test("回归 6b：估计值只降不升，文案分档不跳变", () => {
    expect(clampMonotonic(null, 42)).toBe(42);
    expect(clampMonotonic(42, 30)).toBe(30);
    // 回弹被压住：这是「只降不升」的核心断言
    expect(clampMonotonic(30, 42)).toBe(30);

    expect(formatRemaining(null)).toBeNull();
    expect(formatRemaining(3)).toBe("即将完成");
    expect(formatRemaining(45)).toContain("45 秒");
    expect(formatRemaining(120)).toContain("2 分钟");
  });

  test("回归 6c：轮询按阶段时长退避，阶段一变立刻回到快档", () => {
    expect(pollIntervalFor(0)).toBe(FAST_MS);
    expect(pollIntervalFor(FAST_WINDOW_MS - 1)).toBe(FAST_MS);
    expect(pollIntervalFor(FAST_WINDOW_MS)).toBe(SLOW_MS);
    expect(pollIntervalFor(60_000)).toBe(SLOW_MS);

    expect(msUntilSlow(0)).toBe(FAST_WINDOW_MS);
    expect(msUntilSlow(FAST_WINDOW_MS + 5_000)).toBe(0);

    // 慢档必须明显比旧实现的 800ms 稀疏，否则这次改动没有意义
    expect(SLOW_MS / FAST_MS).toBeGreaterThanOrEqual(3);
  });

  test("回归 6d：相对时间四档", () => {
    const now = Date.now();
    expect(formatRelativeTime(now - 10_000, now)).toBe("刚刚");
    expect(formatRelativeTime(now - 5 * 60_000, now)).toBe("5 分钟前");
    expect(formatRelativeTime(now - 3 * 3_600_000, now)).toBe("3 小时前");
    expect(formatRelativeTime(now - 2 * 86_400_000, now)).toBe("2 天前");
  });
});

test.describe("P1-8 采样窗口常量", () => {
  test("回归 6e：采样窗口不短于 3 秒（否则网络抖动会被放大成荒谬估计）", () => {
    expect(MIN_WINDOW_MS).toBeGreaterThanOrEqual(3_000);
  });
});
