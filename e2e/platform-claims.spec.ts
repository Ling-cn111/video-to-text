import { expect, test } from "@playwright/test";

import { resolveVideoUrl } from "../src/lib/platform";
import {
  SUPPORTED_PLATFORMS,
  isPlatformSupported,
  supportedPlatformsShortText,
  supportedPlatformsText,
  unavailablePlatformMessage,
  unsupportedHostMessage,
} from "../src/lib/platforms/registry";

/**
 * PR2（P0-4）回归：平台宣称必须与真实能力一致。
 *
 * 背景：首页曾写死「支持 B站 / 抖音」，而真实后端 `backend/app/platforms.py`
 * 只注册了 bilibili —— 抖音只存在于前端 Mock 的演示数据里。用户粘贴抖音链接
 * 一路走到后端才收到 422，属于最典型的预期违背。
 *
 * 这里不占用「锁定 N」编号（那是 docs/frontend-features.md 的协议），
 * 只断言「文案跟着能力走」这一条不变量。
 */

test.describe("平台宣称与能力一致", () => {
  test("首页文案只承诺当前构建支持的平台", async ({ page }) => {
    await page.goto("/");

    // 首页 Hero 副标题与输入框 placeholder 都必须由 SUPPORTED_PLATFORMS 派生
    const expectedShort = supportedPlatformsShortText();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("section").first()).toContainText(`粘贴${expectedShort}视频链接`);
    await expect(page.getByLabel("视频链接")).toHaveAttribute(
      "placeholder",
      new RegExp(`粘贴${expectedShort}视频链接`),
    );

    // 反向断言：不在支持列表里的平台名不得出现在 Hero 文案中
    const hero = await page.locator("section").first().innerText();
    for (const platform of ["哔哩哔哩", "抖音"] as const) {
      const id = platform === "抖音" ? "douyin" : "bilibili";
      if (!isPlatformSupported(id)) {
        expect(hero).not.toContain(platform);
      }
    }
  });

  test("示例链接只列当前构建支持的平台", async ({ page }) => {
    await page.goto("/");
    // 平台示例条数 == 支持平台数（另有两条固定的异常演示入口）
    const platformExamples = page.locator("button", { hasText: "示例" });
    const labels = await platformExamples.allInnerTexts();
    const platformLabels = labels.filter((label) => !label.includes("演示"));

    expect(platformLabels).toHaveLength(SUPPORTED_PLATFORMS.length);
    for (const platform of SUPPORTED_PLATFORMS) {
      const shortName = platform === "douyin" ? "抖音" : "B站";
      expect(platformLabels.some((label) => label.includes(shortName))).toBe(true);
    }
  });

  test("文案函数按能力生成（含未接入平台的专属提示）", () => {
    // 支持列表与能力判断一致
    expect(supportedPlatformsText()).toContain("哔哩哔哩");
    expect(supportedPlatformsShortText()).toContain("B站");
    expect(SUPPORTED_PLATFORMS.length).toBeGreaterThan(0);

    // 「不认识这个站」：说清当前支持哪些
    expect(unsupportedHostMessage("v.qq.com")).toContain("暂不支持");
    expect(unsupportedHostMessage("v.qq.com")).toContain("v.qq.com");
    expect(unsupportedHostMessage("v.qq.com")).toContain(supportedPlatformsText());

    // 「认得但没接上」：不能复用「不支持」的措辞，否则听起来像永远不会支持
    const douyinMessage = unavailablePlatformMessage("douyin");
    expect(douyinMessage).toContain("暂未支持");
    expect(douyinMessage).not.toContain("暂不支持");
    expect(douyinMessage).toContain(supportedPlatformsText());
  });

  test("resolveVideoUrl 对未接入平台返回 unavailable 而不是放行", () => {
    const result = resolveVideoUrl("https://www.douyin.com/video/7301234567890123456");
    if (isPlatformSupported("douyin")) {
      // Mock 构建：抖音确实能演示，正常放行
      expect(result.ok).toBe(true);
    } else {
      // 真实构建：必须在前端拦下，别让用户等一轮后端 422
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.reason).toBe("unavailable");
        expect(result.reason === "unavailable" && result.platform).toBe("douyin");
      }
    }

    // 未注册域名始终是 unsupported
    const unknown = resolveVideoUrl("https://v.qq.com/x/cover/mzc00200xxxx.html");
    expect(unknown.ok).toBe(false);
    if (!unknown.ok) expect(unknown.reason).toBe("unsupported");
  });
});
