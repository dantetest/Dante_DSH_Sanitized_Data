<#
.SYNOPSIS
  安装/升级「数据隐站 · DSH 脱敏检测」插件，并自动配置 MCP client。

.DESCRIPTION
  - 复制插件到 <DSH_HOME>/profiles/<Profile>/node_modules/@blctek/dsh-plugin-sanitize-detect
  - 在 profile package.json 的 dsh.profile.bundles 中加入插件（保留已有条目）
  - 自动在 profile cordis.patch.yml 追加 mcp-sanitized-data（已存在则不覆盖）
  - 安装/升级后重启 DSH 生效；卸载用 -Uninstall。
  - 数据隐站工作台是独立本地服务，不在本脚本里常驻；请用桌面“数据隐站工作台”快捷方式/启动.bat。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1 -Profile desktop
  powershell -ExecutionPolicy Bypass -File install.ps1 -Profile web -SkipMcp
  powershell -ExecutionPolicy Bypass -File install.ps1 -Profile desktop -Uninstall
#>
param(
  [string]$Profile = 'desktop',
  [string]$DshHome = (Join-Path $env:USERPROFILE '.dsh'),
  [string]$McpCommand = '',
  [switch]$SkipMcp,
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$PluginName = '@blctek/dsh-plugin-sanitize-detect'
$PackageRoot = $PSScriptRoot
$ProfileDir = Join-Path $DshHome "profiles\$Profile"
$PackageJson = Join-Path $ProfileDir 'package.json'
$PatchFile = Join-Path $ProfileDir 'cordis.patch.yml'

if (-not (Test-Path $PackageJson)) {
  throw "找不到 DSH profile：$ProfileDir（先启动过一次该 profile）"
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
  [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function YamlQuote([string]$Value) {
  return '"' + $Value.Replace('\', '\\').Replace('"', '\"') + '"'
}

$doc = Get-Content $PackageJson -Raw | ConvertFrom-Json
$nodeModules = Join-Path $ProfileDir 'node_modules\@blctek'
$target = Join-Path $nodeModules 'dsh-plugin-sanitize-detect'
$bundles = @($doc.dsh.profile.bundles)

if ($Uninstall) {
  if (Test-Path $target) { Remove-Item $target -Recurse -Force }
  $doc.dsh.profile.bundles = @($bundles | Where-Object { $_ -ne $PluginName })
  if ($doc.dependencies -and $doc.dependencies.PSObject.Properties.Name -contains $PluginName) {
    $doc.dependencies.PSObject.Properties.Remove($PluginName)
  }
  Write-Utf8NoBom $PackageJson ($doc | ConvertTo-Json -Depth 20)
  Write-Host "已卸载 $PluginName（重启 DSH 生效）。" -ForegroundColor Green
  Write-Host "MCP client 配置保留在 cordis.patch.yml；如不再需要，请手工删除 mcp-sanitized-data 条目。" -ForegroundColor DarkGray
  exit 0
}

New-Item -ItemType Directory -Force -Path $nodeModules | Out-Null
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item (Join-Path $PackageRoot '*') $target -Recurse -Force

if (-not $doc.dependencies) {
  $doc | Add-Member -MemberType NoteProperty -Name dependencies -Value ([pscustomobject]@{})
}
$doc.dependencies | Add-Member -MemberType NoteProperty -Name $PluginName -Value ('file:./node_modules/@blctek/dsh-plugin-sanitize-detect') -Force
if ($bundles -notcontains $PluginName) {
  $doc.dsh.profile.bundles = @($bundles + $PluginName)
}
Write-Utf8NoBom $PackageJson ($doc | ConvertTo-Json -Depth 20)

# ---- MCP client patch（自动化原来手工补的 mcp-sanitized-data） ----
if (-not $SkipMcp) {
  $patchText = if (Test-Path $PatchFile) { Get-Content $PatchFile -Raw } else { '' }
  if ($patchText -match 'mcp-sanitized-data') {
    Write-Host "MCP client 配置已存在，跳过 cordis.patch.yml 修改。" -ForegroundColor DarkGray
  } else {
    $appRoot = Split-Path -Parent $PackageRoot
    $installedMcp = Join-Path $appRoot 'Sanitized_Data_MCP.exe'
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PackageRoot)
    $sourceMcp = Join-Path $repoRoot 'app\server\mcp_server.py'
    $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source

    $command = ''
    $argsYaml = ''
    $cwd = ''
    if ($McpCommand) {
      $command = (Resolve-Path $McpCommand).Path
      $cwd = Split-Path -Parent $command
      $argsYaml = '        args: []'
    } elseif (Test-Path $installedMcp) {
      $command = $installedMcp
      $cwd = $appRoot
      $argsYaml = '        args: []'
    } elseif ((Test-Path $sourceMcp) -and $python) {
      $command = $python
      $cwd = $repoRoot
      $argsYaml = "        args:`n          - `"-X`"`n          - `"utf8`"`n          - " + (YamlQuote $sourceMcp)
    } else {
      Write-Host "! 未自动找到 MCP 入口，跳过 MCP 配置；可传 -McpCommand <Sanitized_Data_MCP.exe> 手工指定。" -ForegroundColor Yellow
    }

    if ($command) {
      $block = @"

- insert:
    - id: mcp-sanitized-data
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        transport: stdio
        serverName: sanitized-data
        command: $(YamlQuote $command)
$(if ($argsYaml) { $argsYaml + "`n" } else { "" })        cwd: $(YamlQuote $cwd)
        toolCallTimeoutMs: 600000
        reconnect:
          enabled: true
          maxAttempts: 10
"@
      Write-Utf8NoBom $PatchFile (($patchText.TrimEnd() + "`n" + $block).TrimStart())
      Write-Host "已写入 MCP client 配置：$command" -ForegroundColor Cyan
    }
  }
}

Write-Host "已安装 $PluginName" -ForegroundColor Green
Write-Host "  profile: $ProfileDir"
Write-Host "  package: $target"
Write-Host "重启 DSH 后：工具卡出现「数据隐站」；左下角「数据隐站工作台」直接打开真实 WebUI。" -ForegroundColor Cyan
Write-Host "工作台需独立运行（桌面快捷方式/启动.bat），默认 http://127.0.0.1:8760；host 代理只读 runtime.json，不外呼。" -ForegroundColor DarkGray
