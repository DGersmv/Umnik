# Podgotovka papki Umnik k peredache kollege.
# Nichego ne udalyaet bez podtverzhdeniya. Soobscheniya latinicey:
# PowerShell 5.1 portit kirillicu v .ps1 bez BOM.
#
# Zapusk:
#   powershell -ExecutionPolicy Bypass -File F:\Umnik\UPAKOVKA.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Size-Of($p) {
    if (-not (Test-Path $p)) { return 0 }
    (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
}

Write-Host ""
Write-Host "PAPKA: $root" -ForegroundColor Cyan
Write-Host ""

$items = @(
  @{p="thunderbird\profile"; why="PAROL OT POCHTY i vsya vasha perepiska"; must=$true},
  @{p="wheels";              why="2.6 GB - koleso torch, uzhe ustanovleno"; must=$false},
  @{p="logs";                why="zhurnaly, kollege ne nuzhny";            must=$false}
)

Write-Host "--- CHTO PREDLAGAYU UBRAT ---" -ForegroundColor Yellow
foreach ($i in $items) {
    $full = Join-Path $root $i.p
    $mb = [math]::Round((Size-Of $full)/1MB)
    if (Test-Path $full) {
        $tag = if ($i.must) { "OBYAZATELNO" } else { "mozhno" }
        Write-Host ("  [{0,-11}] {1,-22} {2,7} MB  - {3}" -f $tag, $i.p, $mb, $i.why)
    }
}

Write-Host ""
Write-Host "--- RAZMER PO PAPKAM ---" -ForegroundColor Yellow
Get-ChildItem $root -Directory | ForEach-Object {
    [PSCustomObject]@{ Papka = $_.Name; MB = [math]::Round((Size-Of $_.FullName)/1MB) }
} | Sort-Object MB -Descending | Format-Table -AutoSize

Write-Host "--- PROVERKA SIMVOLNYH SSYLOK ---" -ForegroundColor Yellow
$links = Get-ChildItem (Join-Path $root "models") -Recurse -Force -ErrorAction SilentlyContinue |
         Where-Object { $_.LinkType }
if ($links) {
    Write-Host ("  NAYDENO ssylok: {0}" -f $links.Count) -ForegroundColor Red
    Write-Host "  ZIP ih ne perenesyot. Upakovyvay 7-Zip s klyuchom -snl," -ForegroundColor Red
    Write-Host "  ili kopiruy papku cherez robocopy /MIR na fleshku." -ForegroundColor Red
} else {
    Write-Host "  Ssylok net - obychnyy ZIP podoydyot." -ForegroundColor Green
}

Write-Host ""
# Zapuschennyy llama-server derzhit zhurnal i ~5 GB videopamyati
$srv = Get-Process llama-server -ErrorAction SilentlyContinue
if ($srv) {
    Write-Host ""
    Write-Host ("  RABOTAET llama-server (PID {0}) - derzhit zhurnal i videopamyat" -f ($srv.Id -join ", ")) -ForegroundColor Red
    $k = Read-Host "  Ostanovit ego? (da/net)"
    if ($k -eq "da") {
        Stop-Process -Name llama-server -Force
        Start-Sleep -Seconds 2
        Write-Host "  ostanovlen" -ForegroundColor Green
    }
}

Write-Host ""
$ans = Read-Host "Udalit thunderbird\profile, wheels i logs? (da/net)"
if ($ans -eq "da") {
    foreach ($i in $items) {
        $full = Join-Path $root $i.p
        if (Test-Path $full) {
            Remove-Item $full -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path $full) {
                Write-Host ("  NE UDALOS: {0} - fayl zanyat drugoy programmoy" -f $i.p) -ForegroundColor Red
            } else {
                Write-Host ("  udaleno: {0}" -f $i.p) -ForegroundColor Green
            }
        }
    }
    New-Item -ItemType Directory -Path (Join-Path $root "logs") -Force | Out-Null
    Write-Host ""
    Write-Host "Gotovo. Itogovyy razmer:" -ForegroundColor Cyan
    Write-Host ("  {0} GB" -f [math]::Round((Size-Of $root)/1GB, 2))
} else {
    Write-Host "Nichego ne udaleno." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "DALSHE:" -ForegroundColor Cyan
Write-Host "  1. Kopiruy TOLKO cherez robocopy - ZIP ne perenosit simvolnye ssylki:"
Write-Host "         robocopy F:\Umnik E:\Umnik /MIR /R:2 /W:2 /NFL /NDL"
Write-Host "     Gde E: - fleshka. Ne cherez internet: skachannye fayly Windows"
Write-Host "     pomechaet i blokiruet zapusk .bat"
Write-Host "  2. U kollegi: raspakovat v KOREN diska, naprimer D:\Umnik"
Write-Host "  3. U kollegi: zapustit PROVERKA.bat - on skazhet, chego ne hvataet"
Write-Host "  4. Tolko posle etogo - umnik.bat"
Write-Host ""
