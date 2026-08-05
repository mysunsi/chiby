# 开源边界门禁（P0-8）
# 用法: powershell -File scripts/check_oss_boundary.ps1 [-Wheel path\to.whl]

param(
    [string[]]$Wheel = @()
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$args = @()
foreach ($w in $Wheel) {
    $args += "--wheel"
    $args += $w
}

python scripts/check_oss_boundary.py @args
exit $LASTEXITCODE
