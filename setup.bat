@echo off

setlocal

cd /d "%~dp0"



echo ============================================

echo   video-to-text 首次配置（setup.bat）

echo ============================================



echo.

echo [1/6] 检查 Python...

where py >nul 2>nul

if not errorlevel 1 (

    set "PYEXE=py -3"

) else (

    where python >nul 2>nul

    if errorlevel 1 goto :error_python

    set "PYEXE=python"

)

%PYEXE% --version

if errorlevel 1 goto :error_python



echo.

echo [2/6] 检查 Node.js 与 pnpm...

where node >nul 2>nul

if errorlevel 1 goto :error_node

node --version

where pnpm >nul 2>nul

if errorlevel 1 goto :error_pnpm

call pnpm --version



echo.

echo [3/6] 创建 Python 虚拟环境（backend\.venv）...

if exist "backend\.venv\Scripts\python.exe" (

    echo     已存在，跳过。

) else (

    %PYEXE% -m venv "backend\.venv"

    if errorlevel 1 goto :error_venv

    echo     创建完成。

)



echo.

echo [4/6] 安装后端依赖（backend\requirements.txt）...

"backend\.venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt"

if errorlevel 1 goto :error_pip



echo.

echo [5/6] 安装前端依赖（pnpm install）...

call pnpm install

if errorlevel 1 goto :error_pnpm_install



echo.

echo [6/6] 复制环境变量模板（已存在的文件不覆盖，保护你填过的 Key）...

if not exist "backend\.env.local" (

    copy /y "backend\.env.example" "backend\.env.local" >nul

    echo     已生成 backend\.env.local

) else (

    echo     backend\.env.local 已存在，保留原值。

)

if not exist ".env.local" (

    copy /y ".env.example" ".env.local" >nul

    echo     已生成 .env.local

) else (

    echo     .env.local 已存在，保留原值。

)



echo.

echo ============================================

echo   配置完成：双击 控制台.bat 启动托盘控制台，或双击 run.bat 用命令行脚本

echo ============================================

echo.

echo 可选配置（不填也能用本地转写）：

echo   backend\.env.local 中填写：

echo     DEEPSEEK_API_KEY   —— AI 总结（阶段三，必需才生效）

echo     CLOUD_ASR_API_KEY  —— 云端转写（首页选「云端」时用）

echo   填写后运行 run.bat restart 生效。

echo.

pause

exit /b 0



:error_python

echo.

echo [错误] 未找到 Python。请安装 Python 3.11+：

echo        https://www.python.org/downloads/（安装时勾选 py launcher）

echo        或使用 Microsoft Store 安装后重开终端再运行本脚本。

pause

exit /b 1



:error_node

echo.

echo [错误] 未找到 Node.js。请安装 Node.js 18+：

echo        https://nodejs.org/（LTS 版本即可），安装后重开终端再运行本脚本。

pause

exit /b 1



:error_pnpm

echo.

echo [错误] 未找到 pnpm。请安装后重试：

echo        npm install -g pnpm

echo        或：corepack enable

pause

exit /b 1



:error_venv

echo.

echo [错误] 虚拟环境创建失败。请检查磁盘空间与 Python 安装完整性。

pause

exit /b 1



:error_pip

echo.

echo [错误] 后端依赖安装失败。请检查网络（可配置 pip 镜像后重试）：

echo        python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

pause

exit /b 1



:error_pnpm_install

echo.

echo [错误] 前端依赖安装失败。请检查网络后重试，或运行：

echo        pnpm config set registry https://registry.npmmirror.com

pause

exit /b 1

