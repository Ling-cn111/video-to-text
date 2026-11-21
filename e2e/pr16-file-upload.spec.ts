import { expect, test } from "@playwright/test";

import { FILE_ALLOWED_EXTENSIONS, FILE_MAX_DURATION_SECONDS, FILE_MAX_SIZE_MB, validateVideoFile } from "../src/lib/file-upload";
import { ApiError } from "../src/lib/http";

/**
 * PR16 的回归测试：本地视频上传（M3）。
 *
 * 编号从回归 36 起，接在 pr15 的 35f 之后；不占用 docs/frontend-features.md
 * 的锁定编号。
 *
 * 这一组想守住的不变量：
 * 1. 上传入口由 capabilities.fileUploadConfigured 驱动（Mock 恒 true），
 *    请求链路必须是「init → 直传（Mock 不发网络）→ kind=file 的 transcribe」
 * 2. kind=file 的请求体必须带完整 file 引用（objectKey/fileName/size）与
 *    engine/hotwords——字段悄悄丢掉是最难查的那类 bug（与热词同款教训）
 * 3. 预校验在前端拦截非法输入（扩展名 / 大小 / 时长），错误码与后端契约一致
 * 4. 文件任务复用进度页与结果页（taskId 来源无关），最近转写记录「本地文件」
 */
test.describe("PR16 本地文件上传", () => {
  /** 造一个「名为 .mp4 的假文件」：Mock 上传与转写都不读内容，名字才是契约 */
  function fakeVideoBuffer(): Buffer {
    return Buffer.from("mock-video-content-for-e2e");
  }

  function fakeFile(name: string, sizeOverride?: number): File {
    const file = new File([fakeVideoBuffer()], name, { type: "video/mp4" });
    if (sizeOverride !== undefined) {
      Object.defineProperty(file, "size", { value: sizeOverride });
    }
    return file;
  }

  test("回归 36：预校验错误码与后端契约一致（扩展名 / 大小 / 时长上限）", () => {
    // 扩展名白名单外 → FILE_FORMAT_UNSUPPORTED
    const wrongExt = validateVideoFile(fakeFile("demo.avi"));
    expect(wrongExt).toBeInstanceOf(ApiError);
    expect((wrongExt as ApiError).code).toBe("FILE_FORMAT_UNSUPPORTED");

    // 超过 FILE_MAX_SIZE_MB → FILE_TOO_LARGE（不真分配 500MB，直接覆写 size 元数据）
    const oversize = validateVideoFile(fakeFile("huge.mp4", FILE_MAX_SIZE_MB * 1024 * 1024 + 1));
    expect(oversize).toBeInstanceOf(ApiError);
    expect((oversize as ApiError).code).toBe("FILE_TOO_LARGE");

    // 合法输入 → null（时长探测在 UI 层异步做，这里只守纯函数）
    expect(validateVideoFile(fakeFile("demo.mp4"))).toBeNull();

    // 数值护栏：上限与后端默认值对齐（后端 FILE_MAX_SIZE_MB=500，WAV≈54.6 分钟 → 前端 50 分钟）
    expect([...FILE_ALLOWED_EXTENSIONS]).toEqual(["mp4", "mov", "webm"]);
    expect(FILE_MAX_SIZE_MB).toBe(500);
    expect(FILE_MAX_DURATION_SECONDS).toBe(3000);
  });

  test("回归 36b：选择文件后按 init → kind=file 的 transcribe 顺序发起，并跳转进度页", async ({ page }) => {
    const initBodies: Array<Record<string, unknown>> = [];
    const transcribeBodies: Array<Record<string, unknown>> = [];
    let ossPutSeen = false;

    page.on("request", (request) => {
      if (request.url().includes("/api/upload/init") && request.method() === "POST") {
        try {
          initBodies.push(JSON.parse(request.postData() ?? "{}"));
        } catch {
          /* 忽略非 JSON */
        }
      }
      if (request.url().includes("/api/transcribe") && request.method() === "POST") {
        try {
          transcribeBodies.push(JSON.parse(request.postData() ?? "{}"));
        } catch {
          /* 忽略非 JSON */
        }
      }
      // Mock 模式绝不真发 PUT：任何对非同源的上传字节请求都算失败
      if (request.method() === "PUT" && !request.url().startsWith("http://localhost:3000")) {
        ossPutSeen = true;
      }
    });

    await page.goto("/");
    // 入口由 capabilities.fileUploadConfigured 驱动；Mock 恒 true
    const dropzone = page.getByTestId("file-upload");
    await expect(dropzone).toBeVisible();

    await page.getByTestId("file-upload-input").setInputFiles({
      name: "课堂录像.mp4",
      mimeType: "video/mp4",
      buffer: fakeVideoBuffer(),
    });

    // Mock 上传约 1.2 秒：进度条应在此窗口出现（真实百分比由 XHR onprogress 驱动，
    // Mock 是模拟步进——两者 UI 路径相同）
    await expect(page.getByTestId("file-upload-progress")).toBeVisible();

    const transcribeRequest = page.waitForRequest(
      (request) => request.url().includes("/api/transcribe") && request.method() === "POST",
    );
    // 时长探测的内部兜底超时（8s）+ Mock 上传（1.2s）+ 网络往返：全量并发下放宽
    await expect(page).toHaveURL(/\/processing\?id=/, { timeout: 30_000 });
    const body = JSON.parse((await transcribeRequest).postData() ?? "{}");

    // init 请求体：只有元数据，没有任何文件字节
    expect(initBodies).toHaveLength(1);
    expect(initBodies[0]).toMatchObject({ fileName: "课堂录像.mp4", mimeType: "video/mp4" });
    expect(initBodies[0]["size"]).toBeGreaterThan(0);

    // transcribe 请求体：kind=file + 完整引用 + engine/hotwords 照传
    expect(body["kind"]).toBe("file");
    expect(body["file"]).toMatchObject({ fileName: "课堂录像.mp4" });
    expect(String(body["file"]["objectKey"])).toContain("uploads/");
    expect(body["hotwords"]).toEqual([]);
    // Mock 模式本地 ASR 可用（capabilities 恒 true），engine 带用户默认偏好
    expect(body["engine"]).toBe("local");

    expect(ossPutSeen).toBe(false);

    // Mock 任务 10 秒后进度页会自动跳去结果页——页面细节必须趁窗口期断言。
    // 进度页视频信息卡：标题=文件名、「本地文件」徽章、无封面占位图标
    await expect(page.getByRole("heading", { name: "课堂录像.mp4" })).toBeVisible();
    await expect(page.getByText("本地文件", { exact: true })).toBeVisible();
    await expect(page.getByTestId("video-cover-fallback")).toBeVisible();
  });

  test("回归 36c：非法扩展名在前端被拦，不发起任何请求", async ({ page }) => {
    let transcribeSeen = false;
    page.on("request", (request) => {
      if (request.url().includes("/api/transcribe") && request.method() === "POST") {
        transcribeSeen = true;
      }
    });

    await page.goto("/");
    await page.getByTestId("file-upload-input").setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("not a video"),
    });

    // 契约错误码的中文文案（error-titles：FILE_FORMAT_UNSUPPORTED → 暂不支持该格式）
    await expect(page.getByText("暂不支持「txt」格式")).toBeVisible();
    expect(transcribeSeen).toBe(false);
    // 输入区还在，可以重新选择
    await expect(page.getByTestId("file-upload")).toBeVisible();
  });

  test("回归 36d：文件任务复用结果页与最近转写（platform=local 记录不被丢弃）", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("file-upload-input").setInputFiles({
      name: "课堂录像.mp4",
      mimeType: "video/mp4",
      buffer: fakeVideoBuffer(),
    });

    // 直接等结果页：Mock 任务约 10 秒走完 + 总结；不中途断言进度页细节
    // （那条竞态归 36b 管——页面 10 秒后自动跳走）
    await expect(page).toHaveURL(/\/result\?id=/, { timeout: 45_000 });
    await expect(page.getByTestId("fulltext-article")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("本地上传的文件与链接转写共用同一条流水线").first()).toBeVisible();

    // 最近转写：标题 = 文件名（platform=local 的记录通过了校验，否则根本不会出现）
    await page.goto("/");
    await expect(page.getByText("课堂录像.mp4").first()).toBeVisible();
  });
});
