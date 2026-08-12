# Sobiraet portativnyy arhiv Umnik dlya drugogo PK.
# Simvolnye ssylki HuggingFace razvorachivayutsya v obychnye fayly
# (robocopy bez /SL), poetomu raspakovka rabotaet bez prav symlink.
#
# Zapusk:
#   powershell -ExecutionPolicy Bypass -File F:\Umnik\SOBRAT-ARHIV.ps1
#
# Rezultat:
#   F:\Umnik-portable.zip  (vnutri papka Umnik\)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$drive = Split-Path -Qualifier $root
$stageRoot = Join-Path $drive "Umnik-pack-staging"
$stage = Join-Path $stageRoot "Umnik"
$zipPath = Join-Path $drive "Umnik-portable.zip"

function Size-Of($p) {
    if (-not (Test-Path $p)) { return 0 }
    (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
}

function Assert-RobocopyOk([int]$code) {
    # 0-7 = uspeh / chastichnyy uspeh; 8+ = oshibka
    if ($code -ge 8) {
        throw "robocopy failed with exit code $code"
    }
}

Write-Host ""
Write-Host "UMNIK PACK" -ForegroundColor Cyan
Write-Host "  source : $root"
Write-Host "  staging: $stage"
Write-Host "  zip    : $zipPath"
Write-Host ""

$srv = Get-Process llama-server -ErrorAction SilentlyContinue
if ($srv) {
    Write-Host ("Stopping llama-server (PID {0})..." -f ($srv.Id -join ", ")) -ForegroundColor Yellow
    Stop-Process -Name llama-server -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

if (Test-Path $stageRoot) {
    Write-Host "Removing old staging..." -ForegroundColor Yellow
    Remove-Item $stageRoot -Recurse -Force
}
if (Test-Path $zipPath) {
    Write-Host "Removing old zip..." -ForegroundColor Yellow
    Remove-Item $zipPath -Force
}

New-Item -ItemType Directory -Path $stage -Force | Out-Null

# Bez /SL: symlinky kopiruyutsya kak soderzhimoe faylov (nuzhno dlya ZIP).
$excludeDirs = @(
    "wheels",
    "__pycache__",
    ".git",
    "logs"
)
$profileDir = Join-Path $root "thunderbird\profile"
if (Test-Path $profileDir) {
    $excludeDirs += $profileDir
}

Write-Host "Copying (dereference symlinks)..." -ForegroundColor Cyan
$t0 = Get-Date
$xdArgs = @()
foreach ($d in $excludeDirs) { $xdArgs += @("/XD", $d) }

& robocopy $root $stage /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NC /NS /NP `
    /XF "Umnik-portable.zip" `
    @xdArgs
Assert-RobocopyOk $LASTEXITCODE

New-Item -ItemType Directory -Path (Join-Path $stage "logs") -Force | Out-Null

$howtoSrc = Join-Path $root "KAK-ZAPUSTIT.txt"
if (Test-Path $howtoSrc) {
    Copy-Item $howtoSrc (Join-Path $stage "KAK-ZAPUSTIT.txt") -Force
}

$prov = Join-Path $stage "scripts\proverka.py"
if (-not (Test-Path $prov)) {
    throw "scripts\proverka.py missing in staging - pack aborted"
}

$linkCount = @(
    Get-ChildItem (Join-Path $stage "models") -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.LinkType }
).Count
if ($linkCount -gt 0) {
    throw "Staging still has $linkCount symlinks - ZIP would break. Abort."
}

$stageGb = [math]::Round((Size-Of $stage) / 1GB, 2)
Write-Host ("Staging ready: {0} GB in {1:n1} min" -f $stageGb, ((Get-Date) - $t0).TotalMinutes) -ForegroundColor Green

Write-Host "Creating zip (tar -a)..." -ForegroundColor Cyan
$t1 = Get-Date
& tar.exe -a -c -f $zipPath -C $stageRoot "Umnik"
if ($LASTEXITCODE -ne 0) {
    throw "tar failed with exit code $LASTEXITCODE"
}
$zipGb = [math]::Round((Get-Item $zipPath).Length / 1GB, 2)
Write-Host ("Zip ready: {0} GB in {1:n1} min" -f $zipGb, ((Get-Date) - $t1).TotalMinutes) -ForegroundColor Green

Write-Host "Removing staging..." -ForegroundColor Yellow
Remove-Item $stageRoot -Recurse -Force

Write-Host ""
Write-Host "DONE" -ForegroundColor Green
Write-Host "  Archive: $zipPath ($zipGb GB)"
Write-Host ""
Write-Host "U kollegi:"
Write-Host "  1. Raspakovat v D:\Umnik (koren diska)"
Write-Host "  2. Esli skachali iz seti - Razblokirovat papku"
Write-Host "  3. PROVERKA.bat, zatem umnik.bat"
Write-Host "  4. Nuzhen drayver NVIDIA"
Write-Host ""
Write-Host "Zapasnoy put bez arhiva (fleshka):"
Write-Host ('  robocopy "{0}" E:\Umnik /E /R:2 /W:2' -f $root)
Write-Host ""
