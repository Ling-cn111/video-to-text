import { expect, test } from "@playwright/test";

/**
 * PR17 的回归测试：BYOK 多套配置与测活按钮。
 *
 * 编号从回归 37 起，接在 pr16 的 36d 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 多套「地址 + 模型 + Key」配置可保存、可一键切换；生效配置随切换同步
 *    （三键镜像 = 请求实际会带的值），切换后旧总结缓存作废
 * 2. 「测试可用性」按钮对草稿发一次极短真实请求，成功/失败都要有明确反馈
 * 3. 换服务商时旧 Key 自动清空并提示，防止「新模型配旧 Key」的静默失败
 * 4. 旧单配置（三键无列表）自动迁移成一条配置，消费者无感
 */
test.describe("PR17 BYOK 多套配置与测活", () => {
  const KEY_A = "sk-deepseek-aaaa111122223333";
  const KEY_B = "sk-siliconflow-bbbb111122223333";

  async function openSettings(page: import("@playwright/test").Page) {
    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("api-settings-dialog")).toBeVisible();
  }

  async function saveCurrentForm(page: import("@playwright/test").Page) {
    await page.getByTestId("byok-llm-save").click();
    await expect(page.getByTestId("byok-config-list")).toBeVisible();
  }

  test("回归 37：保存两套配置并一键切换，生效配置（三键镜像）随切换同步", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());
    await openSettings(page);

    // 第一套：DeepSeek 预设
    await page.getByTestId("byok-preset-deepseek").click();
    await page.getByTestId("byok-llm-input").fill(KEY_A);
    await saveCurrentForm(page);
    await expect(page.getByTestId("byok-config-item")).toHaveCount(1);
    await expect(page.getByTestId("byok-config-active-badge")).toBeVisible();

    // 第二套：硅基流动预设（点「新增一套」重置表单）
    await page.getByTestId("byok-config-add").click();
    await page.getByTestId("byok-preset-siliconflow").click();
    await page.getByTestId("byok-llm-input").fill(KEY_B);
    await saveCurrentForm(page);
    await expect(page.getByTestId("byok-config-item")).toHaveCount(2);
    // 第二套保存即启用：三键镜像应指向硅基流动
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmBaseUrl"))).toBe(
      "https://api.siliconflow.cn/v1",
    );
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmKey"))).toBe(KEY_B);

    // 一键切回第一套：三键镜像同步变化
    const firstUse = page.getByTestId("byok-config-list").getByRole("button", { name: /切换到 deepseek-chat/ });
    await firstUse.click();
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmBaseUrl"))).toBe(
      "https://api.deepseek.com/v1",
    );
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmKey"))).toBe(KEY_A);
    // 列表徽章跟随：第一条是「使用中」
    const activeBadge = page.getByTestId("byok-config-active-badge");
    await expect(activeBadge).toBeVisible();
    await expect(page.getByTestId("byok-config-item").filter({ hasText: "deepseek-chat" })).toContainText("使用中");
  });

  test("回归 37b：切换配置后旧总结缓存作废（回归 25 语义延伸到切换）", async ({ page }) => {
    await page.addInitScript(() => {
      window.sessionStorage.setItem(
        "summary:task-1:standard",
        JSON.stringify({ summary: "旧配置的总结", keyPoints: [], chapters: [] }),
      );
    });
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());
    await openSettings(page);

    await page.getByTestId("byok-preset-deepseek").click();
    await page.getByTestId("byok-llm-input").fill(KEY_A);
    await saveCurrentForm(page);
    await page.getByTestId("byok-config-add").click();
    await page.getByTestId("byok-preset-siliconflow").click();
    await page.getByTestId("byok-llm-input").fill(KEY_B);
    await saveCurrentForm(page);

    // 缓存仍在（保存第二套时清过一次，这里重新放入验证「切换」这条路径）
    await page.evaluate(() => {
      window.sessionStorage.setItem(
        "summary:task-1:standard",
        JSON.stringify({ summary: "旧配置的总结", keyPoints: [], chapters: [] }),
      );
    });
    await page
      .getByTestId("byok-config-list")
      .getByRole("button", { name: /切换到 deepseek-chat/ })
      .click();

    const summaryKeys = await page.evaluate(() =>
      Object.keys(window.sessionStorage).filter((k) => k.startsWith("summary:")),
    );
    expect(summaryKeys).toEqual([]);
  });

  test("回归 37c：测试按钮——成功与失败都有明确反馈（Mock 注入）", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());
    await openSettings(page);

    await page.getByTestId("byok-preset-deepseek").click();
    await page.getByTestId("byok-llm-input").fill(KEY_A);
    await page.getByTestId("byok-test-button").click();
    await expect(page.getByTestId("byok-test-result")).toContainText("可用");
    await expect(page.getByTestId("byok-test-result")).toContainText("123");

    // 失败注入：模型名含「invalid」→ Mock 返回 BYOK_KEY_INVALID
    await page.getByTestId("byok-llm-model-input").fill("invalid-model");
    await page.getByTestId("byok-test-button").click();
    await expect(page.getByTestId("byok-test-result")).toContainText("被服务商拒绝");

    // 测试按钮缺 Key 时禁用
    await page.evaluate(() => window.localStorage.clear());
    await page.reload();
    await openSettings(page);
    await page.getByTestId("byok-preset-deepseek").click();
    await expect(page.getByTestId("byok-test-button")).toBeDisabled();
  });

  test("回归 37d：删除生效配置回到未配置态；删除非生效配置不影响使用中", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());
    await openSettings(page);

    await page.getByTestId("byok-preset-deepseek").click();
    await page.getByTestId("byok-llm-input").fill(KEY_A);
    await saveCurrentForm(page);
    await page.getByTestId("byok-config-add").click();
    await page.getByTestId("byok-preset-siliconflow").click();
    await page.getByTestId("byok-llm-input").fill(KEY_B);
    await saveCurrentForm(page);

    // 删除生效中的硅基流动套：三键镜像清空 → 回到未配置态，另一套仍在列表
    const sfRow = page.getByTestId("byok-config-item").filter({ hasText: "siliconflow.cn" });
    await sfRow.getByRole("button", { name: /删除/ }).click();
    await expect(page.getByTestId("byok-config-item")).toHaveCount(1);
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmKey"))).toBeNull();
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmBaseUrl"))).toBeNull();

    // 再删掉剩下的一套：列表整块消失，「清除全部」禁用
    await page
      .getByTestId("byok-config-item")
      .filter({ hasText: "deepseek-chat" })
      .getByRole("button", { name: /删除/ })
      .click();
    await expect(page.getByTestId("byok-config-list")).toHaveCount(0);
    await expect(page.getByTestId("byok-clear-all")).toBeDisabled();
  });

  test("回归 37e：旧单配置自动迁移为列表条目，三键值原样保留", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem("byokLlmKey", "sk-legacy-12345678");
      window.localStorage.setItem("byokLlmBaseUrl", "https://api.deepseek.com/v1");
      window.localStorage.setItem("byokLlmModel", "deepseek-chat");
    });
    await page.goto("/");
    await openSettings(page);

    // 列表出现一条且「使用中」；三键镜像原样保留（消费者无感）
    await expect(page.getByTestId("byok-config-item")).toHaveCount(1);
    await expect(page.getByTestId("byok-config-item")).toContainText("deepseek-chat");
    await expect(page.getByTestId("byok-config-active-badge")).toBeVisible();
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmKey"))).toBe("sk-legacy-12345678");
    expect(await page.evaluate(() => window.localStorage.getItem("byokConfigs"))).not.toBeNull();
  });

  test("回归 37f：模型名 combobox 提供预设家常用模型，自由输入仍可保存", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());
    await openSettings(page);

    await page.getByTestId("byok-preset-deepseek").click();
    // datalist 挂上了预设家的模型列表
    const listAttr = await page.getByTestId("byok-llm-model-input").getAttribute("list");
    expect(listAttr).toBe("byok-model-options");
    await expect(page.locator("#byok-model-options option")).toHaveCount(2);
    await expect(page.locator("#byok-model-options option").first()).toHaveAttribute("value", "deepseek-chat");

    // 自由输入不被覆盖
    await page.getByTestId("byok-llm-model-input").fill("my-custom-model");
    await page.getByTestId("byok-llm-input").fill(KEY_A);
    await saveCurrentForm(page);
    expect(await page.evaluate(() => window.localStorage.getItem("byokLlmModel"))).toBe("my-custom-model");
  });

  test("回归 37g：切换服务商时旧 Key 自动清空并提示；沿用旧 Key 保存时给出强提示", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.clear());
    await openSettings(page);

    // 先保存 DeepSeek 一套
    await page.getByTestId("byok-preset-deepseek").click();
    await page.getByTestId("byok-llm-input").fill(KEY_A);
    await saveCurrentForm(page);

    // 编辑它并切到硅基流动：草稿 Key 被清空 + 出现切换提示
    await page
      .getByTestId("byok-config-item")
      .filter({ hasText: "deepseek-chat" })
      .getByRole("button", { name: /编辑/ })
      .click();
    await page.getByTestId("byok-preset-siliconflow").click();
    await expect(page.getByTestId("byok-switch-hint")).toContainText("请填写该服务商的 API Key");
    await expect(page.getByTestId("byok-llm-input")).toHaveValue("");

    // 不填新 Key 直接保存：沿用旧 Key + 强提示（新模型配旧 Key 大概率失败）
    await page.getByTestId("byok-llm-save").click();
    await expect(page.getByText("注意：沿用了之前的 Key")).toBeVisible();
  });
});
