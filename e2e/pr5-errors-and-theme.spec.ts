import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * P1-2 回归测试：错误边界 / 暗色模式 / robots / 安全响应头。
 *
 * 编号从「回归 10」起，不占用 docs/frontend-features.md 的锁定编号。
 *
 * 这四件事过去都是「代码里根本没有」，属于沉默的缺失：
 * 渲染异常白屏、404 英文页、深色变量备好却没开关、没有 robots 与安全头。
 * 缺失型问题不会在功能测试里暴露，所以这里逐条钉住。
 */

/** 把 .next/static/chunks 里所有 JS 拼起来，用于断言某段文案确实被打进产物 */
function buildChunksText(): string {
  const dir = join(process.cwd(), ".next", "static", "chunks");
  const walk = (current: string): string[] => {
    const entries = readdirSync(current, { withFileTypes: true });
    return entries.flatMap((entry) => {
      const full = join(current, entry.name);
      if (entry.isDirectory()) return walk(full);
      return entry.name.endsWith(".js") ? [full] : [];
    });
  };
  return walk(dir)
    .map((file) => readFileSync(file, "utf8"))
    .join("\n");
}

test.describe("P1-2 错误边界与站点元信息", () => {
  test("回归 10：未知路径显示中文 404 页而不是英文默认页", async ({ page }) => {
    const response = await page.goto("/definitely-not-a-real-page-xyz");
    expect(response?.status()).toBe(404);

    const notFound = page.getByTestId("not-found");
    await expect(notFound).toBeVisible();
    await expect(notFound).toContainText("页面不存在");
    // 关键：不再是 Next 默认的英文文案
    await expect(page.locator("body")).not.toContainText("This page could not be found");

    // 有返回首页的出口，不是死胡同
    await expect(notFound.getByRole("link", { name: "返回首页" })).toBeVisible();
  });

  test("回归 10b：错误边界真的被打进构建产物（删掉文件就会红）", async () => {
    const chunks = buildChunksText();

    // error.tsx：路由级边界，带重试与错误编号
    expect(chunks).toContain("页面出错了");
    expect(chunks).toContain("这不是你的操作问题");
    expect(chunks).toContain("错误编号");

    // global-error.tsx：根布局崩溃时兜底，文案与 error.tsx 不同，两者都要在
    expect(chunks).toContain("应用未能启动");
    expect(chunks).toContain("页面在加载最外层框架时就失败了");
  });

  test("回归 10c：缺参数的 /result 走兜底引导，不白屏", async ({ page }) => {
    await page.goto("/result?id=");
    const body = page.locator("body");
    await expect(body).toBeVisible();

    const text = (await body.innerText()).trim();
    expect(text.length).toBeGreaterThan(10);
    // 绝不能是框架默认的错误页
    expect(text).not.toContain("Application error");
    expect(text).not.toContain("This page could not be found");
  });

  test("回归 11：robots.txt 可达且禁止抓取任务页", async ({ request }) => {
    const response = await request.get("/robots.txt");
    expect(response.status()).toBe(200);

    const body = await response.text();
    expect(body).toContain("User-Agent: *");
    expect(body).toContain("Disallow: /processing");
    expect(body).toContain("Disallow: /result");
    // 必须指向 sitemap，否则搜索引擎发现不了
    expect(body).toContain("Sitemap:");
    expect(body).toContain("/sitemap.xml");
  });

  test("回归 11b：sitemap.xml 可达且只含首页", async ({ request }) => {
    const response = await request.get("/sitemap.xml");
    expect(response.status()).toBe(200);

    const body = await response.text();
    expect(body).toContain("<urlset");
    // 任务页带 ?id= 且 7 天后过期，收录进来只会是死链
    expect(body).not.toContain("/processing");
    expect(body).not.toContain("/result");
    expect(body.match(/<loc>/g)?.length).toBe(1);
  });

  test("回归 12：安全响应头齐全（CSP / 防点击劫持 / nosniff）", async ({ request }) => {
    const response = await request.get("/");
    const headers = response.headers();

    const csp = headers["content-security-policy"];
    expect(csp).toBeTruthy();
    // 防 XSS 的核心几条
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'self'");

    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    // 任务页 URL 含 taskId，跨站跳转不能整条泄露
    expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
    expect(headers["permissions-policy"]).toContain("microphone=()");
  });

  test("回归 12b：Service Worker 不被缓存（否则老客户端长期跑旧外壳）", async ({ request }) => {
    const response = await request.get("/sw.js");
    expect(response.status()).toBe(200);
    const cacheControl = response.headers()["cache-control"] ?? "";
    expect(cacheControl).toContain("no-cache");
    expect(cacheControl).toContain("no-store");
  });

  test("回归 13：主题可切换且刷新后保留", async ({ page }) => {
    // 固定系统偏好为浅色，否则 CI 的 headless 若默认深色，首帧断言会不稳
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");

    const toggle = page.getByTestId("theme-toggle");
    await expect(toggle).toBeVisible();

    // 默认跟随系统（系统是浅色，所以此时不该有 .dark）
    await expect(page.locator("html")).not.toHaveClass(/dark/);

    await toggle.click();
    await page.getByTestId("theme-dark").click();

    // 关键：next-themes 往 <html> 加 .dark 类，globals.css 的深色变量才生效
    await expect(page.locator("html")).toHaveClass(/dark/);
    // 背景真的变了（变量生效，不只是加了个类名）
    const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(darkBg).not.toBe("rgb(255, 255, 255)");

    // 刷新后保留：next-themes 把选择写进 localStorage 的 theme 键
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    expect(await page.evaluate(() => localStorage.getItem("theme"))).toBe("dark");

    // 切回浅色也要真的变白
    await page.getByTestId("theme-toggle").click();
    await page.getByTestId("theme-light").click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
    const lightBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(lightBg).toBe("rgb(255, 255, 255)");
  });

  test("回归 13b：深色模式下首页仍可读（文字不是深底深字）", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");

    // 跟随系统时，系统深色 → 应自动进入深色，无需手动切换
    await expect(page.locator("html")).toHaveClass(/dark/);

    const body = page.locator("body");
    const colors = await body.evaluate((el) => {
      const style = getComputedStyle(el);
      return { color: style.color, background: style.backgroundColor };
    });

    // 深色态下前景必须比背景亮，否则等于看不见
    const parse = (value: string) => (value.match(/\d+/g) ?? []).slice(0, 3).map(Number);
    const fg = parse(colors.color);
    const bg = parse(colors.background);
    expect(fg.length).toBe(3);
    expect(bg.length).toBe(3);
    const luminance = (rgb: number[]) => 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    expect(luminance(fg)).toBeGreaterThan(luminance(bg));
  });
});
