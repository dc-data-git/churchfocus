# Run the full pipeline unattended on Windows. Safe to re-run: finished stages are
# skipped and every model call is cached.
#   powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1 -Config other.yaml
param([string]$Config = "config.yaml")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONUTF8 = "1"                      # UTF-8 everywhere (corpus text contains curly quotes etc.)
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $py -m lexicon -c $Config doctor
if ($LASTEXITCODE -ne 0) { Write-Host "doctor failed; fix the issues above first."; exit 1 }

# keep the PC awake while plugged in (restore later with: powercfg /change standby-timeout-ac 30)
powercfg /change standby-timeout-ac 0

$p = Start-Process -FilePath $py -ArgumentList @("-m", "lexicon", "-c", $Config, "run") `
     -RedirectStandardOutput "run_overnight.out" -RedirectStandardError "run_overnight.err" `
     -WindowStyle Minimized -PassThru
Write-Host "started pid $($p.Id)"
Write-Host "follow:   Get-Content run_overnight.err -Wait -Tail 20"
Write-Host "progress: $py -m lexicon -c $Config status"
Write-Host "stop:     Stop-Process -Id $($p.Id)   (finished stages are kept)"
