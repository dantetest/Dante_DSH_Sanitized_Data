# 数据隐站 · Windows 打包脚本
#   powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1
#
# 产物：
#   dist\Sanitized_Data\                     绿色版目录（可直接压缩分发）
#   dist_installer\Sanitized_Data_Setup_x64.exe   安装包（若装了 Inno Setup）
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "`n[1/6] 检查构建依赖" -ForegroundColor Cyan
$need = @()
python -c "import PyInstaller" 2>$null; if ($LASTEXITCODE -ne 0) { $need += 'pyinstaller' }
python -c "import webview"     2>$null; if ($LASTEXITCODE -ne 0) { $need += 'pywebview' }
# 条码/二维码识别引擎（P0 起必需，Apache-2.0，离线 wheel）
python -c "import zxingcpp"    2>$null; if ($LASTEXITCODE -ne 0) { $need += 'zxing-cpp' }
# 正射 TIF 中性化/大图（P0 起建议安装；缺失时自动降级为 Pillow + warning）
python -c "import rasterio, pyproj" 2>$null; if ($LASTEXITCODE -ne 0) {
    Write-Host "  ! 未安装 rasterio/pyproj：TIF 大图/位深/中性化会降级（见 docs/impl/tiff.md）" -ForegroundColor Yellow
}
if ($need.Count -gt 0) {
    Write-Host "  安装：$($need -join ', ')"
    python -m pip install --quiet --disable-pip-version-check @need
}
python -m PyInstaller --version

Write-Host "`n[2/6] 拉取运行时安装包（WebView2 / VC++）" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'fetch_redist.ps1')

Write-Host "`n[3/6] PyInstaller 打包（onedir）" -ForegroundColor Cyan
& python -m PyInstaller --clean --noconfirm (Join-Path $PSScriptRoot 'app.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败' }

Write-Host "`n[4/6] 补 VC++ 运行库 DLL（免装 VC++ 也能跑）" -ForegroundColor Cyan
$dist = Join-Path $Root 'dist\Sanitized_Data'
foreach ($dll in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    $src = Join-Path $env:SystemRoot "System32\$dll"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $dist $dll) -Force
        "  + $dll"
    } else {
        "  ! 未找到 $dll（系统里没有，安装时由 VC++ 安装包补齐）"
    }
}

Write-Host "`n[5/6] 自检：直接跑一次引擎" -ForegroundColor Cyan
$exe = Join-Path $dist 'Sanitized_Data.exe'
if (Test-Path $exe) {
    $p = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 8
    try {
        $h = (Invoke-WebRequest 'http://127.0.0.1:8760/api/health' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        "  ✓ 服务已启动  OCR=$($h.engine.ocr)  解析器=$($h.engine.parsers -join ',')"
        # 打包关键自检：识别引擎与图像扫描器必须真的被收进包里
        "  扫描器=$($h.engine.scanners -join ',')  启用=$($h.engine.scan_enabled)"
        if (-not ($h.engine.scanners -contains 'barcode')) {
            Write-Host "  ! 警告：条形码扫描器未加载（检查 zxingcpp 是否被 collect_all 收进包）" -ForegroundColor Yellow
        }
        if ($h.engine.ocr -ne $true) {
            Write-Host "  ! 警告：OCR 未就绪（检查 rapidocr/onnxruntime 模型是否打进包）" -ForegroundColor Yellow
        }
    } catch {
        "  ! 自检请求失败（可能窗口已打开但端口不同，检查 data\runtime.json）"
    }
    Get-Process -Id $p.Id -ErrorAction SilentlyContinue | Stop-Process -Force
}

Write-Host "`n[6/6] 打安装包（需要 Inno Setup 6）" -ForegroundColor Cyan
$iscc = @(
    "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    & $iscc (Join-Path $PSScriptRoot 'installer.iss')
    if ($LASTEXITCODE -eq 0) {
        "  ✓ 安装包：dist_installer\"
        Get-ChildItem (Join-Path $Root 'dist_installer') | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
    }
} else {
    "  ! 没找到 Inno Setup 6，跳过安装包。绿色版在 dist\Sanitized_Data\"
    "    下载：https://jrsoftware.org/isdl.php"
}

Write-Host "`n[7/7] 打绿色版压缩包" -ForegroundColor Cyan
$zip = Join-Path $Root 'out\desktop\Sanitized_Data-1.0.0-win64-portable.zip'
New-Item -ItemType Directory -Force -Path (Split-Path $zip) | Out-Null
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Root 'dist\Sanitized_Data') -DestinationPath $zip -CompressionLevel Optimal
"  ✓ $zip  $([math]::Round((Get-Item $zip).Length/1MB,1)) MB"

Write-Host "`n完成。" -ForegroundColor Green
