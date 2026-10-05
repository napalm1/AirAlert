<#
  AirAlert uninstaller. Windows starts this from Settings > Installed apps.

    uninstall.ps1 [-Silent] [-PurgeData]

  Your settings and recorded history (in %LOCALAPPDATA%\AirAlert) are kept unless you choose to delete them.
#>
param(
    [switch]$Silent,
    [switch]$PurgeData,
    [string]$InstallDir = '',
    [switch]$Relaunched
)
$ErrorActionPreference = 'Stop'
Set-Location $env:TEMP          # a shell standing in the folder would keep it from being deleted

if (-not $Relaunched) {
    # Run from a copy in %TEMP% so this folder, including this script, can be deleted.
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $copy = Join-Path $env:TEMP ('AirAlert-uninstall-' + [guid]::NewGuid().ToString('N') + '.ps1')
    Copy-Item -LiteralPath $MyInvocation.MyCommand.Path -Destination $copy
    $list = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $copy,
              '-Relaunched', '-InstallDir', $here)
    if ($Silent) { $list += '-Silent' }
    if ($PurgeData) { $list += '-PurgeData' }
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $list -Wait -PassThru -WindowStyle Hidden
    Remove-Item -LiteralPath $copy -Force -ErrorAction SilentlyContinue
    exit $p.ExitCode
}

Add-Type -AssemblyName System.Windows.Forms
function Ask($text, $default) {
    $button = if ($default -eq 'No') { [System.Windows.Forms.MessageBoxDefaultButton]::Button2 } else { [System.Windows.Forms.MessageBoxDefaultButton]::Button1 }
    return [System.Windows.Forms.MessageBox]::Show($text, 'AirAlert', [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question, $button) -eq [System.Windows.Forms.DialogResult]::Yes
}
function Tell($text, $icon = 'Information') {
    if (-not $Silent) {
        [void][System.Windows.Forms.MessageBox]::Show($text, 'AirAlert', [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::$icon)
    }
}

# --- Make sure this really is an AirAlert folder: this script deletes it.
$dir = $InstallDir.TrimEnd('\')
if ($dir.Length -lt 8 -or -not (Test-Path -LiteralPath (Join-Path $dir 'AirAlert.exe')) -or
    -not (Test-Path -LiteralPath (Join-Path $dir '_internal'))) {
    Tell "This folder does not look like an AirAlert installation, so nothing was removed:`r`n$dir" 'Error'
    exit 2
}

if (-not $Silent) {
    if (-not (Ask "Remove AirAlert from this computer?`r`n`r`n$dir" 'Yes')) { exit 3 }
    if (-not $PurgeData) {
        $PurgeData = Ask ("Also delete your AirAlert settings and recorded history?`r`n`r`n" +
            "Choose No to keep them, for example if you reinstall later.") 'No'
    }
}

$info = $null
$infoFile = Join-Path $dir 'install.json'
if (Test-Path -LiteralPath $infoFile) { $info = Get-Content -LiteralPath $infoFile -Raw | ConvertFrom-Json }

# --- Stop AirAlert and its receiver programs if they are running from this folder.
foreach ($p in Get-Process -ErrorAction SilentlyContinue) {
    try { if ($p.Path -and $p.Path.StartsWith($dir + '\', [StringComparison]::OrdinalIgnoreCase)) { $p.Kill(); $p.WaitForExit(5000) | Out-Null } } catch { }
}

# --- Remove shortcuts, but only ones that point into this folder (never someone else's).
$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\AirAlert.lnk'   # "start with Windows"
$links = @($startup)
if ($info) { $links += @($info.startMenuLink, $info.desktopLink) }
$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links) {
    if ($link -and (Test-Path -LiteralPath $link)) {
        try {
            $target = $shell.CreateShortcut($link).TargetPath
            if ($target -and $target.StartsWith($dir + '\', [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $link -Force }
        } catch { }
    }
}

# --- Remove the entry in Settings > Installed apps.
$regKey = if ($info -and $info.regKey) { $info.regKey } else { 'Software\Microsoft\Windows\CurrentVersion\Uninstall\AirAlert' }
Remove-Item -LiteralPath ("HKCU:\" + $regKey) -Recurse -Force -ErrorAction SilentlyContinue

# --- Remove the program files (retrying while Windows lets go of them).
$failed = $false
for ($i = 0; $i -lt 8; $i++) {
    try { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction Stop; $failed = $false; break }
    catch { $failed = $true; Start-Sleep -Milliseconds 700 }
}

# --- Settings and history, only when asked. The Qt cache of compiled interface files is always safe to remove.
$data = if ($env:AIRALERT_DATA) { $env:AIRALERT_DATA } else { Join-Path $env:LOCALAPPDATA 'AirAlert' }
$cache = Join-Path $data 'AirAlert\cache'
if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache -Recurse -Force -ErrorAction SilentlyContinue }
if ($PurgeData -and $data.Length -ge 10 -and (Test-Path -LiteralPath $data) -and
    ((Test-Path -LiteralPath (Join-Path $data 'settings.json')) -or (Test-Path -LiteralPath (Join-Path $data 'history.sqlite')))) {
    Remove-Item -LiteralPath $data -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failed) {
    Tell "Some files in $dir could not be removed (they may be in use). Restart Windows and delete the folder by hand." 'Warning'
    exit 1
}
Tell ('AirAlert has been removed.' + $(if (-not $PurgeData) { "`r`nYour settings and history were kept." } else { '' }))
exit 0
