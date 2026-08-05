#Requires -Version 5.1
<#
.SYNOPSIS
  Export OSS tree from SVN Assistant and push to GitHub mysunsi/chiby (SSH).

.EXAMPLE
  powershell -File scripts/sync_oss_to_github.ps1
  powershell -File scripts/sync_oss_to_github.ps1 -DryRun
  powershell -File scripts/sync_oss_to_github.ps1 -ForcePush
#>
param(
  [string]$RepoRoot = "",
  [string]$MirrorDir = "",
  [string]$RemoteUrl = "git@github.com:mysunsi/chiby.git",
  [string]$Branch = "main",
  [switch]$DryRun,
  [switch]$ForcePush,
  [switch]$SkipBoundaryCheck
)

$ErrorActionPreference = "Stop"
# ssh.exe writes banners to stderr; do not treat as terminating
$PSNativeCommandUseErrorActionPreference = $false

function Resolve-RepoRoot {
  param([string]$Hint)
  if ($Hint -and (Test-Path (Join-Path $Hint "release\oss_publish.toml"))) {
    return (Resolve-Path $Hint).Path
  }
  $here = $PSScriptRoot
  if (-not $here) { $here = (Get-Location).Path }
  $cand = (Resolve-Path (Join-Path $here "..")).Path
  if (Test-Path (Join-Path $cand "release\oss_publish.toml")) { return $cand }
  throw "Cannot locate Assistant repo root (need release/oss_publish.toml)."
}

$RepoRoot = Resolve-RepoRoot $RepoRoot
if (-not $MirrorDir) {
  $MirrorDir = Join-Path (Split-Path $RepoRoot -Parent) "_oss_mirror\chiby"
}

$sshExe = "C:/Program Files/Git/usr/bin/ssh.exe"
$keyPath = Join-Path $env:USERPROFILE ".ssh\id_ed25519_github_chiby"
if (-not (Test-Path $sshExe)) { throw "Git ssh.exe not found: $sshExe" }
if (-not (Test-Path $keyPath)) { throw "SSH key missing: $keyPath" }

# Avoid non-ASCII HOME path issues in Git's MSYS ssh: keep known_hosts under D:\Open
$khDir = "D:\Open\_oss_mirror"
if (-not (Test-Path $khDir)) { New-Item -ItemType Directory -Path $khDir -Force | Out-Null }
$khPath = Join-Path $khDir "known_hosts"
$keyFwd = ($keyPath -replace '\\', '/')
$khFwd = ($khPath -replace '\\', '/')
# Quote ssh.exe path (contains spaces under Program Files)
$env:GIT_SSH_COMMAND = "`"$sshExe`" -i `"$keyFwd`" -o IdentitiesOnly=yes -o UserKnownHostsFile=`"$khFwd`" -o StrictHostKeyChecking=accept-new"
# Avoid global core.sshCommand overriding / duplicating
git config --global --unset-all core.sshCommand 2>$null
$env:GIT_SSH = $sshExe

Write-Host "== Auth check =="
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$authOut = (& $sshExe -i $keyPath -o IdentitiesOnly=yes -o "UserKnownHostsFile=$khPath" -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 | ForEach-Object { "$_" }) -join "`n"
$ErrorActionPreference = $prevEap
Write-Host $authOut
if ($authOut -notmatch "successfully authenticated") {
  throw "GitHub SSH auth failed. Check account SSH key."
}

$AllowRoots = @(
  "packages",
  "docs",
  "release",
  "scripts",
  "examples",
  "tests",
  "tools",
  "api",
  "extensions",
  "remediator",
  "deploy",
  "ops-ui",
  "web",
  "terminal",
  "README.md",
  "CONTRIBUTING.md",
  "CODE_OF_CONDUCT.md",
  "ARCHITECTURE.md",
  "API_REFERENCE.md",
  "CHANGELOG.md",
  "SECURITY.md",
  "LICENSE",
  "NOTICE",
  "COMPATIBILITY.md",
  "pyproject.toml",
  "requirements.txt",
  "uv.lock",
  "conftest.py",
  "path_alias.py",
  ".gitignore",
  ".gitleaks.toml"
)

$DenyPrefixes = @(
  "proprietary",
  "data",
  "log",
  "reports",
  "MagicMock",
  ".cursor",
  ".pytest_cache",
  ".venv",
  ".smoke-venv",
  ".release-venv",
  ".venv_p23_test",
  "node_modules",
  "dist",
  "build",
  "dashboard",
  "patches"
)

$DenyNameExact = @(
  ".env",
  ".env.example",
  "dirlist.txt",
  "_bridge_snip.txt",
  "_obr.txt",
  "_debug_pty.py",
  "llm_config.json",
  "llm_models.json",
  "hosts.json"
)

$DenyNameContains = @(
  "mobile",
  "hermes",
  "omnipotent",
  "pro_core",
  "repair_txn",
  "hermes_bridge"
)

$DenyDocsNameContains = @(
  "strategy",
  "mobile",
  "hermes",
  "omnipotent",
  "contest",
  "industrial",
  "smartops",
  "db-ai"
)

function Test-DeniedPath {
  param([string]$RelPosix)
  $rel = $RelPosix.Replace('\', '/').TrimStart('/')
  $name = Split-Path $rel -Leaf

  foreach ($p in $DenyPrefixes) {
    if ($rel -eq $p -or $rel.StartsWith("$p/")) { return $true }
  }
  if ($DenyNameExact -contains $name) { return $true }
  if ($name -match '\.db$' -or $name -match '\.sqlite') { return $true }
  if ($name -match '^\.env') { return $true }

  if ($rel.StartsWith("tests/") -or $rel.StartsWith("tools/")) {
    $low = $name.ToLowerInvariant()
    foreach ($k in $DenyNameContains) {
      if ($low.Contains($k)) { return $true }
    }
  }

  if ($rel.StartsWith("docs/")) {
    $low = $name.ToLowerInvariant()
    foreach ($k in $DenyDocsNameContains) {
      if ($low.Contains($k)) { return $true }
    }
  }

  $parts = $rel.Split('/')
  foreach ($seg in $parts) {
    if ($seg -in @('__pycache__', 'node_modules', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.eggs')) {
      return $true
    }
    if ($seg -match '\.egg-info$') { return $true }
  }
  return $false
}

function Copy-OssTree {
  param([string]$SrcRoot, [string]$DstRoot)

  if (Test-Path $DstRoot) {
    Get-ChildItem $DstRoot -Force | Where-Object { $_.Name -ne '.git' } | ForEach-Object {
      Remove-Item $_.FullName -Recurse -Force
    }
  } else {
    New-Item -ItemType Directory -Path $DstRoot -Force | Out-Null
  }

  $script:ossCopied = 0
  $script:ossSkipped = 0

  foreach ($item in $AllowRoots) {
    $src = Join-Path $SrcRoot $item
    if (-not (Test-Path $src)) {
      Write-Host "skip missing: $item"
      continue
    }
    $srcItem = Get-Item -LiteralPath $src
    if ($srcItem.PSIsContainer) {
      Get-ChildItem -LiteralPath $src -Recurse -File -Force | ForEach-Object {
        $full = $_.FullName
        $rel = $full.Substring($SrcRoot.Length).TrimStart('\', '/')
        $relPosix = $rel.Replace('\', '/')
        if (Test-DeniedPath $relPosix) {
          $script:ossSkipped++
          return
        }
        $dest = Join-Path $DstRoot $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) {
          New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $full -Destination $dest -Force
        $script:ossCopied++
      }
    } else {
      $relPosix = $item.Replace('\', '/')
      if (Test-DeniedPath $relPosix) {
        $script:ossSkipped++
        continue
      }
      $dest = Join-Path $DstRoot $item
      $destDir = Split-Path $dest -Parent
      if ($destDir -and -not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
      }
      Copy-Item -LiteralPath $src -Destination $dest -Force
      $script:ossCopied++
    }
  }

  $ciDir = Join-Path $SrcRoot "release\ci"
  if (Test-Path $ciDir) {
    $wf = Join-Path $DstRoot ".github\workflows"
    New-Item -ItemType Directory -Path $wf -Force | Out-Null
    if (Test-Path (Join-Path $ciDir "ci.yml")) {
      Copy-Item (Join-Path $ciDir "ci.yml") (Join-Path $wf "ci.yml") -Force
      $script:ossCopied++
    }
    if (Test-Path (Join-Path $ciDir "release.yml")) {
      Copy-Item (Join-Path $ciDir "release.yml") (Join-Path $wf "release.yml") -Force
      $script:ossCopied++
    }
  }

  $giDst = Join-Path $DstRoot ".gitignore"
  if (-not (Test-Path $giDst)) {
    $giSrc = Join-Path $SrcRoot "release\templates\.gitignore"
    if (Test-Path $giSrc) {
      Copy-Item $giSrc $giDst -Force
      $script:ossCopied++
    }
  }

  Write-Host "copied_files=$script:ossCopied skipped=$script:ossSkipped"
}

Write-Host "== Export OSS tree =="
Write-Host "src=$RepoRoot"
Write-Host "dst=$MirrorDir"
Copy-OssTree -SrcRoot $RepoRoot -DstRoot $MirrorDir

if (-not $SkipBoundaryCheck) {
  Write-Host "== Boundary check =="
  Push-Location $RepoRoot
  try {
    python scripts/check_oss_boundary.py
    if ($LASTEXITCODE -ne 0) { throw "check_oss_boundary.py failed (exit $LASTEXITCODE)" }
  } finally {
    Pop-Location
  }
}

if ($DryRun) {
  Write-Host "DryRun: export done, skip git push."
  exit 0
}

Write-Host "== Git commit / push =="
if (-not (Test-Path (Join-Path $MirrorDir ".git"))) {
  git -C $MirrorDir init -b $Branch
  git -C $MirrorDir remote add origin $RemoteUrl
} else {
  $rem = git -C $MirrorDir remote get-url origin 2>$null
  if (-not $rem) {
    git -C $MirrorDir remote add origin $RemoteUrl
  } elseif ($rem -ne $RemoteUrl) {
    git -C $MirrorDir remote set-url origin $RemoteUrl
  }
}

git -C $MirrorDir add -A
$status = git -C $MirrorDir status --porcelain
if (-not $status) {
  Write-Host "No changes to commit."
} else {
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
  $msg = "chore: sync OSS snapshot from SVN Assistant $stamp"
  git -C $MirrorDir -c user.name="Zhang Quanlin" -c user.email="285193443@qq.com" commit -m $msg
}

if ($ForcePush) {
  Write-Host "git push --force origin HEAD:$Branch"
  git -C $MirrorDir push -u origin "HEAD:$Branch" --force
} else {
  Write-Host "git push origin HEAD:$Branch"
  git -C $MirrorDir push -u origin "HEAD:$Branch"
}

if ($LASTEXITCODE -ne 0) {
  Write-Host "Push failed. If remote has unrelated history, re-run with -ForcePush" -ForegroundColor Yellow
  exit $LASTEXITCODE
}

Write-Host "OK: pushed to $RemoteUrl ($Branch)" -ForegroundColor Green
Write-Host "Mirror working tree: $MirrorDir"
