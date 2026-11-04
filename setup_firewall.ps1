# 喵艺服务器防火墙配置脚本
# 请以管理员身份运行此脚本

Write-Host "=== 喵艺服务器防火墙配置 ===" -ForegroundColor Cyan
Write-Host ""

# 获取本机IP地址
$ipConfig = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.*" }
$localIP = $ipConfig | Select-Object -First 1 -ExpandProperty IPAddress

if (-not $localIP) {
    Write-Host "错误：无法获取本机IP地址" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "本机IP地址: $localIP" -ForegroundColor Green
Write-Host ""

# 添加入站规则
Write-Host "正在添加防火墙规则（TCP）..." -ForegroundColor Yellow

# 【修复】旧版只放行 8000 端口且与实例无关；实际后端跑在 8001（失败回退 8002~8100），
# 前端 vite 跑在 5173。程序级规则容易因 python/node 路径变化失效，改用端口级规则更稳。

# 规则1: 允许 TCP 8001（后端 API + WebSocket）
$rule1Name = "Miaoyi Backend TCP 8001"
$rule1Exists = Get-NetFirewallRule -DisplayName $rule1Name -ErrorAction SilentlyContinue

if ($rule1Exists) {
    Write-Host "  规则 '$rule1Name' 已存在，正在更新..." -ForegroundColor Gray
    Remove-NetFirewallRule -DisplayName $rule1Name
}

New-NetFirewallRule -DisplayName $rule1Name `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 8001 `
    -Action Allow `
    -Profile Any `
    -Description "允许手机等移动设备访问喵艺后端 API 与 WebSocket"

Write-Host "  规则1: TCP 8001 - 已添加" -ForegroundColor Green

# 规则2: 允许 TCP 5173（前端 vite 开发服务器）
$rule2Name = "Miaoyi Vite TCP 5173"
$rule2Exists = Get-NetFirewallRule -DisplayName $rule2Name -ErrorAction SilentlyContinue

if ($rule2Exists) {
    Write-Host "  规则 '$rule2Name' 已存在，正在更新..." -ForegroundColor Gray
    Remove-NetFirewallRule -DisplayName $rule2Name
}

New-NetFirewallRule -DisplayName $rule2Name `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 5173 `
    -Action Allow `
    -Profile Any `
    -Description "允许手机等设备访问喵艺前端开发服务器"

Write-Host "  规则2: TCP 5173 - 已添加" -ForegroundColor Green

# 规则3: 允许 TCP 8000-8100（后端端口回退区间）
$rule3Name = "Miaoyi Backend TCP 8000-8100"
$rule3Exists = Get-NetFirewallRule -DisplayName $rule3Name -ErrorAction SilentlyContinue

if ($rule3Exists) {
    Write-Host "  规则 '$rule3Name' 已存在，正在更新..." -ForegroundColor Gray
    Remove-NetFirewallRule -DisplayName $rule3Name
}

New-NetFirewallRule -DisplayName $rule3Name `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 8000-8100 `
    -Action Allow `
    -Profile Any `
    -Description "允许手机等设备访问喵艺后端回退端口区间"

Write-Host "  规则3: TCP 8000-8100 - 已添加" -ForegroundColor Green

Write-Host ""
Write-Host "=== 配置完成 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "手机端访问地址:" -ForegroundColor Yellow
Write-Host "  后端: http://$localIP`:8001" -ForegroundColor White
Write-Host "  前端(可选): http://$localIP`:5173" -ForegroundColor White
Write-Host ""
Write-Host "请确保手机和电脑在同一WiFi网络下" -ForegroundColor Yellow
Write-Host ""

pause
