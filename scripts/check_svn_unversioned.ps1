# 提交前检查未 svn add 的源码。用法：
#   .\scripts\check_svn_unversioned.ps1
#   .\scripts\check_svn_unversioned.ps1 -Strict
param(
    [switch]$Strict,
    [string]$Root = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here "check_svn_unversioned.py"
$argsList = @()
if ($Root) { $argsList += @("--root", $Root) }
if ($Strict) { $argsList += "--strict" }
python $py @argsList
exit $LASTEXITCODE
