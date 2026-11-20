/// 桌面端导出：弹系统「另存为」对话框让用户选目标文件夹，选中后直接写盘。
///
/// 之前桌面壳只能走 WebView 的 `blob:` 下载——WebView2 默认静默存到「下载」目录，
/// 用户既选不了文件夹，也常常找不到文件落在哪。此命令与 `src/lib/share-file.ts`
/// 的桌面分支配套，只在桌面壳内被调用。
///
/// 返回 `Ok(None)` 表示用户取消了对话框——这是正常操作，不是错误。
/// 用 `spawn_blocking` 在独立线程跑：`rfd` 的文件对话框会阻塞当前线程，
/// 且 Windows 的 COM 初始化不允许在主线程上跑模态对话框。
/// 注意：当前发版目标只有 Windows（msi / nsis）；若未来支持 macOS / Linux，
/// `rfd` 需要在主线程上调用，这里要改成 `run_on_main_thread` 方案。
#[tauri::command]
async fn save_text_file(
  content: String,
  default_filename: String,
  title: Option<String>,
) -> Result<Option<String>, String> {
  tauri::async_runtime::spawn_blocking(move || {
    let mut dialog = rfd::FileDialog::new().set_file_name(&default_filename);
    // 按扩展名加过滤器，防止用户存出无后缀文件；认不出扩展名就不加，保持默认行为
    if let Some(ext) = default_filename.rsplit('.').next() {
      if default_filename.contains('.') && !ext.is_empty() && ext.len() <= 5 {
        dialog = dialog.add_filter(&format!("*.{ext}"), &[ext]);
      }
    }
    if let Some(t) = title.as_deref() {
      if !t.is_empty() {
        dialog = dialog.set_title(t);
      }
    }
    match dialog.save_file() {
      Some(path) => {
        std::fs::write(&path, content.as_bytes()).map_err(|e| e.to_string())?;
        Ok(Some(path.to_string_lossy().into_owned()))
      }
      None => Ok(None),
    }
  })
  .await
  .map_err(|e| e.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    // 桌面端导出的「另存为」对话框（见 save_text_file）
    .invoke_handler(tauri::generate_handler![save_text_file])
    // 自动更新：检查 → 下载 → 安装。endpoints 与公钥在 tauri.conf.json 的 plugins.updater
    .plugin(tauri_plugin_updater::Builder::new().build())
    // 安装完成后需要重启进程才能跑上新版本（Windows 会自行拉起安装器，其余平台要主动 restart）
    .plugin(tauri_plugin_process::init())
    // 用系统浏览器打开外部链接。没有它，WebView2 会直接吞掉 target="_blank" 的点击
    // （wry 在未注册 new_window_req_handler 时执行 args.SetHandled(true)），
    // 表现就是「点打开发布页没反应」。
    .plugin(tauri_plugin_opener::init())
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while building tauri application");
}
