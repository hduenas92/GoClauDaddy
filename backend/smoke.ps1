<#
    smoke.ps1 — cold-boot check for GoClaudaddy.

    The pytest suite proves units work; it does not prove the real app starts.
    This boots the actual server (or uses one already running), hits the
    endpoints the UI depends on at load, and checks the log for errors.

    Read-only: GETs only, no writes. Migrations are idempotent, so pointing this
    at the real data dir is safe.

    Three result kinds:
      PASS  - fine
      FAIL  - unexpected; exits 1
      GAP   - a known, tracked shortfall. Reported loudly, does NOT exit 1.
              When a GAP starts passing, the script says so — delete the marker.

    Usage:  ./smoke.ps1            from backend/
    Exit:   0 = no unexpected failures, 1 = at least one FAIL
#>

$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:8765'
$py = '..\.venv\Scripts\python.exe'
$logPath = Join-Path $HOME '.goclaudaddy\logs\app.log'

$failures = @()
$gaps = @()
$fixedGaps = @()
$startedByUs = $false
$proc = $null

function Check($name, [scriptblock]$test) {
    try {
        $r = & $test
        if ($r -eq $true) { "  PASS  $name"; return }
        "  FAIL  $name -> $r"; $script:failures += $name
    } catch { "  FAIL  $name -> $($_.Exception.Message)"; $script:failures += $name }
}

# A known shortfall tracked in goclaudaddy-v1-plan.md. Does not fail the run.
function Gap($name, $task, [scriptblock]$test) {
    try {
        $r = & $test
        if ($r -eq $true) {
            "  FIXED $name  <- GAP ($task) now passes; remove the Gap marker"
            $script:fixedGaps += $name; return
        }
        "  GAP   $name [$task] -> $r"; $script:gaps += $name
    } catch { "  GAP   $name [$task] -> $($_.Exception.Message)"; $script:gaps += $name }
}

function Get-Json($path) { Invoke-RestMethod -Uri "$base$path" -TimeoutSec 10 -ErrorAction Stop }

"=== GoClaudaddy smoke check ==="
""

# --- 1. server up, or start one -------------------------------------------
$alreadyUp = $false
try {
    Invoke-WebRequest -Uri "$base/api/config" -TimeoutSec 3 -UseBasicParsing | Out-Null
    $alreadyUp = $true
    "Server already running on 8765 — testing against it (will not stop it)."
} catch {
    "No server on 8765 — starting one."
    if (-not (Test-Path $py)) { "  FAIL  venv python not found at $py"; exit 1 }
    $proc = Start-Process -FilePath $py -ArgumentList 'run.py' -PassThru -WindowStyle Hidden
    $startedByUs = $true
    $up = $false; $waited = 0
    foreach ($i in 1..30) {
        Start-Sleep -Milliseconds 500; $waited = $i * 0.5
        try { Invoke-WebRequest -Uri "$base/api/config" -TimeoutSec 2 -UseBasicParsing | Out-Null; $up = $true; break } catch { }
    }
    if (-not $up) {
        "  FAIL  server did not answer within 15s"
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
        exit 1
    }
    "  server answered after ~${waited}s"
}
""

# --- 2. endpoints the UI needs at load ------------------------------------
"Endpoints:"
Check 'GET /  (frontend index)' { (Invoke-WebRequest -Uri "$base/" -TimeoutSec 10 -UseBasicParsing).StatusCode -eq 200 }
Check 'GET /api/config' { $null -ne (Get-Json '/api/config').models }
Check 'GET /api/server/stats' { $null -ne (Get-Json '/api/server/stats') }
Check 'GET /api/conversations' { $null -ne (Get-Json '/api/conversations') }
Check 'GET /api/projects' { $null -ne (Get-Json '/api/projects') }
Check 'GET /api/flow-templates (8 built-ins seeded)' {
    $t = Get-Json '/api/flow-templates'
    if ($t.Count -ge 8) { $true } else { "expected >=8, got $($t.Count)" }
}
""

# --- 3. config contract the frontend reads -------------------------------
# NOTE: the response key for a model's id is 'value', not 'id' (config.py).
"Config contract:"
$cfg = Get-Json '/api/config'
Check 'models list is non-empty' { $cfg.models.Count -gt 0 }
Check 'every model exposes context_window' {
    $m = @($cfg.models | Where-Object { $null -eq $_.context_window } | ForEach-Object { $_.value })
    if ($m.Count -eq 0) { $true } else { "missing on: $($m -join ', ')" }
}
Check 'default_model is one of the listed models' {
    if ($cfg.models.value -contains $cfg.default_model) { $true } else { "default_model '$($cfg.default_model)' not in list" }
}
Check 'all 6 permission modes exposed' {
    $want = @('acceptEdits', 'auto', 'bypassPermissions', 'manual', 'dontAsk', 'plan')
    $miss = @($want | Where-Object { $cfg.permission_modes -notcontains $_ })
    if ($miss.Count -eq 0) { $true } else { "missing: $($miss -join ', ')" }
}
# Promoted from Gap to Check on 2026-09-16: v1-plan task 2-C shipped the rates,
# both gaps reported FIXED, so these are now permanent regressions guards.
foreach ($field in 'input_rate', 'output_rate') {
    Check "every model exposes $field" {
        $m = @($cfg.models | Where-Object { $null -eq $_.$field } | ForEach-Object { $_.value })
        if ($m.Count -eq 0) { $true } else { "missing on: $($m -join ', ')" }
    }
}
Check 'model rates are positive numbers' {
    $bad = @($cfg.models | Where-Object { $_.input_rate -le 0 -or $_.output_rate -le 0 } | ForEach-Object { $_.value })
    if ($bad.Count -eq 0) { $true } else { "non-positive rate on: $($bad -join ', ')" }
}
""

# --- 4. static assets -----------------------------------------------------
"Static assets:"
Check '/static/css/base.css served' { (Invoke-WebRequest -Uri "$base/static/css/base.css" -TimeoutSec 10 -UseBasicParsing).StatusCode -eq 200 }
Check '/static/js/main.js sends Cache-Control: no-cache' {
    $cc = (Invoke-WebRequest -Uri "$base/static/js/main.js" -TimeoutSec 10 -UseBasicParsing).Headers['Cache-Control']
    if ($cc -match 'no-cache') { $true } else { "got '$cc'" }
}
""

# --- 5. log ---------------------------------------------------------------
"Log:"
Check 'no ERROR/CRITICAL in the last 200 log lines' {
    if (-not (Test-Path $logPath)) { return "log not found at $logPath" }
    $bad = @(Get-Content $logPath -Tail 200 | Select-String -Pattern '\[(ERROR|CRITICAL)\]')
    if ($bad.Count -eq 0) { $true } else { "$($bad.Count) line(s), first: $($bad[0].Line.Trim())" }
}
if ($startedByUs) {
    # Only meaningful for a server we just booted — on a long-running one the
    # startup line has long since scrolled out of the tail window.
    Check 'startup line present in log' {
        if (-not (Test-Path $logPath)) { return 'log not found' }
        if (Get-Content $logPath -Tail 100 | Select-String -Pattern 'backend started' -Quiet) { $true }
        else { 'no "backend started" line after boot' }
    }
} else {
    "  SKIP  startup line check (server was already running, not booted by us)"
}
""

# --- teardown -------------------------------------------------------------
if ($startedByUs -and $proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
    "Stopped the server we started (PID $($proc.Id))."
} elseif ($alreadyUp) {
    "Left the pre-existing server running."
}
""

# --- result ---------------------------------------------------------------
if ($fixedGaps.Count -gt 0) { "$($fixedGaps.Count) tracked gap(s) now pass — remove their Gap markers: $($fixedGaps -join ', ')" }
if ($gaps.Count -gt 0) { "$($gaps.Count) known gap(s): $($gaps -join ', ')" }

if ($failures.Count -eq 0) {
    "RESULT: PASS ($($gaps.Count) known gap(s), 0 unexpected failures)"
    exit 0
}
"RESULT: FAIL — $($failures.Count) unexpected: $($failures -join ', ')"
exit 1
