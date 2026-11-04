Write-Host "=== 喵艺 AI 一键部署助手 ===" -ForegroundColor Cyan
Write-Host ""

# 检查必要文件
$requiredFiles = @(
    "server.py",
    "requirements.txt",
    "render.yaml",
    ".env.example"
)

$allExist = $true
foreach ($file in $requiredFiles) {
    if (Test-Path $file) {
        Write-Host "  [OK] $file" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $file" -ForegroundColor Red
        $allExist = $false
    }
}

Write-Host ""

# 检查 Git
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "  [OK] Git 已安装" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] Git 未安装" -ForegroundColor Red
    Write-Host "  请安装 Git: https://git-scm.com/download/win"
    $allExist = $false
}

Write-Host ""

# 检查 Python
if (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host "  [OK] Python 已安装: $(python --version)" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] Python 未安装" -ForegroundColor Red
    $allExist = $false
}

Write-Host ""

# 检查 Node.js
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "  [OK] Node.js 已安装: $(node --version)" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] Node.js 未安装" -ForegroundColor Red
    $allExist = $false
}

Write-Host ""

if ($allExist) {
    Write-Host "=== 环境检查通过 ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "下一步操作：" -ForegroundColor Yellow
    Write-Host "1. 初始化 Git 仓库：git init"
    Write-Host "2. 创建 .gitignore 文件（包含 .env, *.db 等敏感文件）"
    Write-Host "3. 推送到 GitHub 私有仓库"
    Write-Host "4. 在 Render.com 连接该仓库"
    Write-Host "5. 配置环境变量并部署"
} else {
    Write-Host "=== 环境检查未通过 ===" -ForegroundColor Red
    Write-Host "请安装缺失的依赖后重试"
}

Write-Host ""
Write-Host "详细部署说明请查看 DEPLOY.md" -ForegroundColor Cyan
Write-Host ""
pause
