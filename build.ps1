param(
    [string]$Action = "deploy"
)

Write-Host "=== 喵艺 AI 部署脚本 ===" -ForegroundColor Cyan
Write-Host ""

switch ($Action) {
    "init" {
        Write-Host "初始化 Git 仓库..."
        if (-not (Test-Path ".git")) {
            git init
            git add .
            git commit -m "feat: 喵艺 AI 初始版本"
            Write-Host "Git 仓库已初始化" -ForegroundColor Green
        } else {
            Write-Host "Git 仓库已存在" -ForegroundColor Yellow
        }
    }
    
    "build" {
        Write-Host "构建前端..."
        Set-Location frontend
        npm install
        npm run build
        Copy-Item -Recurse -Force dist\* android\app\src\main\assets\public\
        Set-Location ..
        Write-Host "前端构建完成" -ForegroundColor Green
    }
    
    "apk" {
        Write-Host "构建 APK..."
        Set-Location frontend\android
        .\gradlew.bat assembleDebug
        Set-Location ..\..
        Write-Host "APK 构建完成" -ForegroundColor Green
        Write-Host "APK 位置: frontend\android\app\build\outputs\apk\debug\app-debug.apk"
    }
    
    "deploy" {
        Write-Host "检查部署准备..."
        
        $checks = @(
            @{Name = "Git"; Command = "git"},
            @{Name = "Python"; Command = "python"},
            @{Name = "Node.js"; Command = "node"}
        )
        
        foreach ($check in $checks) {
            if (Get-Command $check.Command -ErrorAction SilentlyContinue) {
                Write-Host "  [OK] $($check.Name)" -ForegroundColor Green
            } else {
                Write-Host "  [MISSING] $($check.Name)" -ForegroundColor Red
            }
        }
        
        Write-Host ""
        Write-Host "=== 下一步 ===" -ForegroundColor Yellow
        Write-Host "1. 运行 .\build.ps1 build 构建前端"
        Write-Host "2. 运行 .\build.ps1 apk 构建 APK"
        Write-Host "3. 推送代码到 GitHub"
        Write-Host "4. 在 Render.com 部署"
    }
    
    default {
        Write-Host "未知操作: $Action" -ForegroundColor Red
        Write-Host "可用操作: init, build, apk, deploy"
    }
}

Write-Host ""
Write-Host "详细说明请查看 DEPLOY.md" -ForegroundColor Cyan
