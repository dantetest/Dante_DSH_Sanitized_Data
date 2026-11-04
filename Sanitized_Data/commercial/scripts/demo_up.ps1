<#
.SYNOPSIS
  数据隐站 L3 付费解锁 · Windows 一键演示（商业服务 mock + 本机购买页）。

.DESCRIPTION
  自动完成：
    1) 缺测试密钥时调用 commercial/scripts/gen_test_keys.py 生成（Ed25519 test-2026 + 两组 RSA）；
    2) 起「商业服务」：commercial/scripts/run_dev.py --provider mock（默认端口 8788）；
    3) 起「本机 app」：app/server/run.py（默认端口 8799，--no-browser --no-ocr），
       env 指向仓库内临时数据目录 + 测试公钥 + 商业服务地址；
    4) 等两个服务 /healthz、/api/health 就绪，并确认 /purchase 可访问；
    5) 打开浏览器到 http://127.0.0.1:<AppPort>/purchase；
    6) 打印 PID / 日志位置 / 停止方法；本窗口 Ctrl+C 时自动停止两个子进程。

  演示步骤：购买页选「专业版」→「支付宝扫码」→ 点「演示：模拟支付」→
  页面轮询到 paid → 自动调用本机 /api/license/import → 显示「已解锁」。

  重要（测试密钥 vs 生产）：
    本脚本用仓库内 **L3 测试密钥**（commercial/var/test_keys，gitignore）：
      * 商业服务：LICENSE_PRIVATE_KEY_PATH=测试 license 私钥，LICENSE_KEY_ID=test-2026；
      * 本机 app：YINZHAN_PUBLIC_KEYS_PATH=测试公钥 JSON（覆盖/合并产品内置公钥），
        因此测试密钥签发的 license 才能导入解锁。
    真实生产演示：商业服务改成生产私钥（LICENSE_KEY_ID=prod-2026），
    本机 app **不要**设置 YINZHAN_PUBLIC_KEYS_PATH（用安装包内置 prod-2026 公钥）。

.PARAMETER Python
  指定 Python 解释器；缺省依次找 $env:DSH_PYTHON、DSH 自带运行时、PATH 里的 python。
.PARAMETER MockPort
  商业服务端口（默认 8788）。
.PARAMETER AppPort
  本机 app 端口（默认 8799；被占用时会提示换端口）。
.PARAMETER NoBrowser
  不自动打开浏览器（CI/自动化演示用）。
.PARAMETER ValidateOnly
  只做环境/密钥/端口/文件检查后退出 0（不启动服务），用于验证脚本可用性。
.PARAMETER DurationSeconds
  >0 时：启动并检查就绪后等待 N 秒自动停止（自动化冒烟用）；0=一直运行到 Ctrl+C。
.PARAMETER KeepData
  保留上次演示的数据目录（默认每次清空，保证初始状态为未授权、可直接演示）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1 -ValidateOnly

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1 -NoBrowser -DurationSeconds 20
#>
[CmdletBinding()]
param(
    [string]$Python = '',
    [int]$MockPort = 8788,
    [int]$AppPort = 8799,
    [switch]$NoBrowser,
    [switch]$ValidateOnly,
    [int]$DurationSeconds = 0,
    [switch]$KeepData
)

$ErrorActionPreference = 'Stop'
$script:MockProc = $null
$script:AppProc = $null
$script:ExitCode = 0
$script:PidFile = $null

function Write-Step([string]$Text) {
    Write-Host "[*] $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Write-WarnLine([string]$Text) {
    Write-Host "[!] $Text" -ForegroundColor Yellow
}

function Find-Python {
    param([string]$Explicit)
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($Explicit) { $candidates.Add($Explicit) }
    if ($env:DSH_PYTHON) { $candidates.Add($env:DSH_PYTHON) }
    $candidates.Add('C:\Users\Dante\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe')
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "找不到可用的 Python 解释器。请用 -Python <path\to\python.exe> 指定（需已安装 fastapi/uvicorn/cryptography）。"
}

function Test-PortFree {
    param([int]$Port)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) { try { $listener.Stop() } catch { } }
    }
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90,
        [System.Diagnostics.Process]$Proc = $null,
        [string]$ErrLog = ''
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = '尚未响应'
    while ((Get-Date) -lt $deadline) {
        if ($Proc -and $Proc.HasExited) {
            $tail = ''
            if ($ErrLog -and (Test-Path -LiteralPath $ErrLog)) {
                $tail = (Get-Content -LiteralPath $ErrLog -Tail 20 -ErrorAction SilentlyContinue) -join "`n"
            }
            throw "进程已提前退出（exit=$($Proc.ExitCode)）。错误日志尾部：`n$tail"
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) { return $response }
            $lastError = "HTTP $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 400
    }
    throw "等待 $Url 就绪超时（${TimeoutSeconds}s）：$lastError"
}

function Reset-DemoDir {
    param([string]$Path, [string]$VarRoot)
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($VarRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理 commercial/var 之外的目录：$full"
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $full -Force | Out-Null
    return $full
}

function Show-LogTail {
    param([string]$Path, [int]$Lines = 20)
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Write-Host "--- $Path （尾部 $Lines 行） ---" -ForegroundColor DarkGray
        Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
}

function Remove-CaseDuplicateEnvVars {
    # Windows 环境变量本身大小写不敏感，但受限环境（某些终端/CI/DSH 沙箱）的进程环境块里
    # 可能同时存在 NO_PROXY 与 no_proxy（HTTP_PROXY/http_proxy 等）。Windows PowerShell 5.1
    # 的 Start-Process 复制这种环境块时会抛：
    #   "Item has already been added. Key in dictionary: 'NO_PROXY' Key being added: 'no_proxy'"
    # 这里先按「大小写不敏感」去重（保留首次出现的名字），保证后面能正常起子进程。
    $lines = @()
    try { $lines = @(& cmd.exe /c set 2>$null) } catch { return 0 }
    $seen = @{}
    $removed = 0
    foreach ($line in $lines) {
        $text = [string]$line
        if (-not $text) { continue }
        $idx = $text.IndexOf('=')
        if ($idx -lt 1) { continue }
        $name = $text.Substring(0, $idx)
        $lower = $name.ToLowerInvariant()
        if ($seen.ContainsKey($lower)) {
            try {
                [System.Environment]::SetEnvironmentVariable($name, $null)
                $removed++
            }
            catch { }
        }
        else {
            $seen[$lower] = $name
        }
    }
    return $removed
}

# ─────────────────────────────────────────────────────────────────────────────
# 0) 路径与解释器
# ─────────────────────────────────────────────────────────────────────────────
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$CommercialDir = Split-Path -Parent $ScriptDir
$ProjectRoot = Split-Path -Parent $CommercialDir
$RunDev = Join-Path $CommercialDir 'scripts\run_dev.py'
$GenKeys = Join-Path $ScriptDir 'gen_test_keys.py'
$AppRun = Join-Path $ProjectRoot 'app\server\run.py'
$PurchaseHtml = Join-Path $ProjectRoot 'app\web\purchase.html'
$KeysDir = Join-Path $CommercialDir 'var\test_keys'
$VarRoot = Join-Path $CommercialDir 'var'
$LogsDir = Join-Path $VarRoot 'demo_logs'
$CommercialDataDir = Join-Path $VarRoot 'demo_commercial_data'
$AppDataDir = Join-Path $VarRoot 'demo_app_data'

Write-Host '============================================================' -ForegroundColor White
Write-Host ' 数据隐站 L3 付费解锁 · 一键演示（mock 支付）' -ForegroundColor White
Write-Host '============================================================' -ForegroundColor White

$Py = Find-Python -Explicit $Python
Write-Step "Python     : $Py"
$PyVersion = (& $Py -X utf8 -c "import sys; print(sys.version.split()[0])" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "Python 无法运行：$PyVersion" }
& $Py -X utf8 -c "import fastapi, uvicorn, cryptography" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Python 依赖缺失：需要 fastapi / uvicorn / cryptography。请先运行：`"$Py`" -m pip install -r commercial\requirements.txt"
}
Write-Ok "Python 版本 $PyVersion；fastapi/uvicorn/cryptography 可用"

foreach ($required in @($RunDev, $GenKeys, $AppRun, $PurchaseHtml)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "缺少文件：$required（仓库不完整？）" }
}
Write-Ok '脚本与页面文件齐全（run_dev.py / gen_test_keys.py / run.py / purchase.html）'

# 受限终端/沙箱常见坑：进程环境块里同时有 NO_PROXY 与 no_proxy 等大小写重复项，
# Windows PowerShell 5.1 的 Start-Process 会因此直接报 "Item has already been added"。
$envDupRemoved = Remove-CaseDuplicateEnvVars
if ($envDupRemoved -gt 0) {
    Write-WarnLine "已清理进程环境变量中的大小写重复项 $envDupRemoved 个（Windows PowerShell Start-Process 兼容处理）"
}

# ─────────────────────────────────────────────────────────────────────────────
# 1) 测试密钥（缺则生成；幂等，不覆盖已有）
# ─────────────────────────────────────────────────────────────────────────────
$keyFiles = @{
    'license 私钥'   = (Join-Path $KeysDir 'license_signing_ed25519.pem')
    'license 公钥表' = (Join-Path $KeysDir 'license_public_keys_test.json')
    '应用 RSA 私钥'  = (Join-Path $KeysDir 'app_private.pem')
    '应用 RSA 公钥'  = (Join-Path $KeysDir 'app_public.pem')
    '支付宝 RSA 私钥' = (Join-Path $KeysDir 'alipay_private.pem')
    '支付宝 RSA 公钥' = (Join-Path $KeysDir 'alipay_public.pem')
}
$missingKeys = @($keyFiles.GetEnumerator() | Where-Object { -not (Test-Path -LiteralPath $_.Value) })
if ($missingKeys.Count -gt 0) {
    Write-Step "缺少 $($missingKeys.Count) 个测试密钥文件，调用 gen_test_keys.py 生成…"
    & $Py -X utf8 $GenKeys
    if ($LASTEXITCODE -ne 0) { throw "生成测试密钥失败（exit=$LASTEXITCODE）" }
    $stillMissing = @($keyFiles.GetEnumerator() | Where-Object { -not (Test-Path -LiteralPath $_.Value) })
    if ($stillMissing.Count -gt 0) { throw "生成后仍缺少：$($stillMissing.Key -join ', ')" }
    Write-Ok '测试密钥已生成（commercial/var/test_keys，已 gitignore）'
}
else {
    Write-Ok "测试密钥已存在（复用）：$KeysDir"
}
$LicenseKeyPath = $keyFiles['license 私钥']
$PublicKeysJson = $keyFiles['license 公钥表']

# ─────────────────────────────────────────────────────────────────────────────
# 2) 端口/参数检查
# ─────────────────────────────────────────────────────────────────────────────
if ($MockPort -eq $AppPort) { throw "MockPort 与 AppPort 不能相同（都是 $MockPort）" }
if ($MockPort -lt 1024 -or $MockPort -gt 65535) { throw "MockPort 非法：$MockPort" }
if ($AppPort -lt 1024 -or $AppPort -gt 65535) { throw "AppPort 非法：$AppPort" }
if (-not (Test-PortFree -Port $MockPort)) {
    throw "商业服务端口 $MockPort 已被占用。请关闭占用进程，或用 -MockPort 换一个端口（如 8790）。"
}
if (-not (Test-PortFree -Port $AppPort)) {
    throw "本机 app 端口 $AppPort 已被占用。请关闭占用进程，或用 -AppPort 换一个端口（如 8801）。"
}
Write-Ok "端口可用：商业服务 $MockPort / 本机 app $AppPort"

if ($ValidateOnly) {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor White
    Write-Host ' 校验通过（-ValidateOnly，未启动任何服务）' -ForegroundColor Green
    Write-Host '============================================================' -ForegroundColor White
    Write-Host "  正式演示命令：powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Write-Host "  本机购买页  ：http://127.0.0.1:$AppPort/purchase"
    Write-Host "  商业服务    ：http://127.0.0.1:$MockPort/buy.html"
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# 3) 准备数据目录/日志目录（默认清空，保证初始未授权状态）
# ─────────────────────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Path $VarRoot -Force | Out-Null
$VarRootFull = (Resolve-Path -LiteralPath $VarRoot).Path
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
Get-ChildItem -LiteralPath $LogsDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like '*.log' } | Remove-Item -Force -ErrorAction SilentlyContinue
if (-not $KeepData) {
    $CommercialDataDir = Reset-DemoDir -Path $CommercialDataDir -VarRoot $VarRootFull
    $AppDataDir = Reset-DemoDir -Path $AppDataDir -VarRoot $VarRootFull
    Write-Ok '演示数据目录已重置（初始为未授权状态）'
}
else {
    New-Item -ItemType Directory -Path $CommercialDataDir -Force | Out-Null
    New-Item -ItemType Directory -Path $AppDataDir -Force | Out-Null
    Write-WarnLine '保留上次演示数据目录（-KeepData）'
}
$MockOut = Join-Path $LogsDir 'commercial_stdout.log'
$MockErr = Join-Path $LogsDir 'commercial_stderr.log'
$AppOut = Join-Path $LogsDir 'app_stdout.log'
$AppErr = Join-Path $LogsDir 'app_stderr.log'

# ─────────────────────────────────────────────────────────────────────────────
# 4) 起两个服务
# ─────────────────────────────────────────────────────────────────────────────
try {
    # 本机 app 通过 YINZHAN_PUBLIC_KEYS_PATH 加载测试公钥；测试密钥签发的 license 才能解锁。
    # 真实生产演示：去掉该 env，改用安装包内置的 prod-2026 公钥 + 生产私钥签发的 license。
    $env:YINZHAN_DATA_DIR = $AppDataDir
    $env:YINZHAN_PUBLIC_KEYS_PATH = $PublicKeysJson
    $env:YINZHAN_COMMERCIAL_URL = "http://127.0.0.1:$MockPort"
    $env:YINZHAN_PAYWALL = '1'

    # 商业服务：测试 license 私钥；确保不继承生产口令
    $env:LICENSE_KEY_PASSWORD = $null
    $env:LICENSE_PRIVATE_KEY_PATH = $LicenseKeyPath
    $env:LICENSE_KEY_ID = 'test-2026'
    $env:LICENSE_KEY_PASSWORD_ENV = 'LICENSE_KEY_PASSWORD'
    $env:COMMERCIAL_CORS_EXTRA_ORIGINS = ''
    $env:COMMERCIAL_LOG_LEVEL = 'WARNING'

    Write-Step '启动商业服务（provider=mock）…'
    $mockArgs = @(
        '-X', 'utf8', "`"$RunDev`"",
        '--provider', 'mock', '--host', '127.0.0.1', '--port', "$MockPort",
        '--data-dir', "`"$CommercialDataDir`"",
        '--db', "`"$(Join-Path $CommercialDataDir 'commercial.db')`"",
        '--base-url', "http://127.0.0.1:$MockPort", '--log-level', 'warning'
    )
    $script:MockProc = Start-Process -FilePath $Py -ArgumentList $mockArgs `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $MockOut -RedirectStandardError $MockErr
    Write-Ok "商业服务已启动：PID=$($script:MockProc.Id)（日志 $MockOut）"

    Write-Step '启动本机 app（--no-browser --no-ocr）…'
    $appArgs = @(
        '-X', 'utf8', "`"$AppRun`"",
        '--port', "$AppPort", '--host', '127.0.0.1', '--no-browser', '--no-ocr'
    )
    $script:AppProc = Start-Process -FilePath $Py -ArgumentList $appArgs `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $AppOut -RedirectStandardError $AppErr
    Write-Ok "本机 app 已启动：PID=$($script:AppProc.Id)（日志 $AppOut）"

    Write-Step '等待商业服务 /healthz …'
    $mockHealth = Wait-HttpOk -Url "http://127.0.0.1:$MockPort/healthz" -TimeoutSeconds 90 `
        -Proc $script:MockProc -ErrLog $MockErr
    Write-Ok "商业服务就绪：$($mockHealth.Content)"

    Write-Step '等待本机 app /api/health …'
    $runtimePath = Join-Path $AppDataDir 'runtime.json'
    $runtime = $null
    $runtimeDeadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $runtimeDeadline) {
        if ($script:AppProc.HasExited) {
            Show-LogTail -Path $AppErr
            throw "本机 app 提前退出（exit=$($script:AppProc.ExitCode)）"
        }
        if (Test-Path -LiteralPath $runtimePath) {
            try {
                $runtime = Get-Content -LiteralPath $runtimePath -Raw -ErrorAction Stop |
                    ConvertFrom-Json
                if ($runtime -and $runtime.port) { break }
            }
            catch { }
        }
        Start-Sleep -Milliseconds 400
    }
    if (-not $runtime -or -not $runtime.port) {
        Show-LogTail -Path $AppOut
        throw "等待本机 app runtime.json 超时：$runtimePath"
    }
    $ActualAppPort = [int]$runtime.port
    $appHealth = Wait-HttpOk -Url "http://127.0.0.1:$ActualAppPort/api/health" -TimeoutSeconds 90 `
        -Proc $script:AppProc -ErrLog $AppErr
    Write-Ok "本机 app 就绪：http://127.0.0.1:$ActualAppPort/api/health"

    $purchaseUrl = "http://127.0.0.1:$ActualAppPort/purchase"
    Write-Step '检查本机购买页 /purchase …'
    $purchaseResp = Invoke-WebRequest -Uri $purchaseUrl -UseBasicParsing -TimeoutSec 10
    if ($purchaseResp.StatusCode -ne 200) { throw "GET /purchase HTTP $($purchaseResp.StatusCode)" }
    if ($purchaseResp.Content -notmatch 'purchase\.js') { throw '/purchase 页面未加载 purchase.js' }
    Write-Ok "/purchase 200（含 purchase.js）"

    # PID 文件：窗口被强关时可用于手工停进程（正常退出时删除）
    $script:PidFile = Join-Path $LogsDir 'demo_pids.json'
    @{
        commercial_pid = $script:MockProc.Id
        app_pid        = $script:AppProc.Id
        commercial_url = "http://127.0.0.1:$MockPort"
        app_url        = "http://127.0.0.1:$ActualAppPort"
        purchase_url   = $purchaseUrl
        started_at     = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $script:PidFile -Encoding UTF8

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ' 演示已启动！' -ForegroundColor Green
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host "  本机购买页 : $purchaseUrl"
    Write-Host "  商业服务   : http://127.0.0.1:$MockPort/buy.html   （mock 支付）"
    Write-Host "  进程 PID   : 商业服务=$($script:MockProc.Id)  本机 app=$($script:AppProc.Id)"
    Write-Host "  日志目录   : $LogsDir"
    Write-Host ''
    Write-Host '  演示步骤：打开购买页 → 选「专业版」→「支付宝扫码」'
    Write-Host '           → 点「演示：模拟支付」→ 页面自动轮询到 paid 并解锁。'
    Write-Host ''
    Write-Host '  停止演示：在本窗口按 Ctrl+C（自动停止两个子进程）；' -ForegroundColor Yellow
    Write-Host "           或另开窗口执行：Stop-Process -Id $($script:MockProc.Id),$($script:AppProc.Id)" -ForegroundColor Yellow
    Write-Host '  说明：本次使用 L3 测试密钥（test-2026）+ YINZHAN_PUBLIC_KEYS_PATH 测试公钥；'
    Write-Host '        真实生产演示请改用生产私钥（LICENSE_KEY_ID=prod-2026）且本机 app 不设该 env。'
    Write-Host '============================================================' -ForegroundColor Green

    if (-not $NoBrowser) {
        Write-Step '打开浏览器…'
        Start-Process $purchaseUrl | Out-Null
    }
    else {
        Write-WarnLine '已指定 -NoBrowser，请手动打开上面的购买页地址。'
    }

    if ($DurationSeconds -gt 0) {
        Write-Step "自动演示模式：保持 $DurationSeconds 秒后自动停止…"
        Start-Sleep -Seconds $DurationSeconds
        Write-Ok '自动演示计时结束。'
    }
    else {
        Write-Host ''
        Write-Host '按 Ctrl+C 停止演示…' -ForegroundColor Yellow
        while ($true) { Start-Sleep -Seconds 1 }
    }
}
catch {
    $script:ExitCode = 1
    Write-Host ''
    Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
    Show-LogTail -Path $MockErr
    Show-LogTail -Path $AppErr
    Show-LogTail -Path $AppOut
}
finally {
    Write-Host ''
    Write-Step '正在停止子进程并清理…'
    foreach ($proc in @($script:AppProc, $script:MockProc)) {
        if ($proc -and -not $proc.HasExited) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch { }
        }
    }
    Start-Sleep -Milliseconds 500
    if ($script:PidFile -and (Test-Path -LiteralPath $script:PidFile)) {
        Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Ok '演示已停止（数据与日志保留在 commercial/var/demo_*，可复查）'
}

exit $script:ExitCode
