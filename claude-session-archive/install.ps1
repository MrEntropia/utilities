# install.ps1 [-Archive DIR] [-Uninstall]       Windows (PowerShell 5.1 or 7)
#
# Finds Python 3.8+ (installing it with winget if missing), then runs
#   claude_session_archive.py install [--archive DIR]
# which turns off transcript auto-deletion, adds the daily sync hook and runs a first sync.
# Also puts the claude-session-archive command on your user PATH.
#
# If scripts are blocked, run it as:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
param(
    [string]$Archive,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-PythonExe([string]$exe, [string[]]$pre = @()) {
    # The interpreter's real path if it is 3.8+ and not a Microsoft Store build. Store Python
    # (and its WindowsApps placeholder) runs in a package sandbox that can redirect writes
    # under %APPDATA%, so the files this tool installs there might not be where cmd.exe looks.
    try {
        $out = & $exe @pre -c "import sys; print(sys.executable if sys.version_info >= (3, 8) else '')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $path = "$out".Trim()
        if (-not $path -or $path -like '*\WindowsApps\*') { return $null }
        return $path
    } catch { return $null }
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = Get-PythonExe 'py' @('-3')
        if ($p) { return $p }
    }
    foreach ($cmd in Get-Command python.exe, python3.exe -All -ErrorAction SilentlyContinue) {
        if ($cmd.Source -like '*\WindowsApps\*') { continue }
        $p = Get-PythonExe $cmd.Source
        if ($p) { return $p }
    }
    return $null
}

function Get-RawUserPath {
    # Read without expanding %VARIABLES%, so writing it back keeps them as they were.
    $key = Get-Item 'HKCU:\Environment'
    return [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
}

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

$py = Find-Python
if (-not $py) {
    Write-Host 'Python 3.8+ not found; installing it for this user with winget.'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'winget is not available. Install Python 3 from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.'
    }
    winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    $py = Find-Python
    if (-not $py) { throw 'Python was installed but is not visible yet. Open a new PowerShell window and run this again.' }
}
Write-Host "using $py ($(& $py --version 2>&1))"

$script = Join-Path $here 'claude_session_archive.py'
if ($Uninstall) { & $py $script uninstall; exit $LASTEXITCODE }

$argv = @($script, 'install')
if ($Archive) {
    # PowerShell 5.1 passes "G:\My Drive\" to native programs as G:\My Drive" (trailing backslash
    # escapes the quote), so drop trailing separators.
    $argv += @('--archive', $Archive.TrimEnd('\', '/'))
}
$env:CLAUDE_SESSION_ARCHIVE_PYTHON = $py
& $py @argv
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Put the folder holding claude-session-archive.cmd on the user PATH, as an expandable string.
$bin = Join-Path $env:APPDATA 'claude-session-archive'
$userPath = Get-RawUserPath
$entries = @($userPath -split ';' | Where-Object { $_ })
if ($entries -notcontains $bin) {
    $new = (@($entries) + $bin) -join ';'
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value $new -Type ExpandString
    # Writing the registry directly does not tell Explorer; setting and clearing a variable
    # through .NET broadcasts WM_SETTINGCHANGE so new terminals pick up the PATH.
    [Environment]::SetEnvironmentVariable('CLAUDE_SESSION_ARCHIVE_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('CLAUDE_SESSION_ARCHIVE_REFRESH', $null, 'User')
    Write-Host "added $bin to your PATH (open a new terminal to use claude-session-archive)"
}
