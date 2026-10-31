# 桌面端打包（Tauri / Windows）

本文记录把前端打成 Windows 安装包（`.msi` / `.exe`）的完整流程，以及在
中国大陆网络环境下实际踩到的坑。README 里的「桌面端（Tauri）」章节是速查版，
这里是完整版。

---

## 1. 为什么是静态导出

Tauri 本身不带 Node 运行时，窗口里加载的是本地文件，所以前端必须能完全静态化：

- `next.config.mjs` 在 `BUILD_TARGET=desktop` 时切到 `output: 'export'`
- 产物 `out/` 由 Tauri 的 asset 协议直接读取（`frontendDist: "../out"`）
- 另一条路是内置 `node.exe` 起本地服务（`output: 'standalone'` + sidecar），
  代码零改动但包体 120MB+、启动慢 2~4 秒。本项目选了静态导出（包体 ~2.3MB）。

静态导出带来的三处约束，都在代码里有对应处理：

| 约束 | 处理 |
| --- | --- |
| 不支持 `rewrites`（没有服务端） | 桌面端改直连后端绝对地址，见 `src/lib/api-base.ts` |
| 不支持动态段 `/x/[id]`（无法枚举取值） | 改查询参数 `/x?id=`，见 `src/lib/routes.ts` |
| 不支持 Route Handler 的 POST | 构建期临时移出 `src/app/api/`，见 `scripts/build-shell.mjs` |

### 1.1 后端地址如何切换

```
web   : fetch('/backend-api/parse')  → Next rewrites → BACKEND_ORIGIN/api/parse
桌面端 : fetch('<NEXT_PUBLIC_API_BASE>/api/parse')      ← 跨域直连
```

`apiUrl()` 按有无 `NEXT_PUBLIC_API_BASE` 二选一。桌面端由
`scripts/build-shell.mjs` 注入该变量（取值顺序：`SHELL_API_BASE` > `DESKTOP_API_BASE`（旧名）>
`.env.local` 的 `BACKEND_ORIGIN`）。

跨域能成立的前提是后端放行：`CORS_ORIGINS=*`（本项目线上即如此）。
Tauri v2 在 Windows 下页面的 origin 是 `http://tauri.localhost`（生产）
或 `https://tauri.localhost`（部分配置），白名单要能覆盖它；用 `*` 最省事。

### 1.2 路由为什么从 `/result/<id>` 改成 `/result?id=<id>`

`output: 'export'` 要求每个动态段都由 `generateStaticParams` 枚举出全部取值。
taskId 是运行时生成的，枚举不出来，构建会直接失败或只导出空壳。

改成查询参数后：

- 桌面端：`/result/index.html` 一个文件，客户端用 `useSearchParams()` 读 id
- web 端：`next.config.mjs` 配了 `redirects`，`/result/<id>` → 308 →
  `/result?id=<id>`，**老书签和外部链接不受影响**

⚠️ 新增跳转时务必用 `src/lib/routes.ts` 的 `resultHref()` / `processingHref()`，
不要手拼字符串——手拼的 `/result/${id}` 在桌面端会 404。

---

## 2. 构建环境

| 依赖 | 版本/来源 | 说明 |
| --- | --- | --- |
| Rust | `stable-x86_64-pc-windows-msvc` | `rustup` 装，用户级，无需管理员 |
| MSVC C++ 生成工具 | VS 2022 Build Tools | 勾「C++ 生成工具」+ Windows SDK |
| WebView2 Runtime | Win11 / 较新 Win10 内置 | Win7/8 与老 Win10 需单独装 |
| NSIS / WiX | Tauri 自动下载 | 缓存在 `%LOCALAPPDATA%\tauri\` |

首次 `cargo build --release` 约 5 分钟（几百个 crate），之后增量 1 分钟左右。

### 2.1 网络受限时的关键环境变量

Tauri 打包阶段会从 GitHub Releases 下载 NSIS 与 WiX。GitHub 直连不通时，
`tauri build` 会卡在 `Downloading ...` 直到 `timeout: global`。Tauri 内置了镜像
支持，设一个变量即可：

```powershell
$env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR = "https://ghfast.top/https://github.com"
```

注意镜像地址必须**能接在 `github.com/` 前面**（形如
`<mirror>/https://github.com/<owner>/<repo>/...`）。只填域名根路径（如
`https://ghfast.top`）会 403。

另一个坑：Windows 的 schannel 会因「无法检查证书吊销状态」而拒绝 GitHub 的
TLS 证书链（`CRYPT_E_NO_REVOCATION_CHECK`）。手写 `curl` 验证时加
`--ssl-no-revoke`；走上面镜像后通常不触发。

---

## 3. WiX 中文产品名（LGHT0311）

`productName` 含中文时，MSI 打包会失败：

```
error LGHT0311 : A string was provided with characters that are not available
in the specified database code page '1252'.
```

原因：Tauri 默认 WiX locale 是 `en-US`、代码页 1252（西欧），存不下中文字符。

修复方式（本项目已做）：

1. 新建 `src-tauri/wix/zh-CN.wxl`，把语言设为 2052、代码页设为 936，
   并且**不引用产品名**（避免中文再次进入本地化字符串）：

   ```xml
   <WixLocalization Culture="zh-CN" Language="2052" Codepage="936"
     xmlns="http://schemas.microsoft.com/wix/2006/localization">
     <String Id="TauriLanguage">2052</String>
     <String Id="TauriCodepage">936</String>
     ...
   </WixLocalization>
   ```

2. 在 `tauri.conf.json` 里引用：

   ```json
   "bundle": {
     "windows": {
       "wix": { "language": { "zh-CN": { "localePath": "wix/zh-CN.wxl" } } }
     }
   }
   ```

改完产物名会从 `_en-US.msi` 变成 `_zh-CN.msi`。校验脚本：

```bash
node scripts/check-msi-locale.mjs   # 直接读 MSI 数据库里的 ProductName，确认中文没坏
```

> NSIS 包不受此问题影响（原生 Unicode），它也是普通用户最该下载的那个。

---

## 4. 安装模式差异（实测）

| | MSI（WiX） | NSIS（`.exe`） |
| --- | --- | --- |
| 默认作用域 | `perMachine`，**需要管理员** | 本项目配为 `currentUser` |
| 无提权静默安装 | 失败，退出码 `1603` | 成功，退出码 `0` |
| 安装位置 | `C:\Program Files\<产品名>` | `%LOCALAPPDATA%\<产品名>` |
| 适合 | 企业分发、组策略、静默部署 | 普通用户双击安装 |

`tauri.conf.json` 里 NSIS 的 `installMode: "currentUser"` 就是为了让最终用户
不需要管理员权限。如果确实要 perMachine，改回 `"both"` 或 `"perMachine"` 并在
安装时接受 UAC。

---

## 5. 构建与验收命令

```powershell
# 一次性：准备工具链（管理员装 VS Build Tools；rustup 用户级即可）
winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
rustup-init -y --default-toolchain stable-x86_64-pc-windows-msvc

# 每次构建
$env:DESKTOP_API_BASE = "https://<后端域名>"
$env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR = "https://ghfast.top/https://github.com"   # 国内网络需要
pnpm tauri build
```

产物：

```
src-tauri/target/release/bundle/msi/<产品名>_<版本>_x64_zh-CN.msi
src-tauri/target/release/bundle/nsis/<产品名>_<版本>_x64-setup.exe
```

验收清单（本项目实际执行过）：

1. `node scripts/check-msi-locale.mjs` — MSI 内中文产品名正确
2. 静默装 NSIS 包 → 开始菜单出现快捷方式 → 启动 → 窗口标题与尺寸正确
3. 首页粘贴真实链接 → 解析 → 进度页 → 结果页出字幕与 AI 总结
4. 静默卸载 → 安装目录与快捷方式均无残留
5. `node scripts/verify-shell.mjs desktop` — 20 项静态壳断言（含自动更新入口、无 Tauri 桥时如实提示）

---

## 5.1 自动更新（v0.1.5 起）

Tauri 的更新机制是「**签名 + 一份 JSON 清单**」，两者缺一不可：

1. **一次性**：生成密钥对并离线保存私钥
   ```bash
   pnpm tauri signer generate -w D:/vtt-toolchain/tauri-keys/vtt-updater.key --ci
   ```
   公钥填进 `src-tauri/tauri.conf.json` 的 `plugins.updater.pubkey`（已填好，改密钥才需要动）。
   **私钥丢失 = 已安装客户端永远收不到自动更新**，必须离线备份；私钥入库等于任何人都能发更新。

2. **每次构建**：把私钥通过环境变量传给 bundler，`bundle.createUpdaterArtifacts: true` 会让它额外产出 `.sig`
   ```bash
   TAURI_SIGNING_PRIVATE_KEY="$(cat D:/vtt-toolchain/tauri-keys/vtt-updater.key)" \
   TAURI_SIGNING_PRIVATE_KEY_PASSWORD="" \
   pnpm tauri build
   ```
   日志里应出现 `Finished 2 updater signatures at:` 两行（msi + nsis 各一个）。

3. **生成清单**：
   ```bash
   node scripts/make-latest-json.mjs --version 0.1.5 --out latest.json
   ```
   它从 `bundle/` 里找 `.sig` 与安装包，拼出 Tauri 要求的格式。**缺 `.sig` 会直接失败退出**——
   无签名的包客户端装不上，宁可发版失败也不要发出去。

4. **发布**：`latest.json` 与安装包上传到**同一个** Release，且资产名必须是 ASCII
   （`gh` 上传中文文件名会被剥成 `_`）。`latest.json` 里的 `url` 指向 `releases/download/<tag>/<asset>`。

`endpoints` 配的是 `releases/latest/download/latest.json`，所以**只要最新 Release 里带这个文件就生效**，
不用改客户端。发版流程已自动化：推 `v*` tag 触发 `.github/workflows/release-desktop.yml`，
它在装依赖前先校验三处版本号一致（`package.json` / `tauri.conf.json` / `Cargo.toml`），
然后构建 → 签名 → 生成 `latest.json` → `gh release create`。

**同一工作流还有第二个 job `android`**（v0.1.7 起）：`needs: build`，在 `ubuntu-latest` 上跑
`pnpm build:mobile` → `npx cap sync android` → `./gradlew assembleRelease` → `gh release upload`，
把 `video-to-text_<version>_android.apk` 追加到同一个 Release。

为什么不并进桌面端那个 job / 为什么必须走 CI：

- **工具链没有交集**：Windows runner + Rust/NSIS/WiX vs Linux + JDK 21 + Android SDK，塞进一个 job
  只会让两边都装一遍对方的东西。
- **`needs: build` 是硬约束**：APK 要传进已建好的 Release；两个 job 同时 `gh release create` 会互踩。
- **手工上传的问题不是慢，是会忘**：v0.1.4 ~ v0.1.6 的 APK 一直是本地 `assembleRelease` 后手补，
  Release 页因此常常「桌面端已是新版本、手机端还是旧的」，用户装上以为新功能没生效。

四条不能动的约束：

1. 签名密钥从 Secrets 还原（`ANDROID_KEYSTORE_BASE64` / `ANDROID_KEYSTORE_PASSWORD` /
   `ANDROID_KEY_ALIAS` / `ANDROID_KEY_PASSWORD`），**任缺一个必须 fail**。`android/app/build.gradle`
   在缺 `keystore.properties` 时会静默跳过 `signingConfigs.release`，产出一个装不上的废包 ——
   这比构建失败更糟，因为它看起来是绿的。
2. `versionName` 必须与 tag 一致（CI 用 `aapt2 dump badging` 拦）。**它和桌面端三处版本号是两份
   独立的配置**，v0.1.6 发版时 `build.gradle` 漏改，APK 里还是 0.1.5。
3. 上传必须带 `--clobber`，重跑流水线时资产已存在。
4. **不要给 Android job 加 `--init-script gradle/init-mirrors.gradle`** —— 那是给国内网络绕
   `dl.google.com` 重置用的，海外 runner 官方仓库本来通，多加一层只是多一次解析。

手动触发流水线时可传 `dry_run: true`：只构建不发布（`Publish release` 与整个 `android` job
都带 `if: ${{ !inputs.dry_run }}`），用于验证改动而不开新版本。

**已知限制**：端点在 `github.com` 上，国内可能不可达。检查失败时客户端静默跳过（自动检查）或
提示失败原因 + 给出 Release 页链接（手动检查）。要覆盖国内用户需把 `latest.json` 与安装包
放到可达域名（如阿里云 FC 反代），属独立改造。

---

## 6. 与 Web 版的行为差异

| | Web | 桌面端 |
| --- | --- | --- |
| 后端 | 同源代理，不跨域 | 跨域直连，依赖后端 CORS |
| 任务页 URL | `/result?id=` | `/result/?id=`（`trailingSlash: true`） |
| Service Worker | 生产环境注册 | **不注册**（本地资源无需缓存） |
| Mock 路由 `/api/*` | 存在 | 构建期移除 |
| 离线可用性 | 依赖 SW 缓存外壳 | 前端全在本地，仅接口需联网 |

Service Worker 在桌面端通过 `NEXT_PUBLIC_STATIC_SHELL=true` 关闭（`NEXT_PUBLIC_DESKTOP` 仍作兼容别名，见
`src/components/pwa/register-sw.tsx`），避免在 asset 协议下注册失败刷警告。
