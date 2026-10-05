"""Start AirAlert at sign-in via a shortcut in the user's Windows Startup folder."""
import os
import subprocess
import sys
from pathlib import Path

SHORTCUT = 'AirAlert.lnk'
FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
# Paths travel in environment variables, never inside the PowerShell command text.
SCRIPT = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AA_LNK);"
          "$s.TargetPath=$env:AA_TARGET;$s.Arguments=$env:AA_ARGS;$s.WorkingDirectory=$env:AA_DIR;"
          "$s.IconLocation=$env:AA_TARGET+',0';$s.Description='AirAlert (starts in the tray)';$s.Save()")


def startup_folder():
    override = os.environ.get('AIRALERT_STARTUP_DIR')
    if override:
        return Path(override)
    return Path(os.environ.get('APPDATA', str(Path.home()))) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'


def shortcut_path():
    return startup_folder() / SHORTCUT


def launch_command():
    """(target, arguments, working folder) that starts AirAlert hidden in the tray."""
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable)
        return exe, '--minimized', exe.parent
    main = Path(__file__).resolve().parents[1] / 'main.py'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    return (pythonw if pythonw.exists() else Path(sys.executable)), f'"{main}" --minimized', main.parent


def is_enabled():
    return shortcut_path().exists()


def set_enabled(enabled):
    """Create or remove the startup shortcut. Returns '' or a readable error."""
    path = shortcut_path()
    if not enabled:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            return f'Could not remove the startup shortcut: {e}'
        return ''
    target, arguments, folder = launch_command()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, AA_LNK=str(path), AA_TARGET=str(target), AA_ARGS=arguments, AA_DIR=str(folder))
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', SCRIPT], env=env,
                                capture_output=True, text=True, timeout=30, creationflags=FLAGS)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f'Could not create the startup shortcut: {e}'
    if result.returncode != 0 or not path.exists():
        return 'Could not create the startup shortcut. ' + (result.stderr or '').strip()[:200]
    return ''
