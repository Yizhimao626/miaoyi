@echo off
chcp 65001 >nul
echo ================================================
echo    🐱 喵艺 AI 系统 - 一键安装脚本
echo ================================================
echo.

set PYTHON_EXE=python
set VENV_DIR=.venv
set REQUIREMENTS_FILE=requirements.txt

echo [1/4] 检查 Python 环境...
%PYTHON_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)
echo ✅ Python 环境就绪

echo.
echo [2/4] 创建虚拟环境...
if not exist %VENV_DIR% (
    %PYTHON_EXE% -m venv %VENV_DIR%
    if %errorlevel% neq 0 (
        echo ❌ 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo ✅ 虚拟环境创建成功
) else (
    echo ⏭️  虚拟环境已存在，跳过
)

echo.
echo [3/4] 激活虚拟环境并安装依赖...
call %VENV_DIR%\Scripts\activate.bat

echo 正在安装核心依赖...
pip install -r %REQUIREMENTS_FILE%
if %errorlevel% neq 0 (
    echo ❌ 依赖安装失败
    pause
    exit /b 1
)
echo ✅ 依赖安装成功

echo.
echo [4/4] 初始化配置文件...
if not exist config.yaml (
    copy config.example.yaml config.yaml >nul 2>&1
    echo ⚠️  请编辑 config.yaml 配置模型与语音
)

if not exist .env (
    copy .env.example .env >nul 2>&1
    echo ⚠️  请编辑 .env 配置环境变量
)

echo.
echo ================================================
echo    🎉 安装完成！
echo ================================================
echo.
echo 使用方法：
echo   1. 编辑 .env，填入 JWT_SECRET_KEY（必填）与可选的模型 API Key
echo   2. 运行: .venv\Scripts\python.exe server.py
echo   3. 打开浏览器访问: http://127.0.0.1:8001
echo.
echo 可选功能安装：
echo   - 离线语音识别: pip install vosk
echo   - 高级语音识别: pip install openai-whisper
echo   - 本地TTS: pip install pyttsx3
echo.
pause
