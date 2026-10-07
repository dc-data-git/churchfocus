# Run every group unattended on Windows, largest first. Safe to re-run: finished groups are
# skipped, model calls and web pages are cached.
#   powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1 -Config other.yaml
param([string]$Config = "config.yaml")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONUTF8 = "1"                      # UTF-8 everywhere (group names contain curly quotes etc.)
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $py -m denomkb -c $Config doctor
if ($LASTEXITCODE -ne 0) { Write-Host "doctor failed; fix the issues above first."; exit 1 }

# keep the PC awake while plugged in (restore later with: powercfg /change standby-timeout-ac 30)
powercfg /change standby-timeout-ac 0

$p = Start-Process -FilePath $py -ArgumentList @("-m", "denomkb", "-c", $Config, "run") `
     -RedirectStandardOutput "run_overnight.out" -RedirectStandardError "run_overnight.err" `
     -WindowStyle Minimized -PassThru
Write-Host "started pid $($p.Id)"
Write-Host "follow:   Get-Content run_overnight.err -Wait -Tail 20"
Write-Host "progress: $py -m denomkb -c $Config status"
Write-Host "stop:     Stop-Process -Id $($p.Id)   (finished groups are kept)"
