<#
  End-to-end test of the BUILT one-file installer (release\AirAlert-Setup-<version>.exe): silent install, the installed
  app's own self-test, update over a running app, a folder that holds other files, rollback after failures, path
  rules, and the uninstaller run exactly as Windows runs it. Start menu / desktop / "Installed apps" entries and the
  data folder are redirected to a scratch area, so your real profile is not touched.

      powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_installer.ps1 [-Setup path\to\setup.exe]

  Exit code = number of failed checks. Build the installer first with tools\make_installer.py.
#>
param([string]$Setup = '')
$ErrorActionPreference = 'Stop'
if (-not $Setup) { $Setup = (Get-ChildItem "$PSScriptRoot\..\release\AirAlert-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName }
if (-not $Setup -or -not (Test-Path $Setup)) { throw 'No installer found. Run tools\make_installer.py first.' }
if (Get-Process AirAlert -ErrorAction SilentlyContinue) { throw 'AirAlert is running; close it first so the test cannot disturb it.' }

$root = Join-Path $env:TEMP ('aa-e2e-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$regRoot = 'Software\AirAlertSetupTest'
New-Item -ItemType Directory $root, "$root\startmenu", "$root\desktop", "$root\data" | Out-Null
'{"home":[1,2],"setup_done":true}' | Set-Content "$root\data\settings.json"     # stands in for a user's settings
$env:AIRALERT_SETUP_STARTMENU = "$root\startmenu"; $env:AIRALERT_SETUP_DESKTOP = "$root\desktop"
$env:AIRALERT_SETUP_REGKEY = "$regRoot\Uninstall\AirAlert"; $env:AIRALERT_DATA = "$root\data"
$dir = "$root\Programs\AirAlert"
$sh = New-Object -ComObject WScript.Shell
$bad = 0
function Check($name, $ok) { "{0,-70} {1}" -f $name, $(if ($ok) { 'ok' } else { 'FAILED' }); if (-not $ok) { $script:bad++ } }
function Files($d) { (Get-ChildItem $d -Recurse -File | Measure-Object).Count }
# Wait for the process itself, not its descendants (Start-Process -Wait would also wait for an app that setup launched).
function Run($exe, $argList) { $p = Start-Process $exe -ArgumentList $argList -PassThru -WindowStyle Hidden; $p.WaitForExit(); $p }
function Uninstall($folder, $flags) { Run 'powershell.exe' (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$folder\uninstall.ps1", '-Silent') + $flags) }
$app = $null
try {
    "installer: $Setup ({0:N0} MB)" -f ((Get-Item $Setup).Length / 1MB)

    "--- 1. silent install"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Run $Setup @('/S', "/D=$dir")
    "took {0:N1} s" -f $sw.Elapsed.TotalSeconds
    Check 'exit code 0' ($p.ExitCode -eq 0)
    Check 'AirAlert.exe, _internal and both decoders installed' ((Test-Path "$dir\AirAlert.exe") -and (Test-Path "$dir\_internal\PySide6") -and (Test-Path "$dir\_internal\vendor\adsb\dump1090.exe") -and (Test-Path "$dir\_internal\vendor\ais\AIS-catcher.exe"))
    Check 'Visual C++ runtime sits beside both decoders' ((Test-Path "$dir\_internal\vendor\adsb\MSVCP140.dll") -and (Test-Path "$dir\_internal\vendor\ais\VCRUNTIME140_1.dll"))
    Check 'uninstall.ps1 and install.json present, no setup-meta.json' ((Test-Path "$dir\uninstall.ps1") -and (Test-Path "$dir\install.json") -and -not (Test-Path "$dir\setup-meta.json"))
    Check 'no .new / .old leftovers' ((-not (Test-Path "$dir.new")) -and (-not (Test-Path "$dir.old")))
    $installedFiles = Files $dir
    Check "a complete copy ($installedFiles files)" ($installedFiles -ge 2000)
    Check 'Start menu shortcut -> installed exe' ($sh.CreateShortcut("$root\startmenu\AirAlert.lnk").TargetPath -eq "$dir\AirAlert.exe")
    Check 'Desktop shortcut -> installed exe' ($sh.CreateShortcut("$root\desktop\AirAlert.lnk").TargetPath -eq "$dir\AirAlert.exe")
    $reg = Get-ItemProperty "HKCU:\$regRoot\Uninstall\AirAlert"
    Check 'Installed-apps entry: name, version, location, uninstall command' (($reg.DisplayName -eq 'AirAlert') -and $reg.DisplayVersion -and ($reg.InstallLocation -eq $dir) -and ($reg.UninstallString -like "*$dir\uninstall.ps1*"))
    $out = "$root\selftest"
    $t = Run "$dir\AirAlert.exe" @('--self-test', $out)
    $results = Get-Content "$out\results.json" -Raw | ConvertFrom-Json
    $names = @($results.PSObject.Properties | Where-Object { $_.Name -ne 'qml_warnings' })
    Check "the installed app passes its own self-test ($($names.Count) checks, no interface warnings)" (($t.ExitCode -eq 0) -and (@($names | Where-Object { $_.Value -ne 'pass' }).Count -eq 0) -and (@($results.qml_warnings).Count -eq 0))

    "--- 1b. /LAUNCH starts the installed app (the same path as 'open when setup finishes')"
    $env:AIRALERT_INSTANCE = 'AirAlert-e2e-launch'
    $p = Run $Setup @('/S', '/LAUNCH', "/D=$dir")
    Check 'exit code 0' ($p.ExitCode -eq 0)
    $launched = $null
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline -and -not $launched) {
        $launched = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq "$dir\AirAlert.exe" } | Select-Object -First 1
        Start-Sleep -Milliseconds 250
    }
    Check 'the installed AirAlert.exe is running' ($null -ne $launched)
    if ($launched) {
        Start-Sleep -Seconds 6
        $still = Get-CimInstance Win32_Process -Filter "ProcessId=$($launched.ProcessId)"
        Check 'it is still running after 6 s (not confused by the setup environment)' ($null -ne $still)
        $logLine = if (Test-Path "$root\data\AirAlert.log") { (Get-Content "$root\data\AirAlert.log" | Select-String 'starting; data folder' | Select-Object -Last 1) } else { $null }
        Check 'it wrote its startup line to the log' ($null -ne $logLine)
        Stop-Process -Id $launched.ProcessId -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2
    }

    "--- 2. update while AirAlert is running from the folder"
    $env:AIRALERT_INSTANCE = 'AirAlert-e2e-update'
    $app = Start-Process "$dir\AirAlert.exe" -PassThru; Start-Sleep -Seconds 6
    Check 'the app is running before the update' (-not $app.HasExited)
    $before = (Get-Item "$dir\install.json").LastWriteTime; Start-Sleep -Milliseconds 1100
    $p = Run $Setup @('/S', "/D=$dir")
    Check 'update exit code 0' ($p.ExitCode -eq 0)
    $app.Refresh(); Check 'the running app was closed by the update' $app.HasExited
    Check 'a fresh install record was written' ((Get-Item "$dir\install.json").LastWriteTime -gt $before)
    Check 'complete copy, no leftovers' (((Files $dir) -eq $installedFiles) -and (-not (Test-Path "$dir.new")) -and (-not (Test-Path "$dir.old")))
    Check 'user settings untouched' ((Get-Content "$root\data\settings.json" -Raw) -like '*setup_done*')

    "--- 3. a folder that already holds other files is never installed into directly"
    New-Item -ItemType Directory "$root\MyDocs" | Out-Null; 'precious' | Set-Content "$root\MyDocs\notes.txt"
    $p = Run $Setup @('/S', "/D=$root\MyDocs")
    Check 'installed into an AirAlert subfolder instead' (($p.ExitCode -eq 0) -and (Test-Path "$root\MyDocs\AirAlert\AirAlert.exe"))
    Check 'the other files are untouched' ((Get-Content "$root\MyDocs\notes.txt" -Raw) -like 'precious*')
    $p = Run $Setup @('/S', "/D=$dir")                       # point the shortcuts and registry back at the first install
    Check 'back on the first folder' ($p.ExitCode -eq 0)

    "--- 4. rollback"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $bad1 = "$root\bad-payload.zip"
    $zip = [IO.Compression.ZipFile]::Open($bad1, 'Create')
    $e = $zip.CreateEntry('setup-meta.json'); $w = New-Object IO.StreamWriter($e.Open()); $w.Write('{"name":"AirAlert","version":"9.9.9","size_bytes":1000,"files":1}'); $w.Close()
    $e = $zip.CreateEntry('junk.txt'); $w = New-Object IO.StreamWriter($e.Open()); $w.Write('junk'); $w.Close(); $zip.Dispose()
    $env:AIRALERT_SETUP_PAYLOAD = $bad1
    $p = Run $Setup @('/S', "/D=$dir")
    Check 'damaged setup file: reports failure (exit code 1)' ($p.ExitCode -eq 1)
    Check '  ...and the existing install is intact' (((Files $dir) -eq $installedFiles) -and ((Get-Content "$dir\install.json" -Raw | ConvertFrom-Json).version -eq $reg.DisplayVersion))
    Remove-Item Env:AIRALERT_SETUP_PAYLOAD
    'file' | Set-Content "$root\blocker"; $env:AIRALERT_SETUP_STARTMENU = "$root\blocker\sub"
    $marker = "$dir\_internal\marker-of-old-version.txt"; 'old' | Set-Content $marker
    $p = Run $Setup @('/S', "/D=$dir")
    Check 'failure after the swap: reports failure (exit code 1)' ($p.ExitCode -eq 1)
    Check '  ...and the previous version was put back' ((Test-Path $marker) -and (-not (Test-Path "$dir.new")) -and (-not (Test-Path "$dir.old")))
    $env:AIRALERT_SETUP_STARTMENU = "$root\startmenu"; Remove-Item $marker

    "--- 5. path rules"
    $p = Run $Setup @('/S', '/D=relative\path'); Check 'a relative path is rejected' ($p.ExitCode -eq 1)
    $p = Run $Setup @('/S', '/D=C:\'); Check 'a whole drive is rejected' ($p.ExitCode -eq 1)
    $long = "$root\" + ('x' * 100); $p = Run $Setup @('/S', "/D=$long"); Check 'an over-long path is rejected, nothing created' (($p.ExitCode -eq 1) -and (-not (Test-Path $long)))

    "--- 6. uninstall, run exactly as Windows runs it"
    New-Item -ItemType Directory "$root\notapp" | Out-Null
    Copy-Item "$dir\uninstall.ps1" "$root\notapp\uninstall.ps1"; 'keep me' | Set-Content "$root\notapp\precious.txt"
    $p = Uninstall "$root\notapp" @()
    Check 'refuses to delete a folder that is not an AirAlert installation' (($p.ExitCode -eq 2) -and (Test-Path "$root\notapp\precious.txt"))
    $foreign = $sh.CreateShortcut("$root\desktop\AirAlert.lnk"); $foreign.TargetPath = "$env:SystemRoot\System32\notepad.exe"; $foreign.Save()
    New-Item -ItemType Directory "$root\data\AirAlert\cache" -Force | Out-Null; 'x' | Set-Content "$root\data\AirAlert\cache\x.qmlc"
    $quiet = (Get-ItemProperty "HKCU:\$regRoot\Uninstall\AirAlert").QuietUninstallString
    $p = Run 'cmd.exe' @('/c', "`"$quiet`"")
    Check 'the registered quiet-uninstall command works (exit code 0)' ($p.ExitCode -eq 0)
    Check 'program folder, Start menu shortcut and Installed-apps entry removed' ((-not (Test-Path $dir)) -and (-not (Test-Path "$root\startmenu\AirAlert.lnk")) -and (-not (Test-Path "HKCU:\$regRoot\Uninstall\AirAlert")))
    Check "someone else's shortcut with the same name is left alone" ($sh.CreateShortcut("$root\desktop\AirAlert.lnk").TargetPath -like '*notepad.exe')
    Check 'settings and history kept; compiled-interface cache cleaned' ((Test-Path "$root\data\settings.json") -and (-not (Test-Path "$root\data\AirAlert\cache")))
    Check 'the separate install in the other folder is still there' (Test-Path "$root\MyDocs\AirAlert\AirAlert.exe")
    Remove-Item "$root\desktop\AirAlert.lnk" -Force
    $p = Run $Setup @('/S', '/NODESKTOP', "/D=$dir")
    Check 'reinstall after an uninstall works; /NODESKTOP makes no desktop shortcut' (($p.ExitCode -eq 0) -and (Test-Path "$dir\AirAlert.exe") -and (-not (Test-Path "$root\desktop\AirAlert.lnk")))
    $p = Uninstall $dir @('-PurgeData')
    Check 'uninstall with -PurgeData also deletes settings and history' (($p.ExitCode -eq 0) -and (-not (Test-Path $dir)) -and (-not (Test-Path "$root\data")))
    $p = Uninstall "$root\MyDocs\AirAlert" @()
    Check 'the other install uninstalls; files beside it survive' (($p.ExitCode -eq 0) -and (-not (Test-Path "$root\MyDocs\AirAlert")) -and (Test-Path "$root\MyDocs\notes.txt"))
}
finally {
    if ($app -and -not $app.HasExited) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }   # only the process this script started
    try { [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($regRoot, $false) } catch { }
    try { [IO.Directory]::Delete($root, $true) } catch { Write-Warning "could not remove $root" }
}
"failed checks: $bad"
exit $bad
