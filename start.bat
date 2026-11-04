@echo off
chcp 65001 >nul
title 喵艺一键启动
echo ================================================
echo    🐱 喵艺 AI - 一键启动
echo ================================================
echo.

set FRONTEND_PORT=5173
set BACKEND_PORT=8001
set URL=http://127.0.0.1:%FRONTEND_PORT%/

echo [1/2] 启动后端 (127.0.0.1:%BACKEND_PORT%)...
set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo 未找到 .venv，改用系统 Python（依赖请先 pip install -r requirements.txt）
    set PYTHON=python
)
start "喵艺后端" /min cmd /c "%PYTHON% server.py"

echo [2/2] 启动前端 (127.0.0.1:%FRONTEND_PORT%)...
if not exist "frontend\node_modules" (
    echo 前端依赖未安装，请先执行：cd frontend 然后 npm install
    pause
    exit /b 1
)
start "喵艺前端" /min /d "frontend" cmd /c "npm run dev"

echo.
echo 等待服务就绪（最多 60 秒）...
set /a tries=0

:wait_backend
set /a tries+=1
if %tries% gtr 60 (
    echo 后端启动超时，请查看「喵艺后端」窗口日志
    goto open_browser
)
netstat -ano | findstr ":%BACKEND_PORT%" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_backend
)
echo 后端已就绪 (端口 %BACKEND_PORT%)

set /a tries=0
:wait_frontend
set /a tries+=1
if %tries% gtr 60 (
    echo 前端启动超时（可能 5173 被占用自动换了端口），请查看「喵艺前端」窗口日志
    goto open_browser
)
netstat -ano | findstr ":%FRONTEND_PORT%" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_frontend
)
echo 前端已就绪 (端口 %FRONTEND_PORT%)

:open_browser
echo.
echo 正在打开浏览器: %URL%
rem 关键：用 127.0.0.1 直连 IPv4，绕开 localhost(指向 ::1) 的 IPv6 回环黑洞
start "" "%URL%"
echo.
echo 提示：
echo   若页面停在加载页，请按 Ctrl+Shift+Delete 清除浏览器缓存，
echo   或用无痕窗口（Ctrl+Shift+N）打开 %URL%
echo   两个服务窗口请保持运行，关闭即停止服务
echo.
pause
