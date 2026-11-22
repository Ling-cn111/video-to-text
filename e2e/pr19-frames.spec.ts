import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * PR19 的回归测试：重点帧（URL 任务，手动触发的二次抽帧）。
 *
 * 编号从回归 40 起，接在 pr18 的 39b 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 触发条件：capabilities.framesConfigured + 非 local 平台 + 总结就绪才渲染入口；
 *    任一不满足整卡隐藏（40b / 40d）
 * 2. 生成流：点击「生成重点帧」→ 请求体带总结派生的时间点 → ready 网格
 *    （章节/要点合并去重后的数量、mm:ss 标签）（回归 40）
 * 3. processing 态可见（骨架屏）且轮询任务详情后翻转为网格（40e，路由拦截模拟
 *    真实后端的 processing → ready；Mock 本身无状态，ready 随响应立即返回）
 * 4. 失败态：注入失败标记（编造 taskId 带 fr:1）→ 错误信息 + 重试按钮（40c）
 *
 * Mock 约定见 src/lib/mock-api.ts 的 requestFrames（fr:1 → frames failed）。
 */
test.describe("PR19 重点帧", () => {
  test.beforeEach(async ({ page }) => {
    // addInitScript 在每次导航的应用代码前执行：跨用例清干净 localStorage
    // （pr17 的先例是 goto 后 clear，这里用 addInitScript 等价且更早）
    await page.addInitScript(() => window.localStorage.clear());
  });

  /** 走完整 Mock 流程进结果页（自动总结开启，10 秒左右） */
  const openResultWithSummary = async (page: Page) => {
    await page.goto("/");
    await page.getByTestId("video-url-input").fill("BV1GJ411x7h7");
    await page.getByRole("button", { name: "解析" }).click();
    await expect(page).toHaveURL(/\/result\?id=/, { timeout: 45_000 });
    await expect(page.getByTestId("fulltext-article")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("key-points")).toBeVisible({ timeout: 15_000 });
  };

  const framesCard = (page: Page) => page.getByTestId("frames-card");

  test("回归 40：生成重点帧 → 网格按章节/要点合并数量渲染，时间标签 mm:ss", async ({ page }) => {
    await openResultWithSummary(page);
    await expect(framesCard(page)).toBeVisible();

    // 捕获请求体：时间点必须真的来自总结派生（章节起点 + 要点，经 2s 合并）
    const framesRequest = page.waitForRequest(
      (request) => request.url().includes("/api/frames") && request.method() === "POST",
    );
    await page.getByTestId("frames-generate").click();
    const request = await framesRequest;
    const body = request.postDataJSON() as { taskId: string; timestamps: number[] };
    // Mock 数据：chapters [0,92,196,380,560] ∪ keyPoints [0,92,380,560,636] → 合并 [0,92,196,380,560,636]
    expect(body.timestamps).toEqual([0, 92, 196, 380, 560, 636]);
    expect(body.taskId).toBeTruthy();

    // Mock 无状态：ready 随响应立即返回 → 网格直出
    await expect(page.getByTestId("frames-grid")).toBeVisible();
    await expect(page.getByTestId("frames-item")).toHaveCount(6);
    // mm:ss 时间标签（92s → 01:32；636s → 10:36）
    await expect(page.getByTestId("frames-time").filter({ hasText: "01:32" })).toHaveCount(1);
    await expect(page.getByTestId("frames-time").filter({ hasText: "10:36" })).toHaveCount(1);
    // 帧图 <img> 实际加载（Mock 用本地占位 SVG）
    const imagesLoaded = await page.getByTestId("frames-item").evaluateAll((items) =>
      items.every((item) => {
        const img = item.querySelector("img");
        return img !== null && img.complete && img.naturalWidth > 0;
      }),
    );
    expect(imagesLoaded).toBe(true);
  });

  test("回归 40b：无总结不渲染重点帧卡（关闭自动总结走手动生成态）", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem("autoSummary", "false");
    });
    await page.goto("/");
    await page.getByTestId("video-url-input").fill("BV1GJ411x7h7");
    await page.getByRole("button", { name: "解析" }).click();
    await expect(page).toHaveURL(/\/result\?id=/, { timeout: 45_000 });
    await expect(page.getByTestId("fulltext-article")).toBeVisible({ timeout: 15_000 });

    // 总结未生成：时间戳无处派生，整卡隐藏
    await expect(framesCard(page)).toHaveCount(0);
  });

  test("回归 40c：生成失败（注入标记）→ 错误信息与重试入口", async ({ page }) => {
    // 编造带 fr:1 的 taskId：Mock 的 requestFrames 直接返回 frames failed
    const failTaskId = Buffer.from(
      JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 60_000, fr: 1 }),
    ).toString("base64url");
    await page.goto(`/result?id=${failTaskId}`);
    await expect(page.getByTestId("fulltext-article")).toBeVisible({ timeout: 15_000 });
    await expect(framesCard(page)).toBeVisible();

    await page.getByTestId("frames-generate").click();
    await expect(page.getByTestId("frames-failed")).toBeVisible();
    await expect(page.getByTestId("frames-failed")).toContainText("视频流下载失败");
    await expect(page.getByTestId("frames-retry")).toBeVisible();

    // 重试仍走同一入口（payload 不变，依旧是失败态——入口保持可用即可）
    await page.getByTestId("frames-retry").click();
    await expect(page.getByTestId("frames-failed")).toBeVisible();
  });

  test("回归 40d：capabilities 缺 framesConfigured → 整卡隐藏", async ({ page }) => {
    // 拦截能力探测，返回不带 framesConfigured 的旧后端形状（可选字段缺省按 false）
    await page.route("**/api/capabilities", (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          cloudAsrConfigured: true,
          summarizeConfigured: true,
          localAsrAvailable: true,
          fileUploadConfigured: true,
        }),
      }),
    );
    await openResultWithSummary(page);

    await expect(framesCard(page)).toHaveCount(0);
  });

  test("回归 40e：processing 骨架屏可见，轮询到 ready 后翻转为网格", async ({ page }) => {
    await openResultWithSummary(page);

    // 拦截抽帧请求返回 processing（真实后端的受理形状；Mock 直出 ready 覆盖不到这段）
    await page.route("**/api/frames", (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          taskId: "intercepted",
          frames: { status: "processing", error: null, items: [] },
        }),
      }),
    );
    // 抽帧轮询打任务详情：第一次起恒返回 ready（延迟 300ms 让骨架屏可靠可见）
    await page.route("**/api/transcribe/*", async (route: Route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          taskId: "intercepted",
          status: "completed",
          stage: "asr",
          progress: 100,
          frames: {
            status: "ready",
            error: null,
            items: [
              { time: 0, objectKey: "frames/x/00.jpg", url: "/covers/bilibili-1.svg" },
              { time: 92, objectKey: "frames/x/01.jpg", url: "/covers/bilibili-1.svg" },
            ],
          },
        }),
      });
    });

    await page.getByTestId("frames-generate").click();
    await expect(page.getByTestId("frames-processing")).toBeVisible();
    await expect(page.getByTestId("frames-grid")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("frames-item")).toHaveCount(2);
    // 处理完成后骨架屏退场
    await expect(page.getByTestId("frames-processing")).toHaveCount(0);
  });
});
