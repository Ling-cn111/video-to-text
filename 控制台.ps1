# 视频转文字 · 系统托盘控制台（任务 L）
# 用法：
#   托盘模式：双击 控制台.bat（或右键本文件 -> 使用 PowerShell 运行）
#   命令行模式：powershell -ExecutionPolicy Bypass -File 控制台.ps1 -Action start|stop|restart|status
# 说明：编码为 UTF-8 with BOM（PowerShell 5.1 读取中文必需）；启停逻辑与 run.bat 共享
# 端口(8000/3000)、日志(logs/backend-*.log)、PID 文件(.vtt-pids/) 约定，两者可互相管理。
param([string]$Action = "")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$BackendDir = Join-Path $Root "backend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogsDir = Join-Path $Root "logs"
$PidsDir = Join-Path $Root ".vtt-pids"
$PrefsFile = Join-Path $PidsDir "tray-pref.json"
$BackendPort = 8000
$FrontendPort = 3000
$RepoUrl = "http://localhost:$FrontendPort"
$MaxLogFiles = 20
$StartReadyTimeoutSec = 30

# ============ 基础工具 ============

function Get-ListeningMap {
    # 一次 netstat 解析全部 LISTENING 端口 → @{ 端口字符串 = PID }（实测约 30ms）
    # 不用 Get-NetTCPConnection：无匹配时实测约 1.2s，会阻塞 UI 线程导致菜单卡顿
    $map = @{}
    foreach ($line in (netstat -ano 2>$null)) {
        $parts = @($line -split '\s+' | Where-Object { $_ })
        if ($parts.Count -ge 5 -and $parts[3] -eq 'LISTENING') {
            $local = $parts[1]
            $port = $local.Substring($local.LastIndexOf(':') + 1)
            if ($port -match '^\d+$') { $map[$port] = $parts[4] }
        }
    }
    return $map
}

function Test-Port([int]$Port) {
    return (Get-ListeningMap).ContainsKey("$Port")
}

function Get-PortPids([int]$Port) {
    $map = Get-ListeningMap
    $key = "$Port"
    if ($map.ContainsKey($key)) { return @([int]$map[$key]) }
    return @()
}

function Test-ServiceProcAlive([string]$Name) {
    # 轻量存活检查：PID 文件 + Get-Process（实测 <10ms）
    $pidFile = Join-Path $PidsDir "$Name.pid"
    if (-not (Test-Path $pidFile)) { return $false }
    $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pidValue) { return $false }
    return [bool](Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue)
}

function Get-ServicesStateLight {
    # 高频轮询用轻量状态：两个 PID 进程都活=running；都无=stopped；其余不确定交给全量检查
    $b = Test-ServiceProcAlive "backend"
    $f = Test-ServiceProcAlive "frontend"
    if ($b -and $f) { return "running" }
    if (-not $b -and -not $f) { return "stopped" }
    return "unknown"
}

function Rotate-Logs([string]$Prefix) {
    if (-not (Test-Path $LogsDir)) { return }
    $files = Get-ChildItem (Join-Path $LogsDir "$Prefix-*.log") -ErrorAction SilentlyContinue | Sort-Object Name
    if ($files.Count -gt $MaxLogFiles) {
        $files | Select-Object -First ($files.Count - $MaxLogFiles) | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Get-LatestLog([string]$Prefix) {
    $files = Get-ChildItem (Join-Path $LogsDir "$Prefix-*.log") -ErrorAction SilentlyContinue | Sort-Object Name
    if ($files) { return $files[-1].Name }
    return $null
}

function Kill-ProcessTree([int]$ProcessId) {
    taskkill /PID $ProcessId /T /F 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

# ============ 启停逻辑（与 run.bat 共享约定） ============

function Start-Services {
    if (-not (Test-Path $PythonExe)) { throw "未找到 backend\.venv，请先运行 setup.bat" }
    if (-not (Test-Path (Join-Path $Root "node_modules"))) { throw "未找到 node_modules，请先运行 setup.bat" }

    New-Item -ItemType Directory -Force -Path $LogsDir, $PidsDir | Out-Null
    Rotate-Logs "backend"
    Rotate-Logs "frontend"
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    $backendLog = Join-Path $LogsDir "backend-$ts.log"
    $frontendLog = Join-Path $LogsDir "frontend-$ts.log"

    # 经临时 .cmd 启动（规避路径空格引号问题）；-WindowStyle Hidden 无黑窗口
    $backendLauncher = Join-Path $PidsDir "backend-launch.cmd"
    Set-Content -Path $backendLauncher -Encoding ASCII -Value @(
        "@echo off",
        "`"$PythonExe`" -m uvicorn app.main:app --port $BackendPort --reload > `"$backendLog`" 2>&1"
    )
    $frontendLauncher = Join-Path $PidsDir "frontend-launch.cmd"
    Set-Content -Path $frontendLauncher -Encoding ASCII -Value @(
        "@echo off",
        "cd /d `"$Root`"",
        "pnpm dev > `"$frontendLog`" 2>&1"
    )

    $backendProc = Start-Process cmd.exe -ArgumentList "/c", "`"$backendLauncher`"" `
        -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru
    $frontendProc = Start-Process cmd.exe -ArgumentList "/c", "`"$frontendLauncher`"" `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru

    Set-Content -Path (Join-Path $PidsDir "backend.pid") -Value $backendProc.Id -Encoding ASCII
    Set-Content -Path (Join-Path $PidsDir "frontend.pid") -Value $frontendProc.Id -Encoding ASCII

    return @{
        Backend = $backendProc
        Frontend = $frontendProc
        Logs = @{ Backend = $backendLog; Frontend = $frontendLog }
    }
}

function Stop-Services {
    $stopped = $false
    # 第 1 层：本进程记录的进程对象（GUI 场景）
    foreach ($proc in @($script:BackendProc, $script:FrontendProc)) {
        if ($null -ne $proc) {
            $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
            if ($alive) { if (Kill-ProcessTree $proc.Id) { $stopped = $true } }
        }
    }
    # 第 2 层：.vtt-pids/ PID 文件（跨工具/跨会话）
    foreach ($name in @("backend", "frontend")) {
        $pidFile = Join-Path $PidsDir "$name.pid"
        if (Test-Path $pidFile) {
            $pidValue = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($pidValue) {
                $proc = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
                if ($proc) { if (Kill-ProcessTree ([int]$pidValue)) { $stopped = $true } }
            }
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
    }
    # 第 3 层：端口兜底（进程被外部杀过、PID 失效等）
    foreach ($port in @($BackendPort, $FrontendPort)) {
        foreach ($portPid in (Get-PortPids $port)) {
            if (Kill-ProcessTree ([int]$portPid)) { $stopped = $true }
        }
    }
    $script:BackendProc = $null
    $script:FrontendProc = $null
    return $stopped
}

function Wait-Ready([int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Test-Port $BackendPort) -and (Test-Port $FrontendPort)) { return $true }
        Start-Sleep -Milliseconds 1000
    }
    return $false
}

function Get-ServicesState {
    $backend = Test-Port $BackendPort
    $frontend = Test-Port $FrontendPort
    if ($backend -and $frontend) { return "running" }
    if (-not $backend -and -not $frontend) { return "stopped" }
    return "partial"
}

# ============ 命令行模式（自动化验收 / 无托盘使用） ============

function Invoke-Action([string]$Name) {
    switch ($Name) {
        "start" {
            $state = Get-ServicesState
            if ($state -eq "running") { Write-Host "服务已在运行（$RepoUrl）"; Start-Process $RepoUrl; return 0 }
            try { $script:StartInfo = Start-Services } catch { Write-Host "[错误] $_"; return 1 }
            Write-Host "已启动，等待就绪（最多 $StartReadyTimeoutSec 秒）..."
            if (Wait-Ready $StartReadyTimeoutSec) {
                Write-Host "启动完成，打开浏览器 $RepoUrl"
                Start-Process $RepoUrl
                return 0
            }
            Write-Host "[失败] 启动未就绪，请查看 $(Get-LatestLog 'backend') 与 $(Get-LatestLog 'frontend')"
            return 1
        }
        "stop" {
            $killed = Stop-Services
            if ($killed) { Write-Host "已关闭" } else { Write-Host "已关闭（未发现运行中的服务）" }
            return 0
        }
        "restart" {
            [void](Invoke-Action "stop")
            Start-Sleep -Seconds 2
            return (Invoke-Action "start")
        }
        "status" {
            $backendPid = if (Test-Path (Join-Path $PidsDir "backend.pid")) { (Get-Content (Join-Path $PidsDir "backend.pid") | Select-Object -First 1) }
            $frontendPid = if (Test-Path (Join-Path $PidsDir "frontend.pid")) { (Get-Content (Join-Path $PidsDir "frontend.pid") | Select-Object -First 1) }
            if (Test-Port $BackendPort) {
                $suffix = if ($backendPid) { "（PID $backendPid）" } else { "" }
                Write-Host "后端：运行中$suffix"
            } else { Write-Host "后端：未运行" }
            if (Test-Port $FrontendPort) {
                $suffix = if ($frontendPid) { "（PID $frontendPid）" } else { "" }
                Write-Host "前端：运行中$suffix"
            } else { Write-Host "前端：未运行" }
            $log = Get-LatestLog "backend"
            if ($log) { Write-Host "最新日志：$log" }
            return 0
        }
        default { Write-Host "用法：控制台.ps1 -Action start|stop|restart|status"; return 2 }
    }
}

if ($Action) {
    exit (Invoke-Action $Action)
}

# ============ 托盘模式（GUI） ============

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# 单实例：命名 Mutex；已有实例时通知其弹出气泡并退出本实例
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "VideoToTextTrayConsole_Mutex", [ref]$createdNew)
$activateEventName = "VideoToTextTrayConsole_Activate"
if (-not $createdNew) {
    try {
        $evt = [System.Threading.EventWaitHandle]::OpenExisting($activateEventName)
        $evt.Set() | Out-Null
        $evt.Dispose()
    } catch { }
    exit 0
}
# 激活事件由 UI Timer 非阻塞收取（裸线程执行 scriptblock 无 Runspace 会崩，故不用 Thread）
$activateEvent = New-Object System.Threading.EventWaitHandle($false, [System.Threading.EventResetMode]::AutoReset, $activateEventName)

# 三色图标（System.Drawing 动态绘制：绿=运行中 / 黄=启动中或半就绪 / 灰=已停止 / 红=错误）
function New-StatusIcon([System.Drawing.Color]$Color) {
    $bmp = New-Object System.Drawing.Bitmap 32, 32
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)
    $brush = New-Object System.Drawing.SolidBrush $Color
    $g.FillEllipse($brush, 3, 3, 26, 26)
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(70, 70, 70)), 2
    $g.DrawEllipse($pen, 3, 3, 26, 26)
    $g.Dispose()
    return [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
}

$script:Icons = @{
    running = New-StatusIcon ([System.Drawing.Color]::FromArgb(46, 125, 50))
    starting = New-StatusIcon ([System.Drawing.Color]::FromArgb(249, 168, 37))
    stopped = New-StatusIcon ([System.Drawing.Color]::FromArgb(158, 158, 158))
    error = New-StatusIcon ([System.Drawing.Color]::FromArgb(198, 40, 40))
}

# 状态机：stopped | starting | running | partial | error
$script:Phase = "stopped"
$script:StartingSince = $null
$script:OpenBrowserOnReady = $false
$script:BackendProc = $null
$script:FrontendProc = $null
$script:TickCount = 0

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $script:Icons.stopped
$notify.Text = "视频转文字控制台"
$notify.Visible = $true

function Show-Balloon([string]$Message, [string]$Kind = "Info") {
    $icon = [System.Windows.Forms.ToolTipIcon]::$Kind
    $notify.ShowBalloonTip(4000, "视频转文字控制台", $Message, $icon)
}

function Update-Tray([string]$Phase, [string]$Balloon = "", [string]$BalloonKind = "Info") {
    $script:Phase = $Phase
    $iconKey = switch ($Phase) { "running" { "running" } "stopped" { "stopped" } "error" { "error" } default { "starting" } }
    $notify.Icon = $script:Icons[$iconKey]
    $textKey = switch ($Phase) { "running" { "运行中" } "stopped" { "已停止" } "error" { "出错，请查看日志" } default { "启动中…" } }
    $notify.Text = "视频转文字控制台 · $textKey"
    if ($Balloon) { Show-Balloon $Balloon $BalloonKind }
}

# 开机自启（首次询问一次；菜单可切换）
function Get-Prefs {
    if (Test-Path $PrefsFile) {
        try { return (Get-Content $PrefsFile -Raw | ConvertFrom-Json) } catch { }
    }
    return [pscustomobject]@{ autostartAsked = $false; autostart = $false }
}
function Save-Prefs($Prefs) {
    New-Item -ItemType Directory -Force -Path $PidsDir | Out-Null
    $Prefs | ConvertTo-Json | Set-Content -Path $PrefsFile -Encoding UTF8
}
function Get-AutostartLink {
    $startup = [Environment]::GetFolderPath("Startup")
    return (Join-Path $startup "视频转文字控制台.lnk")
}
function Set-Autostart([bool]$Enable) {
    $link = Get-AutostartLink
    if ($Enable) {
        $ws = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut($link)
        $lnk.TargetPath = (Join-Path $Root "控制台.bat")
        $lnk.WorkingDirectory = $Root
        $lnk.WindowStyle = 7
        $lnk.Save()
    } elseif (Test-Path $link) {
        Remove-Item $link -Force
    }
    $prefs = Get-Prefs
    $prefs.autostart = $Enable
    $prefs.autostartAsked = $true
    Save-Prefs $prefs
}

function Start-FromTray {
    $state = Get-ServicesState
    if ($state -eq "running") { Show-Balloon "服务已在运行（$RepoUrl）"; Update-Tray "running"; return }
    try {
        $info = Start-Services
        $script:BackendProc = $info.Backend
        $script:FrontendProc = $info.Frontend
        $script:StartingSince = Get-Date
        $script:OpenBrowserOnReady = $true
        Update-Tray "starting"
    } catch {
        Update-Tray "error" "启动失败：$_" "Error"
    }
}

function Stop-FromTray {
    $alive = Stop-Services
    $script:StartingSince = $null
    $script:OpenBrowserOnReady = $false
    if ($alive) { Update-Tray "stopped" "已停止" } else { Update-Tray "stopped" "未发现运行中的服务" }
}

# 右键菜单
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemStart = $menu.Items.Add("启动")
$itemStop = $menu.Items.Add("停止")
$itemRestart = $menu.Items.Add("重启")
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$itemLogs = $menu.Items.Add("查看日志")
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$itemAutostart = $menu.Items.Add("开机自启")
$itemAutostart.CheckOnClick = $true
$itemExit = $menu.Items.Add("退出")

$itemStart.Add_Click({ Start-FromTray })
$itemStop.Add_Click({ Stop-FromTray })
$itemRestart.Add_Click({
    Stop-FromTray
    Start-Sleep -Milliseconds 500
    Start-FromTray
})
$itemLogs.Add_Click({ Start-Process explorer.exe $LogsDir })
$itemAutostart.Add_Click({
    Set-Autostart $itemAutostart.Checked
    if ($itemAutostart.Checked) { Show-Balloon "已加入开机启动" } else { Show-Balloon "已取消开机启动" }
})
$itemExit.Add_Click({
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "是否同时停止前后端服务？`n`n是 = 停止服务并退出`n否 = 仅退出托盘（服务继续运行）",
        "退出控制台", "YesNoCancel", "Question")
    if ($answer -eq "Cancel") { return }
    if ($answer -eq "Yes") { [void](Stop-Services) }
    $notify.Visible = $false
    $notify.Dispose()
    try { $activateEvent.Dispose(); $mutex.ReleaseMutex() } catch { }
    [System.Windows.Forms.Application]::ExitThread()
})

$notify.ContextMenuStrip = $menu
# 菜单打开期间暂停轮询：状态检查与菜单高亮同跑在 UI 线程，暂停可彻底消除悬停卡顿
$menu.Add_Opened({ $timer.Stop() })
$menu.Add_Closed({ $timer.Start() })
$notify.Add_MouseDoubleClick({
    if ($script:Phase -eq "running") { Stop-FromTray } else { Start-FromTray }
})

# 状态轮询：2 秒
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({
    if ($activateEvent.WaitOne(0)) {
        Show-Balloon "控制台已在此处运行"
        Update-Tray $script:Phase
    }

    $backendAlive = $true
    if ($script:Phase -eq "starting") {
        foreach ($proc in @($script:BackendProc, $script:FrontendProc)) {
            if ($null -ne $proc -and -not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { $backendAlive = $false }
        }
    }

    # 状态检查分层（性能）：轻量（PID 文件+进程，<10ms）为常态；
    # 「启动中」每拍全量（等端口就绪）；其余每 8 拍（约 16 秒）全量一次，兜底外部启停（如 run.bat 手动起）
    $script:TickCount++
    if ($script:Phase -eq "starting" -or ($script:TickCount % 8 -eq 0)) {
        $state = Get-ServicesState
    } else {
        $state = Get-ServicesStateLight
        if ($state -eq "unknown") { $state = Get-ServicesState }
    }
    if ($script:Phase -eq "starting") {
        if (-not $backendAlive) {
            Update-Tray "error" "启动失败（进程已退出，可能端口被占用或依赖异常），请查看日志" "Error"
            $script:StartingSince = $null
            return
        }
        if ($state -eq "running") {
            $script:StartingSince = $null
            Update-Tray "running" "启动完成（$RepoUrl）"
            if ($script:OpenBrowserOnReady) { Start-Process $RepoUrl; $script:OpenBrowserOnReady = $false }
            return
        }
        if ($script:StartingSince -and ((Get-Date) - $script:StartingSince).TotalSeconds -gt $StartReadyTimeoutSec) {
            Update-Tray "error" "启动超时，请查看日志" "Error"
            $script:StartingSince = $null
        }
        return
    }
    switch ($state) {
        "running" { if ($script:Phase -ne "running") { Update-Tray "running" } }
        "stopped" { if ($script:Phase -ne "stopped") { Update-Tray "stopped" } }
        default { if ($script:Phase -ne "partial") { Update-Tray "starting" } }
    }
})
$timer.Start()

# 开机自启首次询问（延迟 5 秒，避免打断启动）
$askTimer = New-Object System.Windows.Forms.Timer
$askTimer.Interval = 5000
$askTimer.Add_Tick({
    $askTimer.Stop()
    $prefs = Get-Prefs
    if (-not $prefs.autostartAsked) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "是否将控制台加入开机启动？（可随时在右键菜单中切换）",
            "视频转文字控制台", "YesNo", "Question")
        Set-Autostart ($answer -eq "Yes")
        $itemAutostart.Checked = ($answer -eq "Yes")
    } else {
        $itemAutostart.Checked = [bool]$prefs.autostart
    }
    # 托盘启动时检测到服务已在跑：直接标记绿色，不尝试重启
    if ((Get-ServicesState) -eq "running") { Update-Tray "running" }
})
$askTimer.Start()

# 初始状态（服务已在跑则直接绿色）
if ((Get-ServicesState) -eq "running") { Update-Tray "running" } else { Update-Tray "stopped" }
# 启动气泡：Windows 11 默认把新图标收进折叠区，用气泡告知用户去哪里找
Show-Balloon "控制台已启动（图标在系统托盘区；Windows 11 若未看到，请点击任务栏 ^ 展开）"

$context = New-Object System.Windows.Forms.ApplicationContext
[System.Windows.Forms.Application]::Run($context)
