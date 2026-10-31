import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { canonicalUrlFor, extractFirstUrl, resolveVideoUrl } from "../src/lib/platform";
import { SHARE_DEDUPE_MS, sharedTextToVideoInput, shouldIgnoreShare } from "../src/lib/share-intent";

/**
 * PR12 的回归测试（系统分享接收 + 短链规范化）。
 *
 * 编号从回归 32 起，接在 pr11 的 31h 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 分享文案是**整段文本**，不是干净链接。抽不出 URL 时必须返回 null，
 *    不能把整段丢进 `new URL`（要么抛错，要么静默把中文编码进 path）。
 * 2. 抽出链接后要按裸号规则补全，否则后端第一步校验就判非法。
 * 3. 送后端的链接必须是 canonical 形式 —— b23.tv 短链会打掉后端两条快路径。
 * 4. 原生侧（manifest / MainActivity）的接线必须真的存在，不能只在 JS 里写。
 */

const ROOT = join(__dirname, "..");

function read(relativePath: string): string {
  return readFileSync(join(ROOT, relativePath), "utf8");
}

test.describe("PR12 系统分享接收", () => {
  test("回归 32：整段分享文案能抽出链接并归一成可提交的输入", () => {
    const cases: Array<[string, string | null]> = [
      ["【标题】 看看这个 https://b23.tv/AbCdEfG 复制打开", "https://b23.tv/AbCdEfG"],
      ["https://www.bilibili.com/video/BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD"],
      // 分享出来的裸号要补成完整链接：后端只接受公网 http(s) URL
      ["BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD"],
      ["av170001", "https://www.bilibili.com/video/av170001"],
      // 认不出就返回 null，由调用方给用户提示；不能瞎猜
      ["今天天气不错", null],
      ["", null],
      ["   ", null],
    ];
    for (const [input, expected] of cases) {
      expect(sharedTextToVideoInput(input), `输入：${JSON.stringify(input)}`).toBe(expected);
    }
    expect(sharedTextToVideoInput(null)).toBeNull();
    expect(sharedTextToVideoInput(undefined)).toBeNull();
  });

  test("回归 32b：抽链接不会吞掉中文，也不会把尾随标点带进来", () => {
    expect(extractFirstUrl("看这个（https://b23.tv/XyZ）")).toBe("https://b23.tv/XyZ");
    expect(extractFirstUrl("https://www.bilibili.com/video/BV1xx411c7mD。")).toBe(
      "https://www.bilibili.com/video/BV1xx411c7mD",
    );
    expect(extractFirstUrl("【分享】https://b23.tv/A1b2C3 标题")).toBe("https://b23.tv/A1b2C3");
    expect(extractFirstUrl("没有链接的纯文本")).toBeNull();
    expect(extractFirstUrl("")).toBeNull();

    // 尾随文字不能被百分号编码进 path —— 那会得到一个不存在的 videoId
    const result = resolveVideoUrl("https://b23.tv/AbCdEfG 复制打开");
    expect(result).toEqual({ ok: true, platform: "bilibili", videoId: "AbCdEfG" });
  });

  test("回归 32c：送后端的是 canonical 链接，短链被还原", () => {
    // 这条是「分享能用但很慢」的根因：b23.tv 打掉后端 subtitles/audio 两条快路径
    expect(canonicalUrlFor({ platform: "bilibili", videoId: "BV1xx411c7mD" })).toBe(
      "https://www.bilibili.com/video/BV1xx411c7mD",
    );
    expect(canonicalUrlFor({ platform: "bilibili", videoId: "av170001" })).toBe(
      "https://www.bilibili.com/video/av170001",
    );
    // 认不出的形态返回 null，调用方保留原链接 —— 不能拼出一个必然 404 的地址
    expect(canonicalUrlFor({ platform: "bilibili", videoId: "AbCdEfG" })).toBeNull();
    expect(canonicalUrlFor({ platform: "douyin", videoId: "7301234567890123456" })).toBeNull();
  });

  test("回归 32d：同一条分享不会被处理两次", () => {
    const at = 1_000_000;
    // 第一次：没有历史，必须处理
    expect(shouldIgnoreShare({ text: "https://b23.tv/AbCdEfG" }, null, at)).toBe(false);
    // 窗口内的重复投递（retain 补发 + 实时派发）要忽略
    expect(
      shouldIgnoreShare({ text: "https://b23.tv/AbCdEfG" }, { text: "https://b23.tv/AbCdEfG", at }, at + 500),
    ).toBe(true);
    // 窗口外是用户又分享了一次，必须处理
    expect(
      shouldIgnoreShare(
        { text: "https://b23.tv/AbCdEfG" },
        { text: "https://b23.tv/AbCdEfG", at },
        at + SHARE_DEDUPE_MS + 1,
      ),
    ).toBe(false);
    // 文本不同一律处理
    expect(shouldIgnoreShare({ text: "https://b23.tv/Other1" }, { text: "https://b23.tv/AbCdEfG", at }, at + 10)).toBe(
      false,
    );
    // 空文本不触发任何提交
    expect(shouldIgnoreShare({ text: "   " }, null, at)).toBe(true);
    expect(shouldIgnoreShare(null, null, at)).toBe(true);
  });

  test("回归 32e：Android 侧真的声明了 SEND 意图并注册了插件", () => {
    // 只看 JS 是不够的：分享入口在 manifest 里，漏了就是「分享面板里没有本应用」
    const manifest = read("android/app/src/main/AndroidManifest.xml");
    expect(manifest).toContain('android.intent.action.SEND');
    expect(manifest).toContain('android:mimeType="text/plain"');
    // 没有所有权就别声明 https 域名的 VIEW 过滤，否则每次点 B站 链接都弹选择框
    expect(manifest).not.toContain("android.intent.category.BROWSABLE");
    // singleTask 是深链/分享复用的前提，改动会让人每次分享都新开一个实例
    expect(manifest).toContain('android:launchMode="singleTask"');

    const mainActivity = read("android/app/src/main/java/com/videototext/app/MainActivity.java");
    expect(mainActivity).toContain("registerPlugin(ShareIntentPlugin.class)");

    const plugin = read("android/app/src/main/java/com/videototext/app/ShareIntentPlugin.java");
    expect(plugin).toContain('@CapacitorPlugin(name = "ShareIntent")');
    expect(plugin).toContain("Intent.ACTION_SEND");
    expect(plugin).toContain("Intent.EXTRA_TEXT");
    // retainUntilConsumed 必须为 true：冷启动时 JS 还没注册监听器
    expect(plugin).toContain("notifyListeners(EVENT_SHARE, payload, true)");
  });

  test("回归 32f：网页构建不注册分享监听（只在移动壳内启用）", async ({ page }) => {
    // 分享监听依赖原生桥，网页里注册只会抛错并刷一条 console.warn
    await page.goto("/");
    await expect(page.getByTestId("video-url-input")).toBeVisible();
    const hasShareIntent = await page.evaluate(() => {
      const w = window as unknown as { ShareIntent?: unknown };
      return typeof w.ShareIntent !== "undefined";
    });
    expect(hasShareIntent).toBe(false);
  });
});
