# 下载安装包要捆绑的运行时（构建机联网时执行一次）
#   powershell -ExecutionPolicy Bypass -File desktop\fetch_redist.ps1
$ErrorActionPreference = 'Stop'
$dir = Join-Path $PSScriptRoot 'redist'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$items = @(
    @{ Name = 'MicrosoftEdgeWebview2Setup.exe'; Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' },
    @{ Name = 'VC_redist.x64.exe';             Url = 'https://aka.ms/vs/17/release/vc_redist.x64.exe' }
)

foreach ($it in $items) {
    $dst = Join-Path $dir $it.Name
    if (Test-Path $dst) { "已存在 $($it.Name)"; continue }
    "下载 $($it.Name) …"
    Invoke-WebRequest -Uri $it.Url -OutFile $dst -UseBasicParsing
    "{0}  {1:N1} MB  sha256={2}" -f $it.Name, ((Get-Item $dst).Length / 1MB),
        (Get-FileHash $dst -Algorithm SHA256).Hash.Substring(0, 16)
}
"完成：$dir"
