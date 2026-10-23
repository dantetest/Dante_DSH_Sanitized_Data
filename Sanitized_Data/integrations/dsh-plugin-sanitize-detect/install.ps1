<#
.SYNOPSIS
  把「数据隐站 · DSH 脱敏检测」插件安装到指定 DSH profile。

.DESCRIPTION
  - 把插件复制到 <DSH_HOME>/profiles/<Profile>/node_modules/@blctek/dsh-plugin-sanitize-detect
  - 在 profile package.json 的 dsh.profile.bundles 中加入插件（保留已有条目）
  - 不修改 cordis.patch.yml（插件自带 cordis.patch.yml 由 bundle 机制加载）
  - 安装/升级后重启 DSH 生效；卸载用 -Uninstall。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1 -Profile desktop
  powershell -ExecutionPolicy Bypass -File install.ps1 -Profile web
  powershell -ExecutionPolicy Bypass -File install.ps1 -Profile desktop -Uninstall
#>
param(
  [string]$Profile = 'desktop',
  [string]$DshHome = (Join-Path $env:USERPROFILE '.dsh'),
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$PluginName = '@blctek/dsh-plugin-sanitize-detect'
$PackageRoot = $PSScriptRoot
$ProfileDir = Join-Path $DshHome "profiles\$Profile"
$PackageJson = Join-Path $ProfileDir 'package.json'

if (-not (Test-Path $PackageJson)) {
  throw "找不到 DSH profile：$ProfileDir（先启动过一次该 profile）"
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
  $json = $doc | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText($PackageJson, $json, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "已卸载 $PluginName（重启 DSH 生效）。" -ForegroundColor Green
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
$json = $doc | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText($PackageJson, $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "已安装 $PluginName" -ForegroundColor Green
Write-Host "  profile: $ProfileDir"
Write-Host "  package: $target"
Write-Host "重启 DSH 后：对话工具卡出现「数据隐站」，侧边栏底部出现「脱敏检测」。" -ForegroundColor Cyan
Write-Host "本机数据隐站需先运行（默认 http://127.0.0.1:8760）；host 代理只读 runtime.json，不外呼。" -ForegroundColor DarkGray
