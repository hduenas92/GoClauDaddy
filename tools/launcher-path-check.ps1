#Requires -Version 5.1
# Launcher PATH-loop checks (P2-O). NEVER modifies the real USER PATH: every
# helper call here uses -DryRun, every bat run sets GCA_LAUNCHER_DRYRUN=1, and
# the real USER PATH (raw registry value AND its value kind) is snapshotted
# before/after and asserted unchanged.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $repo 'Launch GoClaudaddy.bat'
$helper = Join-Path $PSScriptRoot 'add-user-path.ps1'
$whereSrc = Join-Path $env:SystemRoot 'System32\where.exe'

function Get-UserPathRaw {
    $key = Get-Item 'HKCU:\Environment' -ErrorAction SilentlyContinue
    if ($null -eq $key) { return $null }
    return $key.GetValue('Path', $null, 'DoNotExpandEnvironmentNames')
}

function Get-UserPathKind {
    $key = Get-Item 'HKCU:\Environment' -ErrorAction SilentlyContinue
    if ($null -eq $key) { return '<missing>' }
    try { return $key.GetValueKind('Path') } catch { return '<missing>' }
}

$userPathRawBefore = Get-UserPathRaw
$userPathKindBefore = Get-UserPathKind

function Write-Check {
    param([string]$Name, [int]$Code)
    Write-Output ("CHECK {0} exit={1}" -f $Name, $Code)
}

function Invoke-BatDryRun {
    param(
        [string]$BatPath,
        [string]$FakeUserProfile,
        [string]$PathValue
    )
    $oldUserProfile = $env:USERPROFILE
    $oldPath = $env:PATH
    $oldDryRun = $env:GCA_LAUNCHER_DRYRUN
    try {
        $env:USERPROFILE = $FakeUserProfile
        $env:PATH = $PathValue
        $env:GCA_LAUNCHER_DRYRUN = '1'
        # stderr from the bat (e.g. a missing command) must be captured, not
        # turned into a terminating error by $ErrorActionPreference='Stop'.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = & $env:ComSpec /c call "`"$BatPath`"" 2>&1
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEAP
        }
        return [pscustomobject]@{
            Output   = ($output -join "`n")
            ExitCode = $exitCode
        }
    } finally {
        $env:USERPROFILE = $oldUserProfile
        $env:PATH = $oldPath
        $env:GCA_LAUNCHER_DRYRUN = $oldDryRun
    }
}

# --- T1: helper -DryRun string cases -------------------------------------------
$t1Failures = 0
$t1Details = New-Object 'System.Collections.Generic.List[string]'

function Get-HelperDryRun {
    param([string]$Dir, [string]$UserPath)
    # In-process so an empty-string -UserPath binds correctly (native command
    # invocation drops empty arguments).
    $out = & $helper -Dir $Dir -UserPath $UserPath -DryRun
    return ($out -join "`n")
}

function Assert-HelperEqual {
    param([string]$Case, [string]$Actual, [string]$Expected)
    if ([string]::Equals($Actual, $Expected, [System.StringComparison]::Ordinal)) { return }
    $script:t1Failures++
    $script:t1Details.Add(("T1 {0}: expected [{1}] got [{2}]" -f $Case, $Expected, $Actual))
}

# T1a: empty user PATH -> result is just the new dir
$r = Get-HelperDryRun -Dir 'E:\Claude\bin' -UserPath ''
Assert-HelperEqual 'empty user PATH' $r 'E:\Claude\bin'

# T1b: dir already present with different case and a trailing backslash -> unchanged
$r = Get-HelperDryRun -Dir 'c:\foo\bin' -UserPath 'C:\Foo\Bin\'
Assert-HelperEqual 'present (case + trailing backslash)' $r 'C:\Foo\Bin\'

# T1c: normal append
$r = Get-HelperDryRun -Dir 'E:\New' -UserPath 'C:\One;D:\Two'
Assert-HelperEqual 'normal append' $r 'C:\One;D:\Two;E:\New'

# T1d: existing entries preserved byte-for-byte (leading/trailing spaces and
# double trailing backslashes survive verbatim; only the new dir is appended)
$existing = ' C:\One ; D:\Two\\ ;C:\Three'
$r = Get-HelperDryRun -Dir 'E:\New' -UserPath $existing
Assert-HelperEqual 'existing entries preserved' $r ($existing + ';E:\New')

# T1e (P2-O2 a): a raw %USERPROFILE% entry must stay LITERAL and the new dir
# is appended after it.
$r = Get-HelperDryRun -Dir 'E:\New' -UserPath '%USERPROFILE%\AppData\Local\Microsoft\WindowsApps;C:\x'
Assert-HelperEqual 'raw %USERPROFILE% literal' $r '%USERPROFILE%\AppData\Local\Microsoft\WindowsApps;C:\x;E:\New'

# T1f (P2-O2 b): a target given as its expanded form, while the raw entry is
# %USERPROFILE%\.local\bin, is already present -> no duplicate appended.
$expandedLocalBin = [Environment]::ExpandEnvironmentVariables('%USERPROFILE%\.local\bin')
$r = Get-HelperDryRun -Dir $expandedLocalBin -UserPath '%USERPROFILE%\.local\bin'
Assert-HelperEqual 'expanded target matches raw entry' $r '%USERPROFILE%\.local\bin'

foreach ($d in $t1Details) { Write-Output $d }
Write-Check 'T1' $t1Failures

# --- T2/T3 fixtures ------------------------------------------------------------
$tmpRoot = [System.IO.Path]::GetTempPath()
$fake = Join-Path $tmpRoot ('gca-launcher-' + [guid]::NewGuid().ToString('N'))
$fakeLocalBin = Join-Path $fake '.local\bin'
New-Item -ItemType Directory -Path $fakeLocalBin -Force | Out-Null
$claudeExe = Join-Path $fakeLocalBin 'claude.exe'
Set-Content -Path $claudeExe -Value 'fake' -Encoding Ascii
Copy-Item -LiteralPath $whereSrc -Destination (Join-Path $fake 'where.exe')
# A fake py launcher so the bat's Python section (which runs before the claude
# section) finds a compatible Python without touching the network or PATH.
Set-Content -Path (Join-Path $fake 'py.cmd') -Value "@exit /b 0`r`n" -Encoding Ascii
# PATH contains no claude. The fake dir is first so the copied where.exe is the
# one that runs; System32 and the WindowsPowerShell dir follow so reg/powershell
# still resolve in dry-run.
$fakePath = "$fake;$env:SystemRoot\System32;$env:SystemRoot\System32\WindowsPowerShell\v1.0"

# --- T2: claude.exe present in fake USERPROFILE --------------------------------
$res2 = Invoke-BatDryRun -BatPath $bat -FakeUserProfile $fake -PathValue $fakePath
$line2 = "DRYRUN claude=$claudeExe"
$t2 = 0
if (-not ($res2.Output -match [regex]::Escape($line2))) {
    $t2 = 1
    Write-Output "T2 DETAIL: missing [$line2]"
    Write-Output ('T2 DETAIL: claude lines: ' + (($res2.Output -split "`n" | Where-Object { $_ -like 'DRYRUN claude=*' }) -join ' | '))
}
if ($res2.ExitCode -ne 0) {
    $t2 = 1
    Write-Output "T2 DETAIL: exit code $($res2.ExitCode)"
}
if ($res2.Output -match 'claude\.ai/install\.cmd|installing, this happens once') {
    $t2 = 1
    Write-Output 'T2 DETAIL: installer attempted in dry-run'
}
Write-Check 'T2' $t2

# --- T3: same fixture, no claude.exe -> NONE, and no installer attempt ---------
Remove-Item -LiteralPath $claudeExe -Force
$res3 = Invoke-BatDryRun -BatPath $bat -FakeUserProfile $fake -PathValue $fakePath
$t3 = 0
if (-not ($res3.Output -match 'DRYRUN claude=NONE')) {
    $t3 = 1
    Write-Output 'T3 DETAIL: missing DRYRUN claude=NONE'
    Write-Output ('T3 DETAIL: claude lines: ' + (($res3.Output -split "`n" | Where-Object { $_ -like 'DRYRUN claude=*' }) -join ' | '))
}
if ($res3.ExitCode -ne 0) {
    $t3 = 1
    Write-Output "T3 DETAIL: exit code $($res3.ExitCode)"
}
if ($res3.Output -match 'claude\.ai/install\.cmd|installing, this happens once|close this window') {
    $t3 = 1
    Write-Output 'T3 DETAIL: installer or relaunch message appeared in dry-run'
}
Write-Check 'T3' $t3

# --- T4: helper's internal raw read must equal the registry raw value ----------
# With NO -UserPath override the helper must read HKCU:\Environment\Path with
# DoNotExpandEnvironmentNames. The helper exposes that read as a RAW= line in
# dry-run (printed before the result line). T1 cannot catch a regression here:
# every T1 case injects -UserPath, so the real registry read is never
# exercised by T1. Only this path is.
$t4 = 0
$regRaw = Get-UserPathRaw

function Get-HelperRawLine {
    param([string]$HelperPath)
    $out = & $HelperPath -Dir 'E:\Claude\bin' -DryRun
    $rawLine = $out | Where-Object { $_ -like 'RAW=*' } | Select-Object -First 1
    if ($null -eq $rawLine) { return $null }
    return ([string]$rawLine).Substring(4)
}

$helperRaw = Get-HelperRawLine -HelperPath $helper
if ($null -eq $helperRaw) {
    $t4 = 1
    Write-Output 'T4 DETAIL: helper printed no RAW= line in dry-run'
} elseif (-not [string]::Equals([string]$helperRaw, [string]$regRaw, [System.StringComparison]::Ordinal)) {
    $t4 = 1
    Write-Output 'T4 DETAIL: helper raw read differs from registry raw value'
}
Write-Check 'T4' $t4

# --- RED (T2): temporarily disable the step-1 check and watch T2 die -----------
# The mutation points both `if exist "!CLAUDEBIN!" (` fallback checks at a path
# that can never exist. With the check disabled the dry-run must report NONE
# instead of the fake claude path, i.e. T2 must fail. The real bat is untouched;
# a temp copy is run and then deleted.
$original = [System.IO.File]::ReadAllText($bat)
$mutated = $original.Replace('if exist "!CLAUDEBIN!" (', 'if exist "!CLAUDEBIN!__RED_DISABLED__" (')
$redBat = Join-Path $repo 'Launch GoClaudaddy.red.bat'
[System.IO.File]::WriteAllText($redBat, $mutated)
try {
    $resRed = Invoke-BatDryRun -BatPath $redBat -FakeUserProfile $fake -PathValue $fakePath
} finally {
    Remove-Item -LiteralPath $redBat -Force -ErrorAction SilentlyContinue
}

$redT2Killed = ($resRed.Output -match 'DRYRUN claude=NONE') -and
               (-not ($resRed.Output -match [regex]::Escape($line2))) -and
               ($resRed.ExitCode -eq 0)
if ($mutated -eq $original) {
    $redT2Killed = $false
    Write-Output 'RED DETAIL: mutation did not change the bat'
}
if (-not $redT2Killed) {
    Write-Output 'RED DETAIL: mutation did not kill T2'
    Write-Output ('RED DETAIL: claude lines: ' + (($resRed.Output -split "`n" | Where-Object { $_ -like 'DRYRUN claude=*' }) -join ' | '))
}
# red exit != 0 is the DESIRED state: the mutated launcher failed to resolve.
if ($redT2Killed) { Write-Check 'red-t2' 1 } else { Write-Check 'red-t2' 0 }

# --- RED (T4): switch the helper read back to GetEnvironmentVariable -----------
# The mutation reverts the helper's read to the expanding API and drops the
# RAW= diagnostic line (the pre-fix helper emitted no such line). T4 must then
# fail because the raw read is no longer exposed/raw. The real helper is
# untouched; a temp copy is run and then deleted.
$helperOriginal = [System.IO.File]::ReadAllText($helper)
$helperMutated = $helperOriginal
$helperMutated = $helperMutated.Replace('$envKey.GetValue(''Path'', $null, ''DoNotExpandEnvironmentNames'')', '[Environment]::GetEnvironmentVariable(''Path'',''User'')')
$helperMutated = $helperMutated.Replace('Write-Output (''RAW='' + [string]$currentRaw)', '$null')
$mutationOk = ($helperMutated -ne $helperOriginal) -and
              (-not $helperMutated.Contains('DoNotExpandEnvironmentNames')) -and
              (-not $helperMutated.Contains('RAW='))
$redHelper = Join-Path $PSScriptRoot 'add-user-path.red.ps1'
[System.IO.File]::WriteAllText($redHelper, $helperMutated)
try {
    $helperRawRed = Get-HelperRawLine -HelperPath $redHelper
} finally {
    Remove-Item -LiteralPath $redHelper -Force -ErrorAction SilentlyContinue
}

$redT4Killed = ($null -eq $helperRawRed) -or
               (-not [string]::Equals([string]$helperRawRed, [string]$regRaw, [System.StringComparison]::Ordinal))
if (-not $mutationOk) {
    $redT4Killed = $false
    Write-Output 'RED DETAIL: helper mutation incomplete'
}
if (-not $redT4Killed) {
    Write-Output 'RED DETAIL: mutation did not kill T4'
    Write-Output ('RED DETAIL: mutated helper raw line: ' + $(if ($null -eq $helperRawRed) { '<none>' } else { $helperRawRed }))
}
if ($redT4Killed) { Write-Check 'red' 1 } else { Write-Check 'red' 0 }

# --- real USER PATH must be unchanged (raw value AND kind) ---------------------
Remove-Item -LiteralPath $fake -Recurse -Force -ErrorAction SilentlyContinue
$userPathRawAfter = Get-UserPathRaw
$userPathKindAfter = Get-UserPathKind
$valueUnchanged = [string]::Equals([string]$userPathRawBefore, [string]$userPathRawAfter, [System.StringComparison]::Ordinal)
$kindUnchanged = [string]::Equals([string]$userPathKindBefore, [string]$userPathKindAfter, [System.StringComparison]::Ordinal)
Write-Output ("CHECK userpath-unchanged=" + $(if ($valueUnchanged) { 'yes' } else { 'no' }) + ' (raw value)')
Write-Output ("CHECK userpath-unchanged=" + $(if ($kindUnchanged) { 'yes' } else { 'no' }) + ' (kind)')

$overall = 0
if ($t1Failures -ne 0) { $overall = 1 }
if ($t2 -ne 0) { $overall = 1 }
if ($t3 -ne 0) { $overall = 1 }
if ($t4 -ne 0) { $overall = 1 }
if (-not $redT2Killed) { $overall = 1 }
if (-not $redT4Killed) { $overall = 1 }
if (-not $valueUnchanged) { $overall = 1 }
if (-not $kindUnchanged) { $overall = 1 }
exit $overall
