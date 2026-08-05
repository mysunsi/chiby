# P2-3：上传到 TestPyPI（需 API Token）
#
# 1) 在 https://test.pypi.org/manage/account/token/ 创建 token
# 2) PowerShell:
#      $env:TWINE_USERNAME = '__token__'
#      $env:TWINE_PASSWORD = 'pypi-AgENdGVzdC5weXBpLm9yZw...'
# 3) 本脚本

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $env:TWINE_PASSWORD) {
    Write-Error "请设置 TWINE_PASSWORD（TestPyPI API token）。TWINE_USERNAME 默认 __token__"
}

if (-not $env:TWINE_USERNAME) { $env:TWINE_USERNAME = "__token__" }

Write-Host "==> upload chibycore"
python -m twine upload --repository testpypi --non-interactive packages/chibycore/dist/*
Write-Host "==> upload chibyterm"
python -m twine upload --repository testpypi --non-interactive packages/chibyterm/dist/*
Write-Host "OK. Install test:"
Write-Host '  pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ chibyterm'
