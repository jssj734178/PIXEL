# M2 acceptance test: 5 runs of `pixel run "open Notepad and type hello"`.
# A trial passes only if the run exits 0 (finish) AND an independent tree read
# of Notepad shows a Document whose value is exactly "hello".
# Done means 4/5 or better. Run it yourself from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\trial_notepad.ps1
# Uses `python -m pixel.cli` because the pixel.exe launcher is blocked by
# Application Control on this machine (see QUIRKS.md).

param([int]$Trials = 5)

$task = "open Notepad and type hello"
$passed = 0
$results = @()

for ($i = 1; $i -le $Trials; $i++) {
    Write-Host ""
    Write-Host "=== trial $i of $Trials ===" -ForegroundColor Cyan
    Read-Host "Close Notepad without saving (all windows), then press Enter"
    # A Notepad left open from the last trial would be focused and reused, so the
    # trial could pass without opening or typing anything (see QUIRKS.md).
    while (Get-Process -Name notepad -ErrorAction SilentlyContinue) {
        Read-Host "Notepad is still running. Close every Notepad window (Don't save), then press Enter"
    }

    # Tee keeps the output on screen and lets us spot a quota error.
    uv run python -m pixel.cli run $task --no-confirm -v | Tee-Object -Variable runOut | Out-Host
    $code = $LASTEXITCODE

    if ($code -ne 0 -and (($runOut -join "`n") -match 'exceeded your current quota|RESOURCE_EXHAUSTED')) {
        $results += "trial ${i}: STOPPED (model quota exceeded; this trial does not count)"
        Write-Host ""
        Write-Host "Gemini's quota is used up. Stopping now so no more requests are spent." -ForegroundColor Red
        Write-Host "Wait for the quota to reset (see 'Please retry in ...' above), then run this script again." -ForegroundColor Red
        $Trials = $i - 1
        break
    }

    if ($code -ne 0 -and (($runOut -join "`n") -match 'high demand|UNAVAILABLE|ReadTimeout|timed out')) {
        $results += "trial ${i}: STOPPED (model unavailable or timed out; this trial does not count)"
        Write-Host ""
        Write-Host "The model is overloaded or not answering. Stopping now so no more requests are spent." -ForegroundColor Red
        Write-Host "Try again in a few hours, or switch models in config.toml." -ForegroundColor Red
        $Trials = $i - 1
        break
    }

    $tree = (uv run python -m pixel.cli tree --window notepad) -join "`n"
    $typed = $tree -match '(?m)^\s*\[\d+\] Document .*= "hello"$'

    if ($code -eq 0 -and $typed) {
        $passed++
        $results += "trial ${i}: PASS"
        Write-Host "trial ${i}: PASS" -ForegroundColor Green
    } else {
        $why = @()
        if ($code -ne 0) { $why += "exit code $code" }
        if (-not $typed) { $why += 'no Document = "hello" in the Notepad tree' }
        $results += "trial ${i}: FAIL ($($why -join ', '))"
        Write-Host "trial ${i}: FAIL ($($why -join ', '))" -ForegroundColor Red
    }
}

Write-Host ""
$results | ForEach-Object { Write-Host $_ }
Write-Host "$passed/$Trials passed" -ForegroundColor Yellow
if ($Trials -lt 5) { Write-Host "Fewer than 5 trials ran, so this is not a complete M2 result." -ForegroundColor Yellow }
if ($passed -ge 4) { Write-Host "M2 done-when met (4/5 or better)." -ForegroundColor Green }
