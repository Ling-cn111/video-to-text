import { expect, test } from "@playwright/test";

import {
  llmConfigIssue,
  validateBaseUrlFormat,
  validateModelFormat,
} from "../src/lib/byok";
import { normalizeVideoInput, resolveBareVideoId, resolveVideoUrl } from "../src/lib/platform";

/**
 * 自定义 LLM 服务商 + 裸视频号 回归测试。
 *
 * 编号从「回归 22」起，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 这一组守两件事：
 * 1. **「别家的 Key 用不了」不能复发**：只让用户填 Key 是不够的，请求必须真的
 *    打到用户填的地址、用用户填的模型。旧实现把地址与模型钉死在后端，用户填
 *    OpenAI 的 Key 会被拿去请求 DeepSeek，必然 401。
 * 2. **放行用户输入不能变成 SSRF 入口**：地址过 SSRF 校验这条必须在。
 */

const MOCK_TASK_ID = Buffer.from(
  JSON.stringify({ v: "BV1GJ411x7h7", p: "bilibili", s: Date.now() - 11_000 }),
).toString("base64url");

/** 填一套完整的自定义服务商配置，绕过界面直接写 localStorage（省掉 4 次点击） */
async function seedCustomProvider(
  page: import("@playwright/test").Page,
  config: { key?: string; baseUrl?: string; model?: string },
) {
  await page.goto("/");
  await page.evaluate((values) => {
    window.localStorage.clear();
    for (const [storageKey, value] of Object.entries(values)) {
      if (value) window.localStorage.setItem(storageKey, value);
    }
  }, {
    byokLlmKey: config.key ?? "",
    byokLlmBaseUrl: config.baseUrl ?? "",
    byokLlmModel: config.model ?? "",
  });
}

test.describe("自定义服务商回归", () => {
  test("回归 22：面板三项都能填能存，刷新后保留", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("api-settings-dialog")).toBeVisible();

    await page.getByTestId("byok-llm-base-url-input").fill("https://api.moonshot.cn/v1");
    await page.getByTestId("byok-llm-model-input").fill("moonshot-v1-8k");
    await page.getByTestId("byok-llm-input").fill("sk-moonshot-abcdefgh");
    await page.getByTestId("byok-llm-save").click();

    const stored = await page.evaluate(() => ({
      key: window.localStorage.getItem("byokLlmKey"),
      baseUrl: window.localStorage.getItem("byokLlmBaseUrl"),
      model: window.localStorage.getItem("byokLlmModel"),
    }));
    expect(stored.key).toBe("sk-moonshot-abcdefgh");
    expect(stored.baseUrl).toBe("https://api.moonshot.cn/v1");
    expect(stored.model).toBe("moonshot-v1-8k");

    // 刷新后三个值都还在（地址与模型不像 Key 那样掩码，直接显示在输入框里）
    await page.reload();
    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("byok-llm-base-url-input")).toHaveValue("https://api.moonshot.cn/v1");
    await expect(page.getByTestId("byok-llm-model-input")).toHaveValue("moonshot-v1-8k");
    await expect(page.getByTestId("byok-llm-masked")).toBeVisible();
  });

  test("回归 22b：点预设按钮把地址与模型填进输入框", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-preset-openai").click();

    await expect(page.getByTestId("byok-llm-base-url-input")).toHaveValue("https://api.openai.com/v1");
    await expect(page.getByTestId("byok-llm-model-input")).toHaveValue("gpt-4o-mini");

    // 预设只是填空助手：填完仍可改成任意值
    await page.getByTestId("byok-llm-base-url-input").fill("https://my-gateway.example.com/v1");
    await page.getByTestId("byok-llm-model-input").fill("my-model");
    await page.getByTestId("byok-llm-input").fill("sk-custom-12345678");
    await page.getByTestId("byok-llm-save").click();

    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmBaseUrl"))).toBe(
      "https://my-gateway.example.com/v1",
    );
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmModel"))).toBe("my-model");
  });

  test("回归 23：自定义地址与模型真的进请求体，Key 仍只在请求头", async ({ page }) => {
    await seedCustomProvider(page, {
      key: "sk-custom-header-check",
      baseUrl: "https://api.moonshot.cn/v1",
      model: "moonshot-v1-8k",
    });

    let captured: { headers: Record<string, string>; body: string } | null = null;
    await page.route("**/summarize", async (route) => {
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
    await expect(page.getByText("一句话概要")).toBeVisible();

    expect(captured).not.toBeNull();
    const headers = (captured as unknown as { headers: Record<string, string> }).headers;
    const body = (captured as unknown as { body: string }).body;

    // 地址与模型进请求体：这才是「请求真的打到用户填的服务商」的证据
    const parsed = JSON.parse(body) as Record<string, unknown>;
    expect(parsed.llmBaseUrl).toBe("https://api.moonshot.cn/v1");
    expect(parsed.llmModel).toBe("moonshot-v1-8k");
    // Key 仍只在请求头，请求体里不得出现
    expect(headers["x-vtt-llm-key"]).toBe("sk-custom-header-check");
    expect(body).not.toContain("sk-custom-header-check");
  });

  test("回归 23b：只填地址不填模型时保存被拦下", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-llm-base-url-input").fill("https://api.moonshot.cn/v1");
    await page.getByTestId("byok-llm-input").fill("sk-need-model-1234");
    await page.getByTestId("byok-llm-save").click();

    await expect(page.getByTestId("byok-llm-error")).toContainText("模型名");
    // 没写进去
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmBaseUrl"))).toBeNull();
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmKey"))).toBeNull();
  });

  test("回归 23c：纯函数断言 SSRF 与完整性规则", async () => {
    // 填了地址没填模型 → 给出人话提示（后端也会拒绝同一组合）
    expect(llmConfigIssue({ key: "k", baseUrl: "https://api.moonshot.cn/v1", model: null })).toContain(
      "模型名",
    );
    // 只填 Key 用站点预设 → 不算配置问题
    expect(llmConfigIssue({ key: "k", baseUrl: null, model: null })).toBeNull();
    // 地址末尾斜杠会被去掉：调用方统一拼 /chat/completions，双斜杠有的网关 404
    const trimmed = validateBaseUrlFormat("https://api.moonshot.cn/v1///");
    expect(trimmed.ok && trimmed.value).toBe("https://api.moonshot.cn/v1");
    // 非 http(s) 在格式层就拦下
    expect(validateBaseUrlFormat("file:///etc/passwd").ok).toBe(false);
    // 模型名只拦空白与超长，不猜命名规范
    expect(validateModelFormat("deepseek-ai/DeepSeek-V3").ok).toBe(true);
    expect(validateModelFormat("has space").ok).toBe(false);
  });

  test("回归 23d：只填地址不填 Key 也被拦（站点 Key 不得发往用户地址）", async () => {
    // 这是最严重的一处：Key 的回落目标是**站点自己的** Key。若允许「地址是我的、
    // Key 用站点的」，等于把站点凭据送到用户指定的服务器。`/api/summarize` 无鉴权，
    // 任何人手写请求就能触发。
    expect(
      llmConfigIssue({ key: null, baseUrl: "https://attacker.example.com/v1", model: "gpt-4o-mini" }),
    ).toContain("Key");
    // 三项齐全才算自洽
    expect(
      llmConfigIssue({
        key: "sk-1",
        baseUrl: "https://attacker.example.com/v1",
        model: "gpt-4o-mini",
      }),
    ).toBeNull();
  });

  test("回归 23e：界面只填地址不填 Key 时保存被拦下", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-llm-base-url-input").fill("https://api.moonshot.cn/v1");
    await page.getByTestId("byok-llm-model-input").fill("moonshot-v1-8k");
    await page.getByTestId("byok-llm-save").click();

    // 没填 Key 时 validateKeyFormat 先报「请填写 Key」，两条规则都指向同一件事：
    // 自定义地址必须配自己的 Key
    await expect(page.getByTestId("byok-llm-error")).toContainText("Key");
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmBaseUrl"))).toBeNull();
  });

  test("回归 25：换服务商后旧总结缓存被清掉，不会假装成功", async ({ page }) => {
    // 缓存键 `summary:<taskId>:<detail>` 不含服务商。若不清，用户换到新服务商后
    // 点「重试总结」会命中旧缓存直接返回 —— 页面显示一份总结，用户以为新配置通了，
    // 实际一次请求都没发。配置真错也会显示成功，比报错更难查。
    await page.goto("/");
    await page.evaluate(() => {
      window.localStorage.clear();
      window.sessionStorage.clear();
      window.sessionStorage.setItem(
        "summary:staleTask:standard",
        JSON.stringify({ summary: "旧服务商的结果", keyPoints: [] }),
      );
    });

    await page.getByTestId("open-api-settings").click();
    await page.getByTestId("byok-llm-input").fill("sk-brand-new-key-1234");
    await page.getByTestId("byok-llm-save").click();

    // 保存后缓存必须被清空
    const remaining = await page.evaluate(() =>
      Object.keys(window.sessionStorage).filter((key) => key.startsWith("summary:")),
    );
    expect(remaining).toEqual([]);
  });

  test("回归 24：裸 BV 号能直接解析并补成完整链接", async ({ page }) => {
    // **BV 号正文大小写敏感**：`BV1xx411c7mD` 与 `BV1XX411C7MD` 是两个不同的号。
    // 早先这里写 toUpperCase()，会把合法号悄悄改成另一个（不存在的）号，
    // 用户只会看到「视频不存在」。现在只统一前缀大小写，正文原样保留。
    expect(resolveBareVideoId("bv1xx411c7mD")).toEqual({
      platform: "bilibili",
      videoId: "BV1xx411c7mD",
    });
    expect(resolveBareVideoId("BV1xx411c7mD")).toEqual({
      platform: "bilibili",
      videoId: "BV1xx411c7mD",
    });
    // 正文大小写必须原样透传，不得被规范化
    expect(resolveBareVideoId("BV1Ab411c7mD")?.videoId).toBe("BV1Ab411c7mD");

    expect(normalizeVideoInput("  BV1xx411c7mD  ")).toBe("https://www.bilibili.com/video/BV1xx411c7mD");
    expect(normalizeVideoInput("av170001")).toBe("https://www.bilibili.com/video/av170001");

    // 完整链接原样返回，不被二次改写
    const fullUrl = "https://www.bilibili.com/video/BV1xx411c7mD?p=2";
    expect(normalizeVideoInput(fullUrl)).toBe(fullUrl);

    await page.goto("/");
    const input = page.getByTestId("video-url-input");
    await expect(input).toBeVisible();
    // placeholder 要说明现在也接受裸号，否则没人会去试
    await expect(input).toHaveAttribute("placeholder", /BV 号/);

    await input.fill("BV1xx411c7mD");
    await page.getByRole("button", { name: "解析" }).click();
    // 真的进了进度页，说明裸号没有被当成「链接格式不正确」
    await page.waitForURL(/\/processing\?id=/);
  });

  test("回归 24b：纯数字不被误认成 av 号", async () => {
    // 抖音那种纯数字号太容易与 av 号混淆，硬猜只会把用户送到错误的分支
    expect(resolveBareVideoId("7301234567890123456")).toBeNull();
    expect(resolveBareVideoId("av170001")).toEqual({ platform: "bilibili", videoId: "av170001" });
    // 认不出的输入原样返回，交给 URL 解析去报「格式不正确」
    expect(normalizeVideoInput("7301234567890123456")).toBe("7301234567890123456");
    expect(resolveVideoUrl("7301234567890123456")).toEqual({ ok: false, reason: "invalid" });
  });

  test("回归 24c：av 号走完整链接后仍被识别为 B站", async () => {
    const result = resolveVideoUrl("https://www.bilibili.com/video/av170001");
    expect(result).toEqual({ ok: true, platform: "bilibili", videoId: "av170001" });
  });
});
