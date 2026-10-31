import { expect, test } from "@playwright/test";

import { extractAllUrls, extractFirstUrl, extractVideoUrl, resolveVideoUrl } from "../src/lib/platform";
import { sharedTextToVideoInput } from "../src/lib/share-intent";
import { RELEASES_PAGE } from "../src/lib/updater";

/**
 * PR13 的回归测试（v0.1.6 真机反馈的四个问题）。
 *
 * 编号从回归 33 起，接在 pr12 的 32f 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 四个问题（用户原话）：
 * 1. 「0.16版本打开发布页还是0.15版本的」→ INSTALLER 指向 /releases/latest
 * 2. 「api设置只有deepseek取key，选择其他服务商的时候不能对应」→ 取 Key 只看已保存配置
 * 3. 「手机端没看到有设置选项」→ 顶栏入口只有图标没有文字（见 verify-shell.mjs）
 * 4. 「手机端在b站分享打开显示视频不存在或链接有误」→ 分享文案多链接 + 短链未展开
 *
 * 这一组想守住的不变量：
 * - 发布页不能再依赖 GitHub 的 `latest` 302（它跟的是最后一个 tag，不是当前版本）
 * - 服务商切换后「取 Key」链接必须立刻跟着草稿走，不能等保存
 * - 分享文案里有多条链接时优先挑平台认得的那条，不能固定取第一条
 */

test.describe("PR13 真机反馈修复", () => {
  test("回归 33：发布页指列表页，不再依赖 latest 302", () => {
    // `https://github.com/<repo>/releases/latest` 会 302 到**最后一个非
    // prerelease 的 tag**。新版本装上去但 Release 还没建的时候，用户点开
    // 看到的是上一个版本，观感就是「这个按钮没生效」。
    expect(RELEASES_PAGE).toBe("https://github.com/Ling-cn111/video-to-text/releases");
    expect(RELEASES_PAGE).not.toContain("/releases/latest");
  });

  test("回归 33b：分享文案里有多个链接时优先挑视频链接", () => {
    // B站 分享会带上「UP 主主页」「活动页」等无关链接。固定取第一个的话，
    // 抽到的是主页，后端一路解析失败，用户看到「视频不存在或链接有误」。
    const text = "UP主主页 https://space.bilibili.com/123 视频 https://www.bilibili.com/video/BV1xx411c7mD";
    expect(extractVideoUrl(text)).toBe("https://www.bilibili.com/video/BV1xx411c7mD");
    expect(extractAllUrls(text)).toEqual([
      "https://space.bilibili.com/123",
      "https://www.bilibili.com/video/BV1xx411c7mD",
    ]);

    // 短链同样算「平台认得」
    const shortFirst = "https://b23.tv/AbCdEfG 然后 https://example.com/x";
    expect(extractVideoUrl(shortFirst)).toBe("https://b23.tv/AbCdEfG");

    // 一条都不认得时仍返回第一条：不能让用户连报错都看不到
    expect(extractVideoUrl("https://example.com/a https://example.com/b")).toBe("https://example.com/a");
    expect(extractVideoUrl("没有链接")).toBeNull();
  });

  test("回归 33c：extractVideoUrl 不会重现 extractFirstUrl 的「抽错」问题", () => {
    // 这是修 bug 的对照：extractFirstUrl 仍然取第一条（有别的调用方），
    // resolveVideoUrl 与分享链路必须改走 extractVideoUrl。
    const text = "https://space.bilibili.com/123 视频 https://www.bilibili.com/video/BV1xx411c7mD";
    expect(extractFirstUrl(text)).toBe("https://space.bilibili.com/123");
    expect(extractVideoUrl(text)).toBe("https://www.bilibili.com/video/BV1xx411c7mD");

    // 端到端：整段文案直接喂进解析，应当拿到视频而不是主页
    const result = resolveVideoUrl(text);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.platform).toBe("bilibili");
      expect(result.videoId).toBe("BV1xx411c7mD");
    }

    // 分享链路走的是同一个函数
    expect(sharedTextToVideoInput(text)).toBe("https://www.bilibili.com/video/BV1xx411c7mD");
  });

  test("回归 33d：短链末段不是 BV 号时也不编造假链接", () => {
    // 前端从 b23.tv/AbCdEfG 里解析出的 videoId 是 "AbCdEfG"，它不是 BV 号，
    // canonicalUrlFor 必须返回 null（由后端展开短链），不能拼成
    // https://www.bilibili.com/video/AbCdEfG —— 那才是真正的「视频不存在」。
    const result = resolveVideoUrl("https://b23.tv/AbCdEfG");
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.videoId).toBe("AbCdEfG");
    expect(sharedTextToVideoInput("https://b23.tv/AbCdEfG")).toBe("https://b23.tv/AbCdEfG");
  });

  test("回归 33e：「取 Key」跟随草稿里的服务商，不等保存", async ({ page }) => {
    // 真机反馈：「api设置只有deepseek取key，选择其他服务商的时候不能对应」。
    // 根因是 activePreset 从**已保存的 config** 反推：点了「阿里云百炼」只改了
    // 草稿，没保存 → config 还是空 → 回落到 DeepSeek 的取 Key 地址。
    await page.goto("/");
    await page.getByTestId("open-api-settings").click();
    await expect(page.getByTestId("api-settings-dialog")).toBeVisible();

    // 初始无配置：不该给一个「DeepSeek 取 Key」的假链接。
    // 这也是「其他 / 自建」的初始态 —— 没有可去的 Key 页面时给一句说明，
    // 给一个到不了的链接比不给更差（用户会以为自己点错了）。
    await expect(page.getByTestId("byok-key-url-absent")).toBeVisible();
    await expect(page.getByTestId("byok-key-url")).toHaveCount(0);

    // 点「阿里云百炼」：草稿地址变了，取 Key 必须立刻跟着换
    await page.getByTestId("byok-preset-dashscope").click();
    await expect(page.getByTestId("byok-key-url")).toBeVisible();
    await expect(page.getByTestId("byok-key-url")).toContainText("阿里云百炼");
    await expect(page.getByTestId("byok-key-url")).not.toContainText("DeepSeek");
    await expect(page.getByTestId("byok-llm-base-url-input")).toHaveValue(
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    );

    // 换到 DeepSeek 同样要跟着走
    await page.getByTestId("byok-preset-deepseek").click();
    await expect(page.getByTestId("byok-key-url")).toContainText("DeepSeek");

    // 手打一个自建地址（「其他 / 自建」的真实用法）：认不出服务商，
    // 就不再显示任何「取 Key」链接
    await page.getByTestId("byok-llm-base-url-input").fill("https://my-proxy.example.com/v1");
    await expect(page.getByTestId("byok-key-url-absent")).toBeVisible();
    await expect(page.getByTestId("byok-key-url")).toHaveCount(0);
  });
});
