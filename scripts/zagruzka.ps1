# Zagruzchik s dokachkoy dlya nestabilnogo kanala.
# Soobscheniya latinicey namerenno: PowerShell 5.1 portit kirillicu v .ps1 bez BOM.
#
# Zapusk:
#   powershell -ExecutionPolicy Bypass -File F:\Umnik\scripts\zagruzka.ps1 <URL> <kuda_sohranit>
#
# Pri obryve perezapuskaet curl s prodolzheniem s mesta ostanovki.
# Prervat rukami: Ctrl+C, skachannoe sohranitsya, povtornyy zapusk prodolzhit.

param(
    [Parameter(Mandatory=$true)][string]$Url,
    [Parameter(Mandatory=$true)][string]$Out,
    [int]$MaxTries = 100,
    [string]$Rate = "4M"
)

$dir = Split-Path $Out -Parent
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

Write-Host ""
Write-Host "URL : $Url"
Write-Host "FILE: $Out"
Write-Host ""

$prev = -1
$stuck = 0

for ($i = 1; $i -le $MaxTries; $i++) {
    curl.exe -L -C - -4 --limit-rate $Rate --retry 2 --retry-delay 2 -o $Out $Url
    $code = $LASTEXITCODE
    $have = if (Test-Path $Out) { (Get-Item $Out).Length } else { 0 }
    $mb = [math]::Round($have / 1MB, 1)

    if ($code -eq 0 -and $have -eq $prev -and $have -gt 0) {
        Write-Host ""
        Write-Host "GOTOVO: $mb MB -> $Out" -ForegroundColor Green
        exit 0
    }

    if ($have -eq $prev) {
        $stuck++
        Write-Host ("--- popytka {0}: {1} MB (bez progressa {2} raz) ---" -f $i, $mb, $stuck) -ForegroundColor Yellow
        if ($stuck -ge 5) {
            Write-Host ""
            Write-Host "STOP: 5 popytok podryad bez progressa." -ForegroundColor Red
            Write-Host "Prichina ne v obryvah. Proveri: est li fayl po adresu, hvataet li mesta na diske." -ForegroundColor Red
            exit 1
        }
    } else {
        $stuck = 0
        Write-Host ("--- popytka {0}: {1} MB ---" -f $i, $mb) -ForegroundColor Cyan
    }

    $prev = $have
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "Ischerpano $MaxTries popytok. Zapusti komandu snova - prodolzhit s etogo mesta." -ForegroundColor Yellow
exit 1
