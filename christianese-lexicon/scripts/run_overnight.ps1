# PowerShell version of run_overnight.sh. Run the full pipeline unattended.
# Safe to re-run: finished stages are skipped and every model call is cached.
#   .\scripts\run_overnight.ps1                # uses config.yaml
#   .\scripts\run_overnight.ps1 other.yaml
param([string]$Cfg = "config.yaml")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py -m lexicon -c $Cfg doctor
if ($LASTEXITCODE -ne 0) { Write-Error "doctor failed; not starting the run."; exit 1 }

$p = Start-Process -FilePath $py -ArgumentList "-m", "lexicon", "-c", $Cfg, "run" `
    -RedirectStandardOutput "run_overnight.out" -RedirectStandardError "run_overnight.err" `
    -WindowStyle Hidden -PassThru
Write-Host "started pid $($p.Id)"
Write-Host "follow with:  Get-Content run_overnight.out, run_overnight.err -Wait -Tail 20"
Write-Host "status:       $py -m lexicon -c $Cfg status"
