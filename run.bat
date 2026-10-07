@echo off

setlocal

cd /d "%~dp0"



set "ACTION=%~1"

rem 无参数（双击）＝直接启动；查看用法请运行 run.bat help
if "%ACTION%"=="" goto :start

if /i "%ACTION%"=="start" goto :start

if /i "%ACTION%"=="stop" goto :stop

if /i "%ACTION%"=="restart" goto :restart

if /i "%ACTION%"=="status" goto :status

goto :usage



:usage

echo 用法：

echo   双击 run.bat（或 run.bat start）  启动前后端并打开浏览器

echo   run.bat stop      关闭前后端

echo   run.bat restart   重启

echo   run.bat status    查看运行状态
echo   run.bat help      显示本帮助

exit /b 0



rem ============ start ============



:start

if not exist "backend\.venv\Scripts\python.exe" (

    echo [错误] 未找到 backend\.venv，请先运行 setup.bat
    pause

    exit /b 1

)

if not exist "node_modules" (

    echo [错误] 未找到 node_modules，请先运行 setup.bat
    pause

    exit /b 1

)



set "PORT_BUSY=0"

call :check_port 8000

if errorlevel 1 set "PORT_BUSY=1"

call :check_port 3000

if errorlevel 1 set "PORT_BUSY=1"

if "%PORT_BUSY%"=="1" (

    echo [提示] 端口 8000 或 3000 已被占用。

    choice /c YN /m "Y=停止现有进程后继续启动 / N=退出" /t 15 /d N

    if errorlevel 2 (

        echo 已取消。可运行 run.bat status 查看详情。
        pause

        exit /b 1

    )

    call :stop_impl

    ping -n 3 127.0.0.1 >nul

)



if not exist "logs" mkdir "logs"

if not exist ".vtt-pids" mkdir ".vtt-pids"

call :prune_logs backend

call :prune_logs frontend

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%i"



echo [启动] 后端 uvicorn（--reload，端口 8000）...

start "vtt-backend" /D "backend" cmd /c "title vtt-backend && .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload > ..\logs\backend-%TS%.log 2>&1"

echo [启动] 前端 pnpm dev（端口 3000）...

start "vtt-frontend" cmd /c "title vtt-frontend && pnpm dev > logs\frontend-%TS%.log 2>&1"



ping -n 3 127.0.0.1 >nul

call :write_pid backend "uvicorn"

call :write_pid frontend "next dev"



echo [等待] 前端就绪（最多 30 秒）...

set /a TRIES=0

:wait_front

set /a TRIES+=1

if %TRIES% gtr 30 goto :wait_timeout

curl -s -o nul http://localhost:3000

if not errorlevel 1 goto :front_ready

ping -n 2 127.0.0.1 >nul

goto :wait_front



:wait_timeout

echo [警告] 30 秒内前端未就绪，请查看 logs\frontend-%TS%.log 排查。
pause

start "" http://localhost:3000

exit /b 1



:front_ready

start "" http://localhost:3000

echo.

echo 启动完成，浏览器已打开 http://localhost:3000

echo 关闭请运行 run.bat stop

echo 实时日志：logs\backend-%TS%.log / logs\frontend-%TS%.log

echo PID 信息：.vtt-pids\backend.pid / .vtt-pids\frontend.pid

exit /b 0



rem ============ stop ============



:stop

call :stop_impl

exit /b 0



:stop_impl

rem 顺序：① PID 文件（校验映像名防复用误杀）→ ② 窗口标题 → ③ 端口兜底

set "STOPPED=0"

call :stop_one backend

call :stop_one frontend

call :kill_by_title "vtt-backend"

call :kill_by_title "vtt-frontend"

call :kill_port 8000

call :kill_port 3000

echo 已关闭。

exit /b 0



:stop_one

rem %1 = 名称；读 .vtt-pids\%1.pid，确认进程存在且为自家进程后树杀

if not exist ".vtt-pids\%~1.pid" exit /b 0

set /p PID_VALUE=<".vtt-pids\%~1.pid"

if not defined PID_VALUE exit /b 0

set "IMAGE="

for /f "tokens=1 delims=," %%m in ('tasklist /FI "PID eq %PID_VALUE%" /FO CSV /NH 2^>nul') do set "IMAGE=%%m"

if not defined IMAGE exit /b 0

if /i "%IMAGE%"=="" exit /b 0

taskkill /PID %PID_VALUE% /T /F >nul 2>&1

if not errorlevel 1 (

    echo     %~1：已停止（PID %PID_VALUE%）

) else (

    echo     %~1：PID %PID_VALUE% 终止失败，回退窗口标题/端口兜底。

)

del ".vtt-pids\%~1.pid" >nul 2>&1

exit /b 0



:kill_by_title

rem %1 = 窗口标题前缀；存在匹配窗口才计为执行过

tasklist /FI "WINDOWTITLE eq %~1*" 2>nul | findstr /i "cmd" >nul 2>&1

if errorlevel 1 exit /b 0

taskkill /FI "WINDOWTITLE eq %~1*" /T /F >nul 2>&1

exit /b 0



:kill_port

rem %1 = 端口；LISTENING 进程直接树杀（自用场景：这两个端口归本应用管理）

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%~1 " ^| findstr "LISTENING" 2^>nul') do (

    taskkill /PID %%p /T /F >nul 2>&1

)

exit /b 0



rem ============ restart ============



:restart

echo [重启] 先停止...

call :stop_impl

ping -n 3 127.0.0.1 >nul

echo [重启] 再启动...

goto :start



rem ============ status ============



:status

call :show_status backend 8000

call :show_status frontend 3000

exit /b 0



:show_status

rem %1 = 名称，%2 = 端口

set "STATE=未运行"

set "DETAIL="

set "PID_FILE=.vtt-pids\%~1.pid"

if exist "%PID_FILE%" (

    set /p PID_VALUE=<"%PID_FILE%"

)

if defined PID_VALUE (

    tasklist /FI "PID eq %PID_VALUE%" 2>nul | findstr /i "cmd python node" >nul 2>&1

    if not errorlevel 1 (

        set "STATE=运行中"

        set "DETAIL=PID %PID_VALUE%"

    )

)

if "%STATE%"=="未运行" (

    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%~2 " ^| findstr "LISTENING" 2^>nul') do (

        set "STATE=运行中"

        set "DETAIL=PID %%p（按端口 %~2 判定）"

    )

)

set "LATEST_LOG="

for /f "delims=" %%f in ('dir /b /o:n "logs\%~1-*.log" 2^>nul') do set "LATEST_LOG=%%f"

if defined LATEST_LOG (

    if defined DETAIL set "DETAIL=%DETAIL% ｜ "

    set "DETAIL=%DETAIL%最新日志 %LATEST_LOG%"

)

echo %~1：%STATE%%DETAIL%

set "PID_VALUE="

exit /b 0



rem ============ 子例程 ============



:check_port

rem %1 = 端口；LISTENING 存在则 errorlevel 1

netstat -ano | findstr ":%~1 " | findstr "LISTENING" >nul 2>&1

if errorlevel 1 exit /b 0

exit /b 1



:kill_port

rem %1 = 端口；杀掉 LISTENING 进程（自用场景：这两个端口归本应用管理）

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%~1 " ^| findstr "LISTENING" 2^>nul') do (

    taskkill /PID %%p /T /F >nul 2>&1

)

exit /b 0



:write_pid
rem %1 = 名称，%2 = 命令行匹配关键字；按 Win32_Process.CommandLine 反查进程，
rem 优先取其父 cmd 进程（stop 树杀可连同 uvicorn/next 一起结束）
for /f usebackq %%p in (`powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*%~2*' } | Select-Object -First 1; if ($p) { $parent = Get-Process -Id $p.ParentProcessId -ErrorAction SilentlyContinue; if ($parent -and $parent.ProcessName -eq 'cmd') { $parent.Id } else { $p.ProcessId } }"`) do (
    echo %%p> ".vtt-pids\%~1.pid"
    echo     %~1 PID=%%p
)
exit /b 0



:prune_logs

rem %1 = 名称；logs\%1-*.log 超过 20 个时删最旧（文件名时间戳字典序即时间序）

setlocal enabledelayedexpansion

set /a COUNT=0

for /f "delims=" %%f in ('dir /b /o:n "logs\%~1-*.log" 2^>nul') do (

    set /a COUNT+=1

    set "LOGFILE_!COUNT!=%%f"

)

if %COUNT% gtr 20 (

    set /a KEEP_FROM=%COUNT%-19

    for /l %%i in (1,1,%KEEP_FROM%) do (

        del "logs\!LOGFILE_%%i!" >nul 2>&1

    )

    echo     日志轮转：清理 %~1 最旧 !KEEP_FROM! 个日志

)

endlocal

exit /b 0



rem ============ TODO ============

rem Mac/Linux 版（run.sh / setup.sh）：nohup + PID 文件（.vtt-pids/）管理后台进程，逻辑与本文件一致。

rem 暂未实现（用户平台为 Windows）；需要时按本文件逻辑翻译即可。