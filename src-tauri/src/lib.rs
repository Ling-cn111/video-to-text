#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
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
