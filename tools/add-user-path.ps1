param(
    [Parameter(Mandatory = $true)]
    [string]$Dir,

    [switch]$DryRun,

    # Test-only override: when supplied, this string is used instead of reading
    # the real USER PATH from the registry. It is only honored together with
    # -DryRun so a test can never accidentally write a synthetic PATH to the
    # real registry.
    [string]$UserPath,

    # Test-only override for the registry value kind. Only honored together
    # with -DryRun, so it can never change how the real registry is written.
    [ValidateSet('String', 'ExpandString')]
    [string]$UserPathKind
)

$ErrorActionPreference = 'Stop'

if (($PSBoundParameters.ContainsKey('UserPath') -or $PSBoundParameters.ContainsKey('UserPathKind')) -and -not $DryRun) {
    Write-Error '-UserPath and -UserPathKind are test-only overrides and may only be used with -DryRun.'
    exit 1
}

$userPathSupplied = $PSBoundParameters.ContainsKey('UserPath')

# Read the RAW (unexpanded) value and its kind from HKCU:\Environment.
# [Environment]::GetEnvironmentVariable('Path','User') must NOT be used for
# this read: on .NET Framework (Windows PowerShell 5.1) it expands
# REG_EXPAND_SZ references, so a Windows default entry such as
# %USERPROFILE%\AppData\Local\Microsoft\WindowsApps would come back as
# C:\Users\<user>\AppData\... and writing it back would change that entry
# and can change the value type from REG_EXPAND_SZ to REG_SZ.
$envKey = Get-Item 'HKCU:\Environment' -ErrorAction SilentlyContinue
$currentRaw = $null
$currentKind = $null
if ($null -ne $envKey) {
    $currentRaw = $envKey.GetValue('Path', $null, 'DoNotExpandEnvironmentNames')
    try {
        $currentKind = $envKey.GetValueKind('Path')
    } catch {
        $currentKind = $null
    }
}
if ($userPathSupplied) {
    $currentRaw = $UserPath
    if ($PSBoundParameters.ContainsKey('UserPathKind')) {
        $currentKind = $UserPathKind
    }
}

$entries = New-Object 'System.Collections.Generic.List[string]'
if ($null -ne $currentRaw -and $currentRaw -ne '') {
    foreach ($entry in ($currentRaw -split ';')) {
        $entries.Add([string]$entry)
    }
}

# A trailing "\" on either side must not make the same directory look new,
# and comparison is case-insensitive because the registry PATH is a
# case-insensitive Windows list.
$target = $Dir.TrimEnd('\')

$alreadyPresent = $false
foreach ($entry in $entries) {
    $normalized = [string]$entry
    $normalized = $normalized.TrimEnd('\')
    if ([string]::Equals($normalized, $target, [System.StringComparison]::OrdinalIgnoreCase)) {
        $alreadyPresent = $true
        break
    }
    # Also compare the expanded form of the raw entry, so a target given as
    # C:\Users\<user>\.local\bin counts as already present when the raw entry
    # is %USERPROFILE%\.local\bin.
    $expanded = [Environment]::ExpandEnvironmentVariables($normalized)
    $expanded = $expanded.TrimEnd('\')
    if ([string]::Equals($expanded, $target, [System.StringComparison]::OrdinalIgnoreCase)) {
        $alreadyPresent = $true
        break
    }
}

if (-not $alreadyPresent) {
    $entries.Add($target)
}

$result = ($entries -join ';')

if ($DryRun) {
    if (-not $userPathSupplied) {
        # Expose the raw read for T4 in launcher-path-check.ps1. Printed FIRST
        # so the bat's dry-run preview, which captures the LAST line, keeps
        # seeing the result string.
        Write-Output ('RAW=' + [string]$currentRaw)
    }
    Write-Output $result
} else {
    # Choose the kind to write. An existing ExpandString kind is preserved even
    # when the result no longer contains %, a %VAR% in the result forces
    # ExpandString, and a plain value keeps String. No previous value means
    # the Windows default kind: ExpandString.
    if ($null -eq $currentKind) {
        $writeType = 'ExpandString'
    } elseif ($currentKind -eq 'ExpandString') {
        $writeType = 'ExpandString'
    } elseif ($result.Contains('%')) {
        $writeType = 'ExpandString'
    } else {
        $writeType = 'String'
    }

    if ($null -eq $envKey) {
        New-Item -Path 'HKCU:\Environment' -Force | Out-Null
    }
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value $result -Type $writeType

    # Broadcast WM_SETTINGCHANGE with lParam "Environment" so that
    # Explorer-launched terminals see the change without logging off.
    Add-Type -Namespace Win32 -Name NativeMethods -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
    $HWND_BROADCAST = [IntPtr]0xffff
    $WM_SETTINGCHANGE = 0x1A
    $SMTO_ABORTIFHUNG = 0x0002
    $sendResult = [UIntPtr]::Zero
    [Win32.NativeMethods]::SendMessageTimeout($HWND_BROADCAST, $WM_SETTINGCHANGE, [UIntPtr]::Zero, 'Environment', $SMTO_ABORTIFHUNG, 5000, [ref]$sendResult) | Out-Null
}
