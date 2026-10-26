import { expect, test } from "@playwright/test";

import { cloudUnconfiguredMessage, summarizeUnconfiguredMessage } from "../src/lib/capability-hint";
import { errorTitle } from "../src/lib/error-titles";

/**
 * 用户自带 Key（BYOK）回归测试。
 *
 * 编号从「回归 14」起，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 这一组断言的是**用户的信任边界**，比功能本身更容易在重构中被无声破坏：
 * - Key 只存在本地，不上传、不进请求体
 * - 界面上默认遮住，不铺明文
 * - 站点未配 Key 时，用户填了自己的就能用（否则 BYOK 等于没做）
 * - 用户的 Key 被拒时，错误必须指向「去改自己的」，而不是「去找部署者」
 */

const MOCK_TASK_ID = Buffer.from(
  JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 }),
).toString("base64url");

test.describe("BYOK 回归", () => {
  test("回归 14：设置面板可从顶栏打开，保存后以掩码显示", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("api-settings-dialog")).toBeVisible();

    await page.getByTestId("byok-llm-input").fill("sk-test-abcdefghijklmn");
    await page.getByTestId("byok-llm-save").click();

    // 保存后立刻变成掩码，明文不再出现在页面上
    await expect(page.getByTestId("byok-llm-masked")).toBeVisible();
    await expect(page.getByTestId("byok-llm-masked")).toContainText("••••klmn");
    await expect(page.getByTestId("byok-llm-input")).toHaveCount(0);

    const body = await page.locator("body").innerText();
    expect(body).not.toContain("sk-test-abcdefghijklmn");
  });

  test("回归 14b：Key 存在 localStorage，且刷新后仍是掩码态", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-llm-input").fill("sk-persist-12345678");
    await page.getByTestId("byok-llm-save").click();

    const stored = await page.evaluate(() => window.localStorage.getItem("byokLlmKey"));
    expect(stored).toBe("sk-persist-12345678");

    await page.reload();
    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("byok-llm-masked")).toBeVisible();
  });

  test("回归 14c：清除后回到「使用站点额度」", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => {
      window.localStorage.clear();
      window.localStorage.setItem("byokLlmKey", "sk-to-be-cleared");
    });

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-llm-clear").click();

    await expect(page.getByTestId("byok-llm-input")).toBeVisible();
    const stored = await page.evaluate(() => window.localStorage.getItem("byokLlmKey"));
    expect(stored).toBeNull();
  });

  test("回归 14d：格式校验拦下手误（含空格）", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-llm-input").fill("sk-has space inside");
    await page.getByTestId("byok-llm-save").click();

    await expect(page.getByTestId("byok-llm-error")).toContainText("空格");
    // 没保存成功
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmKey"))).toBeNull();
  });

  test("回归 15：总结请求带上用户 Key 的请求头，且 Key 不进请求体", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => {
      window.localStorage.clear();
      window.localStorage.setItem("byokLlmKey", "sk-header-check");
    });

    let captured: { headers: Record<string, string>; body: string } | null = null;
    await page.route("**/api/summarize", async (route) => {
      captured = {
        headers: route.request().headers(),
        body: route.request().postData() ?? "",
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          summary: "概要",
          keyPoints: [{ time: 0, text: "要点" }],
          chapters: [],
        }),
      });
    });

    await page.goto(`/result?id=${MOCK_TASK_ID}`);
    await expect(page.getByTestId("key-points").or(page.getByTestId("summary-error"))).toBeVisible();

    expect(captured).not.toBeNull();
    const headers = (captured as unknown as { headers: Record<string, string> }).headers;
    const body = (captured as unknown as { body: string }).body;
    // 请求头带上（头名大小写不敏感，Playwright 会归一成小写）
    expect(headers["x-vtt-llm-key"]).toBe("sk-header-check");
    // 请求体里绝不能出现 Key
    expect(body).not.toContain("sk-header-check");
  });

  test("回归 15b：没填 Key 时不发该请求头（不能发空串）", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    let headers: Record<string, string> | null = null;
    await page.route("**/api/summarize", async (route) => {
      headers = route.request().headers();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ summary: "概要", keyPoints: [], chapters: [] }),
      });
    });

    await page.goto(`/result?id=${MOCK_TASK_ID}`);
    // 用「一句话概要」这个标题等总结区渲染完成：keyPoints 为空时 <ol> 高度为 0，
    // toBeVisible 会判成 hidden，拿它当锚点会假红
    await expect(page.getByText("一句话概要")).toBeVisible();

    expect(headers).not.toBeNull();
    expect((headers as unknown as Record<string, string>)["x-vtt-llm-key"]).toBeUndefined();
  });

  test("回归 16：用户 Key 被拒时报「API Key 无效」，而不是「未配置」", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => {
      window.localStorage.clear();
      window.localStorage.setItem("byokLlmKey", "sk-bad-key");
    });

    await page.route("**/api/summarize", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "BYOK_KEY_INVALID",
            message: "你填写的 API Key 被服务商拒绝（HTTP 401），请在右上角「API 设置」中检查或更换",
          },
        }),
      }),
    );

    await page.goto(`/result?id=${MOCK_TASK_ID}`);

    const errorCard = page.getByTestId("summary-error");
    await expect(errorCard).toBeVisible();
    await expect(errorCard).toContainText("API 设置");
    // 文字稿不受影响
    await expect(page.getByTestId("fulltext-article")).toBeVisible();
  });

  test("回归 16b：错误码表里 BYOK_KEY_INVALID 有专属标题，不落到「出了点问题」", async () => {
    // 纯函数断言（首页错误走 toast，不渲染 ErrorAlert，所以直接查码表）
    expect(errorTitle("BYOK_KEY_INVALID")).toBe("API Key 无效");
    // 相邻的「未配置」类错误不能被它覆盖：两者的动作主体不同
    expect(errorTitle("SUMMARIZE_NOT_CONFIGURED")).toBe("未配置 AI 总结");
    expect(errorTitle("CLOUD_NOT_CONFIGURED")).toBe("未配置云端 ASR");
  });

  test("回归 17：未配置提示指向用户自己能走的出路", async ({ page }) => {
    // AI 总结是用户自带 Key 的场景：两条分支都必须提到右上角「API 设置」
    expect(summarizeUnconfiguredMessage("example.com")).toContain("API 设置");
    expect(summarizeUnconfiguredMessage("localhost")).toContain("API 设置");
    // 公网分支不得暴露环境变量名（那是给部署者看的）
    expect(summarizeUnconfiguredMessage("example.com")).not.toContain("DEEPSEEK_API_KEY");
    // 本地分支仍要保留可执行的配置指引
    expect(summarizeUnconfiguredMessage("localhost")).toContain("DEEPSEEK_API_KEY");

    // 云端转写走的是站点的免费模型，界面不收集用户的 ASR Key：
    // 提示里**不能**再让他去填 Key（那是一条走不通的路），只能建议换转写方式
    expect(cloudUnconfiguredMessage("example.com")).not.toContain("API 设置");
    expect(cloudUnconfiguredMessage("example.com")).toContain("本地模式");
    expect(cloudUnconfiguredMessage("example.com")).not.toContain("CLOUD_ASR_API_KEY");
    // 本地部署仍给配置指引
    expect(cloudUnconfiguredMessage("localhost")).toContain("CLOUD_ASR_API_KEY");

    await page.goto("/");
  });
});
