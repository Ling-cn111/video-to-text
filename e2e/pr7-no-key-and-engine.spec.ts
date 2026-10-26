import { expect, test } from "@playwright/test";

import { cloudUnconfiguredMessage, summarizeUnconfiguredMessage } from "../src/lib/capability-hint";

/**
 * 无站点 Key 场景的回归测试。
 *
 * 编号从「回归 18」起，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 这一组守的是「用户什么都没做错，界面不要给他看红卡」这条体验底线，
 * 以及「转写方式由后端按可用性决定，不要逼用户做实现选型」这条产品判断。
 */

const MOCK_TASK_ID = Buffer.from(
  JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 }),
).toString("base64url");

/** 把能力探测改成「站点什么都没配」。
 *
 *  用 `**\/capabilities` 而不是 `**\/api/capabilities`：真实模式下前端走的是
 *  `/backend-api/capabilities`（同源代理），只匹配 `/api/` 会让桩在真实构建里失效，
 *  测试反而打到真后端、断言随机通过或失败。 */
async function stubCapabilities(
  page: import("@playwright/test").Page,
  payload: Record<string, boolean>,
) {
  await page.route("**/capabilities", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        cloudAsrConfigured: false,
        summarizeConfigured: false,
        localAsrAvailable: false,
        ...payload,
      }),
    }),
  );
}

test.describe("无 Key 场景回归", () => {
  test("回归 18：没有可用 LLM Key 时不自动发起总结，也不报红卡", async ({ page }) => {
    await stubCapabilities(page, {});
    const summarizeCalls: string[] = [];
    page.on("request", (request) => {
      // 同时匹配 Mock（/api/summarize）与真实模式（/backend-api/summarize）：
      // 只匹配前者会让这条断言在真实构建里恒真，等于没测
      if (/\/summarize(\?|$)/.test(request.url())) summarizeCalls.push(request.url());
    });

    await page.goto(`/result?id=${MOCK_TASK_ID}`);

    // 文字稿照常可读
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
    // 不出现「总结生成失败」红卡：用户没做错什么，不该被当成错误
    await expect(page.getByTestId("summary-error")).toHaveCount(0);
    // 而是引导态
    await expect(page.getByTestId("summary-manual-prompt")).toBeVisible();
    // 一次都没发出去（发了必然 400/401，纯属浪费一次往返）
    expect(summarizeCalls).toHaveLength(0);
  });

  test("回归 18b：引导态给的是「填写自己的 Key」而不是灰掉的生成按钮", async ({ page }) => {
    await stubCapabilities(page, {});
    await page.goto(`/result?id=${MOCK_TASK_ID}`);

    const prompt = page.getByTestId("summary-manual-prompt");
    await expect(prompt).toBeVisible();
    // 主操作换成可点的「填写自己的 Key」；旧的「生成 AI 总结」不渲染（点了只会失败）
    await expect(page.getByTestId("summary-open-api-settings")).toBeVisible();
    await expect(page.getByTestId("manual-summarize-button")).toHaveCount(0);

    // 点它能把设置面板叫起来——这是这条引导唯一的意义，断了就等于死路
    await page.getByTestId("summary-open-api-settings").click();
    await expect(page.getByTestId("api-settings-dialog")).toBeVisible();
  });

  test("回归 18c：填了 Key 之后引导态变成可生成（不用刷新页面）", async ({ page }) => {
    await stubCapabilities(page, {});
    await page.goto(`/result?id=${MOCK_TASK_ID}`);
    await expect(page.getByTestId("summary-open-api-settings")).toBeVisible();

    // 在面板里填一把自己的 Key
    await page.getByTestId("summary-open-api-settings").click();
    await page.getByTestId("byok-llm-input").fill("sk-my-own-key-123456");
    await page.getByTestId("byok-llm-save").click();
    await page.getByTestId("byok-done").click();

    // 关闭面板后按钮立刻回到「生成 AI 总结」——靠 byok:changed 事件重算，
    // 若漏了这层通知，用户填完 Key 还得手动刷新才看到变化
    await expect(page.getByTestId("manual-summarize-button")).toBeVisible();
    await expect(page.getByTestId("summary-open-api-settings")).toHaveCount(0);
  });

  test("回归 19：没有本地 ASR 的部署不显示「本地 / 云端」选择器", async ({ page }) => {
    await stubCapabilities(page, { cloudAsrConfigured: true, localAsrAvailable: false });
    await page.goto("/");

    // 线上就是这种形态：本地引擎装不上，摆出这个选择器等于给一个必然失败的按钮
    await expect(page.getByTestId("transcribe-mode")).toHaveCount(0);
    await expect(page.getByTestId("mode-description")).toHaveCount(0);
  });

  test("回归 19b：有本地 ASR 的自建部署仍然显示选择器（默认本地 + 说明行）", async ({ page }) => {
    await stubCapabilities(page, { cloudAsrConfigured: true, localAsrAvailable: true });
    await page.goto("/");

    await expect(page.getByTestId("transcribe-mode")).toBeVisible();
    await expect(page.getByRole("button", { name: "本地", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByTestId("mode-description")).toContainText("本地模式");
  });

  test("回归 20：设置面板只收 AI 总结 Key，不再要求语音识别 Key", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("open-api-settings").click();

    // 云端 ASR 走站点的免费模型，多一个输入框只是多一道门槛
    await expect(page.getByTestId("byok-llm-input")).toBeVisible();
    await expect(page.getByTestId("byok-asr-input")).toHaveCount(0);

    // 但要说清楚这件事，否则用户会以为漏了功能
    await expect(page.getByTestId("api-settings-dialog")).toContainText("语音识别");
  });

  test("回归 21：面板如实说明 Key 会随请求发给后端，不再写「不上传服务器」", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("open-api-settings").click();

    const note = page.getByTestId("byok-storage-note");
    await expect(note).toBeVisible();
    // 必须承认 Key 会到服务器（部署者技术上能看到），否则就是误导
    await expect(note).toContainText("随请求发给本站后端");
    // 也要说清后端怎么处理它
    await expect(note).toContainText("不写入任务记录");
    await expect(note).not.toContainText("不会上传到服务器");
  });

  test("回归 21b：两条未配置提示的出路各自正确，不互相抄", async () => {
    // AI 总结：用户自带 Key，出路是「API 设置」
    expect(summarizeUnconfiguredMessage("example.com")).toContain("API 设置");
    // 云端转写：站点免费模型，界面不收集 ASR Key，不能指向「API 设置」
    expect(cloudUnconfiguredMessage("example.com")).not.toContain("API 设置");
    expect(cloudUnconfiguredMessage("example.com")).toContain("本地模式");
  });
});
