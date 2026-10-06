"""AirAlert Setup: a single-file installer for one Windows account (no administrator rights needed).

    AirAlert-Setup-x.y.z.exe                     graphical setup
    AirAlert-Setup-x.y.z.exe /S [/D=C:\\folder]   silent install (also /NODESKTOP, /LAUNCH)

The application files travel inside this program as one compressed archive. Setup unpacks them next to the
final folder, swaps them in only when everything arrived, and rolls back if anything goes wrong, so a failed
install or update never leaves a broken AirAlert behind. Settings and history live in %LOCALAPPDATA%\\AirAlert
and are never touched. Windows' "Installed apps" list runs the uninstall.ps1 that setup puts in the folder.
"""
import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import winreg
import zipfile
from datetime import datetime
from pathlib import Path

APP = 'AirAlert'
EXE = 'AirAlert.exe'
UNINSTALL_SCRIPT = 'uninstall.ps1'
REG_PATH = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\AirAlert'
MAX_FOLDER_CHARS = 110      # the longest path inside the app is 128 characters and Windows stops at 260
CREATE_NO_WINDOW = 0x08000000
LOG_FILE = Path(os.environ.get('TEMP') or '.') / 'AirAlert-setup.log'


class SetupError(Exception):
    """A problem with a readable explanation for the person installing."""


class Cancelled(Exception):
    pass


def log(message):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n')
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------- locations
def resource(relative):
    """A file bundled inside this program (or next to the source when run from a checkout)."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / relative


def payload_path():
    override = os.environ.get('AIRALERT_SETUP_PAYLOAD')
    return Path(override) if override else resource('payload/AirAlert.zip')


def registry_path():
    return os.environ.get('AIRALERT_SETUP_REGKEY') or REG_PATH


def start_menu_folder():
    override = os.environ.get('AIRALERT_SETUP_STARTMENU')
    if override:
        return Path(override)
    return Path(os.environ.get('APPDATA') or Path.home()) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'


def desktop_folder():
    override = os.environ.get('AIRALERT_SETUP_DESKTOP')
    if override:
        return Path(override)
    try:  # the real Desktop, including one that OneDrive has redirected
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
            return Path(winreg.QueryValueEx(key, 'Desktop')[0])
    except OSError:
        return Path.home() / 'Desktop'


def default_folder():
    """Where an existing installation lives, else the usual per-user place for programs."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path()) as key:
            existing = Path(winreg.QueryValueEx(key, 'InstallLocation')[0])
            if is_installation(existing):
                return existing
    except OSError:
        pass
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Programs' / APP


def is_installation(folder):
    return (Path(folder) / EXE).is_file() and (Path(folder) / '_internal').is_dir()


def installed_version(folder):
    try:
        return json.loads((Path(folder) / 'install.json').read_text('utf-8')).get('version', '')
    except (OSError, ValueError):
        return ''


def resolve_folder(chosen):
    """Turn what was typed into the folder to install into, or explain why it cannot be used.

    A folder that already holds someone else's files is never installed into directly: updates and
    uninstalls replace or delete the whole folder, so AirAlert gets its own subfolder instead."""
    text = os.path.expandvars(str(chosen).strip().strip('"'))
    if not text:
        raise SetupError('Choose the folder to install AirAlert into.')
    folder = Path(text)
    if not folder.is_absolute():
        raise SetupError('Type a full folder path, for example C:\\Users\\you\\AppData\\Local\\Programs\\AirAlert.')
    folder = Path(os.path.abspath(folder))
    if folder.parent == folder:
        raise SetupError('Choose a folder, not a whole drive.')
    if folder.exists():
        if not folder.is_dir():
            raise SetupError(f'{folder} is a file, not a folder.')
        if any(folder.iterdir()) and not is_installation(folder):
            folder = folder / APP
            if folder.exists() and any(folder.iterdir()) and not is_installation(folder):
                raise SetupError(f'{folder} already exists and is not an AirAlert installation. '
                                 'Choose an empty folder.')
    if len(str(folder)) > MAX_FOLDER_CHARS:
        raise SetupError(f'That folder path is too long ({len(str(folder))} characters, at most {MAX_FOLDER_CHARS}). '
                         'Choose a shorter one.')
    return folder


def free_space(folder):
    probe = Path(folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


# ----------------------------------------------------------------------------------------- Windows helpers
def powershell(script, env=None, timeout=60):
    """Run a short PowerShell script. Paths travel in environment variables, never inside the script text."""
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-Command', script], capture_output=True, text=True, timeout=timeout,
                            env=dict(os.environ, **(env or {})), creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        log(f'PowerShell failed ({result.returncode}): {result.stderr.strip()[:400]}')
    return result


PROCESS_QUERY = ("Get-Process -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and "
                 "$_.Path.StartsWith($env:AA_DIR + '\\', [StringComparison]::OrdinalIgnoreCase) } catch { $false } }")


def running_from(folder):
    """Names of the programs running from inside folder (AirAlert and its receiver programs)."""
    if not Path(folder).exists():
        return []
    out = powershell(PROCESS_QUERY + ' | ForEach-Object { $_.ProcessName }', env=dict(AA_DIR=str(folder)))
    return sorted({line.strip() for line in out.stdout.splitlines() if line.strip()})


def stop_running(folder):
    powershell(PROCESS_QUERY + ' | ForEach-Object { try { $_.Kill(); $_.WaitForExit(5000) | Out-Null } catch { } }',
               env=dict(AA_DIR=str(folder)))
    time.sleep(0.5)


SHORTCUT_SCRIPT = ("$shell = New-Object -ComObject WScript.Shell; "
                   "foreach ($l in ($env:AA_LINKS | ConvertFrom-Json)) { "
                   "$s = $shell.CreateShortcut($l.link); $s.TargetPath = $l.target; "
                   "$s.WorkingDirectory = $l.folder; $s.IconLocation = $l.target + ',0'; "
                   "$s.Description = 'AirAlert - ADS-B and AIS alerts'; $s.Save() }")


def create_shortcuts(links):
    """links: [(shortcut file, target exe, working folder)]."""
    for link, _, _ in links:
        Path(link).parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([dict(link=str(a), target=str(b), folder=str(c)) for a, b, c in links])
    result = powershell(SHORTCUT_SCRIPT, env=dict(AA_LINKS=payload))
    missing = [str(a) for a, _, _ in links if not Path(a).exists()]
    if result.returncode != 0 or missing:
        raise SetupError('The shortcuts could not be created: ' + (result.stderr.strip()[:200] or ', '.join(missing)))


def register_uninstall(folder, version, size_bytes):
    exe = Path(folder) / EXE
    script = Path(folder) / UNINSTALL_SCRIPT
    command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}"'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, registry_path(), 0, winreg.KEY_WRITE) as key:
        for name, value in (('DisplayName', APP), ('DisplayVersion', version), ('Publisher', APP),
                            ('InstallLocation', str(folder)), ('DisplayIcon', f'{exe},0'),
                            ('UninstallString', command), ('QuietUninstallString', command + ' -Silent'),
                            ('InstallDate', datetime.now().strftime('%Y%m%d'))):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (('NoModify', 1), ('NoRepair', 1), ('EstimatedSize', int(size_bytes / 1024))):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def launch(folder):
    """Start the installed app on its own: without setup's environment and without holding setup open."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('_PYI') and k != '_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    subprocess.Popen([str(Path(folder) / EXE)], cwd=str(folder), env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


# ------------------------------------------------------------------------------------------------ the install
def read_meta(archive):
    with zipfile.ZipFile(archive) as zf:
        return json.loads(zf.read('setup-meta.json').decode('utf-8'))


def extract(archive, destination, progress, cancelled):
    """Unpack archive into destination. progress(done_bytes, total_bytes) is called as it goes."""
    destination = Path(destination)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        entries = [i for i in zf.infolist() if i.filename != 'setup-meta.json']
        total = sum(i.file_size for i in entries) or 1
        done = 0
        for info in entries:
            if cancelled():
                raise Cancelled()
            target = (root / info.filename).resolve()
            if target != root and root not in target.parents:
                raise SetupError(f'The setup file is damaged (unsafe path {info.filename}).')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    progress(done, total)
                    if cancelled():
                        raise Cancelled()
            stamp = time.mktime(tuple(info.date_time) + (0, 0, -1))
            os.utime(target, (stamp, stamp))
    progress(total, total)


def install(folder, desktop_shortcut, progress, status, cancelled, archive=None):
    """Install (or update) into folder. Returns the installed folder. Raises SetupError or Cancelled."""
    archive = Path(archive or payload_path())
    if not archive.is_file():
        raise SetupError('The setup file is incomplete: the application archive is missing.')
    meta = read_meta(archive)
    folder = Path(folder)
    # Last line of defence (both callers go through resolve_folder): an update replaces and later deletes the
    # whole folder, so it must never be someone else's.
    if folder.exists() and any(folder.iterdir()) and not is_installation(folder):
        raise SetupError(f'{folder} is not an AirAlert installation. Choose an empty folder.')
    staging = folder.with_name(folder.name + '.new')
    previous = folder.with_name(folder.name + '.old')
    need = int(meta['size_bytes'] * 2.2) + (200 << 20)   # unpacked copy + the old one during the swap + headroom
    if free_space(folder) < need:
        raise SetupError(f'Not enough free disk space: about {need >> 20} MB is needed.')
    log(f'Installing {meta["version"]} into {folder}')
    status('Closing AirAlert…')
    if running_from(folder):
        stop_running(folder)
    for leftover in (staging, previous):
        shutil.rmtree(leftover, ignore_errors=True)
    swapped = False
    new_links = []
    try:
        status('Copying files…')
        extract(archive, staging, progress, cancelled)
        if not is_installation(staging):
            raise SetupError('The setup file is damaged: AirAlert.exe is missing from it.')
        status('Finishing…')
        if folder.exists():
            try:
                os.replace(folder, previous)
            except OSError as e:
                raise SetupError('The existing AirAlert files are in use. Close AirAlert (and any receiver '
                                 f'programs it started) and try again. ({e})')
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staging, folder)
        except OSError:
            if previous.exists():
                os.replace(previous, folder)
            raise
        swapped = True
        exe = folder / EXE
        shortcut = start_menu_folder() / f'{APP}.lnk'
        links = [(shortcut, exe, folder)]
        desktop = desktop_folder() / f'{APP}.lnk' if desktop_shortcut else None
        if desktop:
            links.append((desktop, exe, folder))
        new_links = [link for link, _, _ in links if not Path(link).exists()]   # only remove what this run made
        create_shortcuts(links)
        (folder / 'install.json').write_text(json.dumps(dict(
            version=meta['version'], folder=str(folder), startMenuLink=str(shortcut),
            desktopLink=str(desktop) if desktop else None, regKey=registry_path(),
            installedAt=datetime.now().isoformat(timespec='seconds'))), 'utf-8')
        register_uninstall(folder, meta['version'], meta['size_bytes'])
    except BaseException:
        # Undo everything: the failed attempt leaves the previous version (or nothing) exactly as it was.
        shutil.rmtree(staging, ignore_errors=True)
        if swapped:
            shutil.rmtree(folder, ignore_errors=True)
            for link in new_links:
                Path(link).unlink(missing_ok=True)
        if previous.exists() and not folder.exists():
            os.replace(previous, folder)
        raise
    shutil.rmtree(previous, ignore_errors=True)
    log('Installed')
    return folder


# ------------------------------------------------------------------------------------------ silent install
def parse_arguments(argv):
    options = dict(silent=False, folder=None, desktop=True, launch=False)
    args = list(argv)
    for i, arg in enumerate(args):
        low = arg.lower()
        if low in ('/s', '/silent', '--silent', '/verysilent'):
            options['silent'] = True
        elif low in ('/nodesktop', '--no-desktop'):
            options['desktop'] = False
        elif low in ('/launch', '--launch'):
            options['launch'] = True
        elif low.startswith('/d='):
            options['folder'] = arg[3:]
        elif low in ('--dir', '/dir') and i + 1 < len(args):
            options['folder'] = args[i + 1]
    return options


def run_silent(options):
    try:
        folder = resolve_folder(options['folder'] or default_folder())
        install(folder, options['desktop'], lambda done, total: None, lambda text: None, lambda: False)
        if options['launch']:
            launch(folder)
        return 0
    except SetupError as e:
        log(f'Setup error: {e}')
        if sys.stderr:   # a windowed program has no console to print to
            print(e, file=sys.stderr)
        return 1
    except Exception:
        log(traceback.format_exc())
        return 1


# ------------------------------------------------------------------------------------------------------ GUI
def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:  # crisp text on high-DPI screens
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    meta = read_meta(payload_path())
    default = default_folder()
    existing = installed_version(default) if is_installation(default) else ''

    root = tk.Tk()
    root.title(f'{APP} Setup')
    root.resizable(False, False)
    try:
        root.iconbitmap(default=str(resource('airalert.ico')))
    except tk.TclError:
        pass
    style = ttk.Style()
    if 'vista' in style.theme_names():
        style.theme_use('vista')
    font = ('Segoe UI', 9)
    style.configure('.', font=font)
    style.configure('Title.TLabel', font=('Segoe UI Semibold', 20))
    style.configure('Sub.TLabel', foreground='#555555')
    root.geometry('560x430')
    root.configure(background='#ffffff')
    style.configure('White.TFrame', background='#ffffff')
    style.configure('White.TLabel', background='#ffffff')
    style.configure('White.TCheckbutton', background='#ffffff')
    style.configure('Title.TLabel', background='#ffffff')
    style.configure('Sub.TLabel', background='#ffffff')

    events = queue.Queue()
    state = dict(cancel=False, working=False, result=None)
    folder_var = tk.StringVar(value=str(default))
    desktop_var = tk.BooleanVar(value=True)
    launch_var = tk.BooleanVar(value=True)

    style.configure('Foot.TFrame', background='#f0f0f0')
    pages = {name: ttk.Frame(root, style='White.TFrame', padding=(28, 22, 28, 12)) for name in ('options', 'progress', 'done')}
    footer = ttk.Frame(root, style='Foot.TFrame', padding=(28, 10, 28, 16))
    footer.pack(side='bottom', fill='x')
    ttk.Separator(root).pack(side='bottom', fill='x')

    def show(name):
        for page in pages.values():
            page.pack_forget()
        pages[name].pack(fill='both', expand=True)

    def header(page, title, subtitle):
        ttk.Label(page, text=title, style='Title.TLabel').pack(anchor='w')
        ttk.Label(page, text=subtitle, style='Sub.TLabel', wraplength=500).pack(anchor='w', pady=(0, 16))

    # -- options page
    page = pages['options']
    header(page, f'{APP} {meta["version"]}', 'ADS-B aircraft and AIS vessel tracking with alerts, for your RTL-SDR receiver.')
    ttk.Label(page, text='Install folder', style='White.TLabel').pack(anchor='w')
    row = ttk.Frame(page, style='White.TFrame')
    row.pack(fill='x', pady=(2, 12))
    entry = ttk.Entry(row, textvariable=folder_var)
    entry.pack(side='left', fill='x', expand=True)

    def browse():
        chosen = filedialog.askdirectory(initialdir=str(Path(folder_var.get()).parent), title='Choose the install folder')
        if chosen:
            folder_var.set(os.path.join(chosen, APP) if any(Path(chosen).iterdir()) and not is_installation(chosen)
                           else os.path.normpath(chosen))
    ttk.Button(row, text='Browse…', command=browse).pack(side='left', padx=(8, 0))
    ttk.Checkbutton(page, text='Add a shortcut to my desktop', variable=desktop_var, style='White.TCheckbutton').pack(anchor='w')
    ttk.Checkbutton(page, text=f'Open {APP} when setup finishes', variable=launch_var, style='White.TCheckbutton').pack(anchor='w', pady=(2, 14))
    notes = [f'Installs for your Windows account only, so no administrator rights are needed. Needs about '
             f'{round(meta["size_bytes"] / (1 << 20), -1):.0f} MB of disk space.']
    if existing:
        notes.insert(0, f'{APP} {existing} is already installed here. Setup will update it and keep your settings and history.')
    notes.append('AirAlert works with an RTL-SDR USB receiver, which needs the WinUSB driver (the README explains '
                 'how to install it with Zadig).')
    ttk.Label(page, text='\n\n'.join(notes), style='Sub.TLabel', wraplength=500, justify='left').pack(anchor='w')

    # -- progress page
    page = pages['progress']
    header(page, 'Installing…', 'Please wait while AirAlert is copied to your computer.')
    status_var = tk.StringVar(value='Starting…')
    ttk.Label(page, textvariable=status_var, style='White.TLabel').pack(anchor='w', pady=(20, 6))
    bar = ttk.Progressbar(page, maximum=1000, length=500)
    bar.pack(anchor='w')

    # -- done page
    page = pages['done']
    done_title = ttk.Label(page, text='', style='Title.TLabel')
    done_title.pack(anchor='w')
    done_text = ttk.Label(page, text='', style='Sub.TLabel', wraplength=500, justify='left')
    done_text.pack(anchor='w', pady=(6, 0))

    # -- footer buttons
    left = ttk.Button(footer, text='View licenses…')
    left.pack(side='left')
    primary = ttk.Button(footer, text='Update' if existing else 'Install')
    primary.pack(side='right')
    secondary = ttk.Button(footer, text='Cancel')
    secondary.pack(side='right', padx=(0, 8))

    def licenses():
        window = tk.Toplevel(root)
        window.title('Licenses and third-party notices')
        window.geometry('720x520')
        text = tk.Text(window, wrap='word', font=('Consolas', 9), padx=10, pady=10)
        scroll = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        with zipfile.ZipFile(payload_path()) as zf:
            for name in ('THIRD_PARTY.md', 'LICENSE'):
                if name in zf.namelist():
                    text.insert('end', f'==== {name} ====\n\n{zf.read(name).decode("utf-8", "replace")}\n\n')
        text.configure(state='disabled')
    left.configure(command=licenses)

    def finish(code=0):
        root.destroy()

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'progress':
                    bar['value'] = value * 1000
                elif kind == 'status':
                    status_var.set(value)
                elif kind == 'done':
                    show_done(value)
                elif kind == 'error':
                    show_error(value)
                elif kind == 'cancelled':
                    state['working'] = False
                    show('options')
                    buttons_options()
        except queue.Empty:
            pass
        root.after(60, poll)

    def show_done(folder):
        state['working'] = False
        state['result'] = folder
        done_title.configure(text=f'{APP} is installed')
        done_text.configure(text=f'It was installed to:\n{folder}\n\nFind it in the Start menu' +
                                 (' and on your desktop' if desktop_var.get() else '') + '. On first launch it walks you '
                                 'through setting your location and receiver.')
        show('done')
        left.pack_forget()
        secondary.pack_forget()
        primary.configure(text='Finish', command=lambda: (launch(folder) if launch_var.get() else None, finish()))
        primary.state(['!disabled'])

    def show_error(message):
        state['working'] = False
        done_title.configure(text='Setup could not finish')
        done_text.configure(text=f'{message}\n\nNothing was changed. Details were saved to:\n{LOG_FILE}')
        show('done')
        left.pack_forget()
        secondary.pack_forget()
        primary.configure(text='Close', command=finish)
        primary.state(['!disabled'])

    def work(folder, desktop):
        def progress(done, total):
            events.put(('progress', done / total))
        try:
            result = install(folder, desktop, progress, lambda text: events.put(('status', text)), lambda: state['cancel'])
            events.put(('done', str(result)))
        except Cancelled:
            log('Cancelled')
            events.put(('cancelled', None))
        except SetupError as e:
            log(f'Setup error: {e}')
            events.put(('error', str(e)))
        except Exception as e:
            log(traceback.format_exc())
            events.put(('error', f'An unexpected error stopped setup: {e}'))

    def start():
        try:
            folder = resolve_folder(folder_var.get())
        except SetupError as e:
            messagebox.showwarning(f'{APP} Setup', str(e))
            return
        folder_var.set(str(folder))
        running = running_from(folder) if folder.exists() else []
        if running and not messagebox.askokcancel(f'{APP} Setup', f'{APP} is running. Setup will close it now so it can be '
                                                  f'updated.\n\nContinue?'):
            return
        state.update(cancel=False, working=True)
        show('progress')
        left.pack_forget()
        primary.state(['disabled'])
        secondary.configure(text='Cancel', command=lambda: state.update(cancel=True))
        threading.Thread(target=work, args=(folder, desktop_var.get()), daemon=True).start()

    def buttons_options():
        left.pack(side='left')
        primary.configure(text='Update' if existing else 'Install', command=start)
        primary.state(['!disabled'])
        secondary.configure(text='Cancel', command=finish)

    def close_window():
        if state['working']:
            state['cancel'] = True
        else:
            finish()
    root.protocol('WM_DELETE_WINDOW', close_window)
    buttons_options()
    show('options')
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f'+{max(0, x)}+{max(0, y)}')
    entry.focus_set()
    if os.environ.get('AIRALERT_SETUP_AUTOCLICK'):   # test hook: press the button after the window has appeared
        root.after(1500, start)
    root.after(60, poll)
    root.mainloop()
    return 0 if state['result'] or not state['working'] else 2


def main(argv=None):
    options = parse_arguments(argv if argv is not None else sys.argv[1:])
    log(f'--- AirAlert Setup started ({"silent" if options["silent"] else "interactive"})')
    if options['silent']:
        return run_silent(options)
    try:
        return run_gui()
    except Exception:
        log(traceback.format_exc())
        ctypes.windll.user32.MessageBoxW(0, f'Setup could not start.\n\nDetails were saved to:\n{LOG_FILE}', f'{APP} Setup', 0x10)
        return 1


if __name__ == '__main__':
    sys.exit(main())
