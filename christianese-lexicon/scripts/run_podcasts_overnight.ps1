# Podcasts + YouTube -> corpus -> lexicon, unattended. Run from anywhere:
#   .\scripts\run_podcasts_overnight.ps1            # full run (about 3 hours on a 16 GB GPU)
#   .\scripts\run_podcasts_overnight.ps1 -Plan      # list the episodes it would use, then stop
#   .\scripts\run_podcasts_overnight.ps1 -Quick     # small test: ~4 episodes per tradition, 10 min each
#   .\scripts\run_podcasts_overnight.ps1 -SkipFetch # corpus already built; just run the lexicon
#   .\scripts\run_podcasts_overnight.ps1 -WhisperTest # 2-minute GPU transcription check, then stop
#   .\scripts\run_podcasts_overnight.ps1 -Episodes 16 -Minutes 12   # smaller / faster corpus
# Safe to re-run: transcripts are cached in podcasts\cache and model calls in work_podcasts\.
# Keep this window open. The PC is kept awake until the run ends (no settings are changed).
# The detailed log (including Python errors) is podcasts\podcasts.log.
param([switch]$Plan, [switch]$Quick, [switch]$SkipFetch, [switch]$WhisperTest, [string]$Cfg = "config.podcasts.yaml",
      [int]$Episodes = 0, [int]$Minutes = 0)   # override episodes per tradition / minutes per episode
# "Continue": in Windows PowerShell 5.1, "Stop" turns Python's log output (stderr) into errors.
# Failures are caught by the exit-code checks below instead.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "No .venv found. Create it first (see README)."; exit 1 }

# keep the machine awake while this script runs (released automatically when it exits)
Add-Type -Namespace Win -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[void][Win.Power]::SetThreadExecutionState([uint32]2147483649)   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

Start-Transcript -Path "podcasts\run.log" -Append | Out-Null
try {
    & $py -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('faster_whisper', 'yt_dlp', 'youtube_transcript_api')) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing extras (faster-whisper, yt-dlp, youtube-transcript-api, CUDA libraries)..."
        & $py -m pip install -r podcasts\requirements-podcasts.txt
        if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    }

    $others = Get-ChildItem data\raw -Include *.jsonl, *.csv -Recurse | Where-Object Name -ne "podcasts.jsonl"
    if ($others) { Write-Warning "data\raw also contains: $($others.Name -join ', '). They will be mixed into this run." }

    if (-not $SkipFetch) {
        $a = @("scripts\podcasts_to_corpus.py")
        if ($Plan)  { $a += "--plan" }
        if ($WhisperTest) { $a += "--whisper-test" }
        if ($Episodes -gt 0) { $a += @("--episodes-per-tradition", "$Episodes") }
        if ($Minutes -gt 0)  { $a += @("--max-minutes", "$Minutes") }
        if ($Quick) { $a += @("--episodes-per-tradition", "4", "--min-per-show", "1", "--max-per-show", "2", "--max-minutes", "10") }
        Write-Host "`n=== 1/2  podcasts -> data\raw\podcasts.jsonl  ($(Get-Date -Format t)) ==="
        & $py @a
        if ($LASTEXITCODE -ne 0) { throw "podcasts_to_corpus.py failed (details in podcasts\podcasts.log)" }
        if ($Plan -or $WhisperTest) { return }
    }

    # don't spend hours of model time on a corpus that came out thin
    $rep = Get-Content podcasts\corpus_report.json -Raw | ConvertFrom-Json
    $usable = @($rep.PSObject.Properties | Where-Object { $_.Value.chunks -ge 20 }).Count
    Write-Host "corpus: $usable traditions with 20+ chunks"
    if ($usable -lt 10 -and -not $Quick) {
        throw "Only $usable traditions have enough text (expected 13). Transcription probably failed: see podcasts\podcasts.log"
    }

    Write-Host "`n=== 2/2  lexicon pipeline  ($(Get-Date -Format t)) ==="
    & $py -m lexicon -c $Cfg doctor
    if ($LASTEXITCODE -ne 0) { throw "doctor failed; not starting the pipeline" }
    # a freshly built corpus invalidates every earlier stage; model calls stay cached either way
    if ($SkipFetch) { & $py -m lexicon -c $Cfg run } else { & $py -m lexicon -c $Cfg run --from 01 }
    if ($LASTEXITCODE -ne 0) { throw "pipeline stopped with an error (re-run this script to resume)" }
    Write-Host "`nDone $(Get-Date -Format t). Report: work_podcasts\14_report\report.md   Lexicon: work_podcasts\out\lexicon.json"
}
finally {
    Stop-Transcript | Out-Null
    [void][Win.Power]::SetThreadExecutionState([uint32]2147483648)   # ES_CONTINUOUS: back to normal
}
