import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  formatHotwords,
  MAX_HOTWORD_CHARS,
  MAX_HOTWORDS,
  MAX_HOTWORDS_TOTAL_CHARS,
  parseHotwords,
} from "../src/lib/hotwords";

/**
 * PR14 的回归测试：热词输入 + APK 自动发布。
 *
 * 编号从回归 34 起，接在 pr13 的 33e 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 热词解析必须容忍用户随手敲的分隔符，并且**不能把整串塞进提示词**
 *    （后端 initial_prompt 有 200 字预算，超了会被静默截断，标题也一起没了）
 * 2. 热词必须真的进到 POST /api/transcribe 的请求体。只在 UI 上有个输入框、
 *    提交时丢掉字段，是最难发现的那类 bug —— Mock 模式下看不出，真实模式下也不报错，
 *    只是「识别质量没变化」
 * 3. 热词是用户偏好，刷新后要还在（存在 localStorage，键 hotwords）
 * 4. APK 必须由 CI 产出，而不是每次发版手工传
 */
test.describe("PR14 热词与 APK 自动发布", () => {
  test("回归 34：热词解析容忍各种分隔符并去重", () => {
    // 中文逗号 / 顿号 / 英文逗号 / 空格 / 换行都当分隔
    expect(parseHotwords("Transformer，字节跳动、LangChain, 微信\n飞书")).toEqual([
      "Transformer",
      "字节跳动",
      "LangChain",
      "微信",
      "飞书",
    ]);

    // 去重按忽略大小写的完全相等判断，但单复数算两个词（可能是不同产品名）
    expect(parseHotwords("Transformer transformer Transformers")).toEqual(["Transformer", "Transformers"]);

    // 空 / 全是分隔符 / null 一律给空数组，不能给 [''] 让后端拼出空串
    expect(parseHotwords("")).toEqual([]);
    expect(parseHotwords(" ，、, ")).toEqual([]);
    expect(parseHotwords(null)).toEqual([]);
    expect(parseHotwords(undefined)).toEqual([]);
  });

  test("回归 34b：热词有长度预算，不会把提示词撑爆", () => {
    // 单条超长会被截断：超过 20 字的基本是一句话，不是术语
    const long = "a".repeat(40);
    expect(parseHotwords(long)).toEqual(["a".repeat(MAX_HOTWORD_CHARS)]);

    // 条数上限：再多后端提示词会截断，实际起作用的只有靠前的
    const many = Array.from({ length: 30 }, (_, i) => `word${i}`).join("、");
    expect(parseHotwords(many)).toHaveLength(MAX_HOTWORDS);

    // 总量上限：100 字是给标题留出 200 字预算里的那 100
    const huge = Array.from({ length: 60 }, () => "一二三四五").join("、");
    const parsed = parseHotwords(huge);
    const total = parsed.reduce((sum, w) => sum + w.length + 1, 0);
    expect(total).toBeLessThanOrEqual(MAX_HOTWORDS_TOTAL_CHARS);

    // 反向格式化能拼回可读的一串（目前只在提示语里用）
    expect(formatHotwords(["A", "B"])).toBe("A、B");
    expect(formatHotwords([])).toBe("");
    expect(formatHotwords(null)).toBe("");
  });

  test("回归 34c：热词真的进了 POST /api/transcribe 的请求体", async ({ page }) => {
    // 这是这一组最重要的断言。历史教训：字段在 UI 上做了、在 store 里存了，
    // 但 api.ts 的 body 里没写，结果整条链路静默失效。
    await page.goto("/");

    await page.getByTestId("hotwords-toggle").click();
    await expect(page.getByTestId("hotwords-input")).toBeVisible();
    await page.getByTestId("hotwords-input").fill("Transformer、字节跳动");

    // 填完立刻能读回解析结果（提示语里会列出来）
    await expect(page.getByTestId("hotwords-hint")).toContainText("Transformer");
    await expect(page.getByTestId("hotwords-hint")).toContainText("字节跳动");

    const requestPromise = page.waitForRequest(
      (req) => req.url().includes("/api/transcribe") && req.method() === "POST",
    );
    await page.getByTestId("video-url-input").fill("BV1xx411c7mD");
    await page.getByRole("button", { name: "解析" }).click();

    const body = JSON.parse((await requestPromise).postData() ?? "{}");
    expect(body.hotwords).toEqual(["Transformer", "字节跳动"]);
  });

  test("回归 34d：没填热词时不会带上非空热词", async ({ page }) => {
    // 两种实现都可以：Mock 客户端总是发 `hotwords: []`，真实客户端（real-api.ts）
    // 干脆省略这个字段（后端 schema 是可选字段，省略等价于没填）。
    // 这里只守住「没有真正填词时，不会凭空出现一个非空数组」。
    await page.goto("/");
    const requestPromise = page.waitForRequest(
      (req) => req.url().includes("/api/transcribe") && req.method() === "POST",
    );
    await page.getByTestId("video-url-input").fill("BV1xx411c7mD");
    await page.getByRole("button", { name: "解析" }).click();

    const body = JSON.parse((await requestPromise).postData() ?? "{}");
    expect(body.hotwords ?? []).toEqual([]);
  });

  test("回归 34e：热词刷新后还在，且默认保持展开", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("hotwords-toggle").click();
    await page.getByTestId("hotwords-input").fill("微信、飞书");

    // 首页刷新后偏好必须恢复（与 engine / autoSummary / summaryDetail 同一套水合路径）
    await page.reload();
    await expect(page.getByTestId("hotwords-input")).toBeVisible();
    await expect(page.getByTestId("hotwords-input")).toHaveValue("微信、飞书");
    await expect(page.getByTestId("hotwords-hint")).toContainText("微信");

    // 存的是**原始文本**，分隔符不被规范化 —— 否则用户回来想继续编辑时
    // 发现自己敲的逗号都变成了顿号，会以为输入被吞了
    const stored = await page.evaluate(() => window.localStorage.getItem("hotwords"));
    expect(stored).toBe("微信、飞书");
  });

  test("回归 34f：release 工作流必须包含 Android job", () => {
    // v0.1.4 ~ v0.1.6 的 APK 都是手工 `gh release upload` 的，Release 页面因此
    // 常常出现「桌面端已是新版本、手机端还是旧的」。
    // 这条测试守的就是「发版流程里真的有 APK 这一步」。
    const root = process.cwd();
    const workflow = readFileSync(join(root, ".github/workflows/release-desktop.yml"), "utf8");

    // 必须有一个独立的 android job，且依赖桌面端先建好 Release
    expect(workflow).toContain("android:");
    expect(workflow).toMatch(/needs:\s*build/);

    // 构建链路的关键三步，少一步就产不出可安装的包
    expect(workflow).toContain("pnpm build:mobile");
    expect(workflow).toContain("cap sync android");
    expect(workflow).toContain("assembleRelease");

    // 签名：没有 keystore 时 build.gradle 会跳过 signingConfigs，
    // 产出的 APK 装不上、也覆盖不了已安装版本。必须从 Secrets 还原并显式失败
    expect(workflow).toContain("ANDROID_KEYSTORE_BASE64");
    expect(workflow).toContain("ANDROID_KEYSTORE_PASSWORD");
    expect(workflow).toContain("ANDROID_KEY_ALIAS");
    expect(workflow).toContain("ANDROID_KEY_PASSWORD");

    // 上传必须 --clobber：重跑流水线时资产已存在，不加会直接失败
    expect(workflow).toContain("gh release upload");
    expect(workflow).toContain("--clobber");
  });
});
