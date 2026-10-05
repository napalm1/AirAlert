"""The one-file installer: folder rules, safe unpacking, update, rollback, shortcuts, registry and uninstall.

Uses a tiny fake application so it runs in seconds. Shortcuts, the Start menu, the desktop and the
"Installed apps" registry key are redirected to a scratch area (see the AIRALERT_SETUP_* variables), so the
real profile is never touched. The full-size installer is exercised by tools/test_installer.ps1.
"""
import importlib.util
import json
import os
import subprocess
import sys
import uuid
import winreg
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows installer')
ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup = load('airalert_setup', ROOT / 'installer' / 'airalert_setup.py')
maker = load('make_installer', ROOT / 'tools' / 'make_installer.py')
REG_ROOT = r'Software\AirAlertTests'


def delete_key(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    delete_key(path + '\\' + winreg.EnumKey(key, 0))
                except OSError:
                    break
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Scratch Start menu, desktop, data folder and registry key, plus a small fake application archive."""
    for name in ('startmenu', 'desktop', 'data', 'appdata'):
        (tmp_path / name).mkdir()
    key = f'{REG_ROOT}\\{uuid.uuid4().hex}\\AirAlert'
    monkeypatch.setenv('AIRALERT_SETUP_STARTMENU', str(tmp_path / 'startmenu'))
    monkeypatch.setenv('AIRALERT_SETUP_DESKTOP', str(tmp_path / 'desktop'))
    monkeypatch.setenv('AIRALERT_SETUP_REGKEY', key)
    monkeypatch.setenv('AIRALERT_DATA', str(tmp_path / 'data'))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'appdata'))     # so an uninstall test never sees the real Startup folder
    monkeypatch.setattr(setup, 'LOG_FILE', tmp_path / 'setup.log')
    app = tmp_path / 'fakeapp'
    (app / '_internal' / 'sub').mkdir(parents=True)
    (app / 'AirAlert.exe').write_bytes(b'MZ fake')
    (app / '_internal' / 'sub' / 'data.bin').write_bytes(os.urandom(50_000))
    (app / 'LICENSE').write_text('license text')
    payload = tmp_path / 'AirAlert.zip'
    maker.build_payload(app, payload, '9.9.9', compression=zipfile.ZIP_DEFLATED)
    monkeypatch.setenv('AIRALERT_SETUP_PAYLOAD', str(payload))
    yield dict(root=tmp_path, key=key, payload=payload, app=app)
    delete_key(REG_ROOT)


def do_install(env, folder, desktop=True):
    return setup.install(folder, desktop, lambda done, total: None, lambda text: None, lambda: False)


def shortcut_target(path):
    out = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                          '(New-Object -ComObject WScript.Shell).CreateShortcut($env:P).TargetPath'],
                         capture_output=True, text=True, env=dict(os.environ, P=str(path)), timeout=60)
    return out.stdout.strip()


def uninstall(folder, *flags):
    return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                           str(Path(folder) / 'uninstall.ps1'), '-Silent', *flags], capture_output=True, text=True,
                          timeout=120)


# ------------------------------------------------------------------------------------------------ arguments
def test_command_line_options():
    p = setup.parse_arguments
    assert p([]) == dict(silent=False, folder=None, desktop=True, launch=False)
    assert p(['/S']) ['silent'] and p(['/silent'])['silent'] and p(['--silent'])['silent']
    assert p(['/S', '/D=C:\\Apps\\Air Alert'])['folder'] == 'C:\\Apps\\Air Alert'   # spaces are kept
    assert p(['--dir', 'C:\\x'])['folder'] == 'C:\\x'
    assert not p(['/S', '/NODESKTOP'])['desktop'] and p(['/S', '/LAUNCH'])['launch']


# ------------------------------------------------------------------------------------------ folder rules
def test_folder_rules(tmp_path):
    with pytest.raises(setup.SetupError):
        setup.resolve_folder('')
    with pytest.raises(setup.SetupError):
        setup.resolve_folder('relative\\path')
    with pytest.raises(setup.SetupError, match='not a whole drive'):
        setup.resolve_folder('C:\\')
    with pytest.raises(setup.SetupError, match='too long'):
        setup.resolve_folder(str(tmp_path / ('x' * 120)))
    (tmp_path / 'afile').write_text('x')
    with pytest.raises(setup.SetupError, match='is a file'):
        setup.resolve_folder(str(tmp_path / 'afile'))
    assert setup.resolve_folder(str(tmp_path / 'new' / 'AirAlert')) == tmp_path / 'new' / 'AirAlert'
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert setup.resolve_folder(str(empty)) == empty
    # somebody else's files: never install straight into it (updates and uninstalls delete the whole folder)
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'notes.txt').write_text('mine')
    assert setup.resolve_folder(str(docs)) == docs / 'AirAlert'
    (docs / 'AirAlert').mkdir()
    (docs / 'AirAlert' / 'other.txt').write_text('also mine')
    with pytest.raises(setup.SetupError, match='not an AirAlert installation'):
        setup.resolve_folder(str(docs))
    # an existing installation is fine to update in place
    (tmp_path / 'inst' / '_internal').mkdir(parents=True)
    (tmp_path / 'inst' / 'AirAlert.exe').write_bytes(b'x')
    assert setup.resolve_folder(str(tmp_path / 'inst')) == tmp_path / 'inst'


def test_the_longest_path_in_the_real_app_fits_the_folder_limit():
    dist = ROOT / 'dist' / 'AirAlert'
    if not dist.exists():
        pytest.skip('the app has not been built')
    longest = max(len(str(p.relative_to(dist))) for p in dist.rglob('*') if p.is_file())
    assert longest + setup.MAX_FOLDER_CHARS + 1 < 260, 'raise the safety margin or lower MAX_FOLDER_CHARS'


# ----------------------------------------------------------------------------------------- unpacking
def test_unsafe_archive_paths_are_refused(tmp_path):
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(bad, 'w') as zf:
        zf.writestr('setup-meta.json', json.dumps(dict(version='1', size_bytes=1)))
        zf.writestr('..\\..\\evil.txt', 'x')
    with pytest.raises(setup.SetupError, match='unsafe path'):
        setup.extract(bad, tmp_path / 'out', lambda d, t: None, lambda: False)
    assert not (tmp_path / 'evil.txt').exists()


def test_extract_reports_progress_and_can_be_cancelled(env):
    seen = []
    setup.extract(env['payload'], env['root'] / 'out', lambda d, t: seen.append((d, t)), lambda: False)
    assert seen[-1][0] == seen[-1][1] and seen == sorted(seen)
    assert (env['root'] / 'out' / 'AirAlert.exe').read_bytes() == b'MZ fake'
    assert (env['root'] / 'out' / 'uninstall.ps1').is_file() and not (env['root'] / 'out' / 'setup-meta.json').exists()
    with pytest.raises(setup.Cancelled):
        setup.extract(env['payload'], env['root'] / 'out2', lambda d, t: None, lambda: True)


# --------------------------------------------------------------------------------------------- install
def test_install_creates_files_shortcuts_and_registry_entry(env):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder)
    assert setup.is_installation(folder) and (folder / 'uninstall.ps1').is_file()
    info = json.loads((folder / 'install.json').read_text('utf-8'))
    assert info['version'] == '9.9.9' and info['regKey'] == env['key']
    assert shortcut_target(env['root'] / 'startmenu' / 'AirAlert.lnk') == str(folder / 'AirAlert.exe')
    assert shortcut_target(env['root'] / 'desktop' / 'AirAlert.lnk') == str(folder / 'AirAlert.exe')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, env['key']) as key:
        values = {n: winreg.QueryValueEx(key, n)[0] for n in ('DisplayName', 'DisplayVersion', 'InstallLocation',
                                                             'UninstallString', 'QuietUninstallString', 'NoModify')}
    assert values['DisplayName'] == 'AirAlert' and values['DisplayVersion'] == '9.9.9'
    assert values['InstallLocation'] == str(folder) and values['NoModify'] == 1
    assert str(folder / 'uninstall.ps1') in values['UninstallString']
    assert values['QuietUninstallString'].endswith('-Silent')
    assert not folder.with_name('AirAlert.new').exists() and not folder.with_name('AirAlert.old').exists()
    assert setup.default_folder() == folder                 # a later run offers to update this installation
    assert setup.installed_version(folder) == '9.9.9'


def test_no_desktop_shortcut_when_declined(env):
    do_install(env, env['root'] / 'A', desktop=False)
    assert (env['root'] / 'startmenu' / 'AirAlert.lnk').exists() and not (env['root'] / 'desktop' / 'AirAlert.lnk').exists()


def test_update_replaces_old_files_and_keeps_the_rest_of_the_disk_alone(env):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder)
    (folder / '_internal' / 'stale-from-old-version.txt').write_text('old')
    (env['root'] / 'data' / 'settings.json').write_text('{"setup_done": true}')
    do_install(env, folder)
    assert not (folder / '_internal' / 'stale-from-old-version.txt').exists()      # a clean replacement
    assert (env['root'] / 'data' / 'settings.json').read_text() == '{"setup_done": true}'
    assert not folder.with_name('AirAlert.old').exists()


def test_a_bad_archive_leaves_the_existing_install_untouched(env):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder)
    (folder / 'marker.txt').write_text('old version')
    bad = env['root'] / 'bad.zip'
    with zipfile.ZipFile(bad, 'w') as zf:            # unpacks fine but holds no AirAlert.exe
        zf.writestr('setup-meta.json', json.dumps(dict(version='9.9.9', size_bytes=10)))
        zf.writestr('junk.txt', 'junk')
    with pytest.raises(setup.SetupError, match='damaged'):
        setup.install(folder, True, lambda d, t: None, lambda s: None, lambda: False, archive=bad)
    assert (folder / 'marker.txt').read_text() == 'old version' and setup.is_installation(folder)
    assert not folder.with_name('AirAlert.new').exists() and not folder.with_name('AirAlert.old').exists()


def test_failure_after_the_swap_rolls_back_to_the_previous_version(env, monkeypatch):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder)
    (folder / 'marker.txt').write_text('old version')
    (env['root'] / 'blocker').write_text('a file, so the shortcut folder cannot be created')
    monkeypatch.setenv('AIRALERT_SETUP_STARTMENU', str(env['root'] / 'blocker' / 'sub'))
    with pytest.raises(Exception):
        do_install(env, folder)
    assert (folder / 'marker.txt').read_text() == 'old version'
    assert not folder.with_name('AirAlert.new').exists() and not folder.with_name('AirAlert.old').exists()


def test_a_failed_first_install_leaves_nothing_behind(env, monkeypatch):
    folder = env['root'] / 'Programs' / 'AirAlert'
    (env['root'] / 'blocker').write_text('x')
    monkeypatch.setenv('AIRALERT_SETUP_STARTMENU', str(env['root'] / 'blocker' / 'sub'))
    with pytest.raises(Exception):
        do_install(env, folder)
    assert not folder.exists() and not folder.with_name('AirAlert.new').exists()
    assert not (env['root'] / 'desktop' / 'AirAlert.lnk').exists()


def test_not_enough_disk_space_is_reported_before_anything_is_written(env, monkeypatch):
    monkeypatch.setattr(setup, 'free_space', lambda folder: 1 << 20)
    with pytest.raises(setup.SetupError, match='free disk space'):
        do_install(env, env['root'] / 'Programs' / 'AirAlert')
    assert not (env['root'] / 'Programs').exists()


def test_silent_install_exit_codes(env):
    folder = env['root'] / 'silent' / 'AirAlert'
    assert setup.run_silent(dict(silent=True, folder=str(folder), desktop=False, launch=False)) == 0
    assert setup.is_installation(folder)
    assert setup.run_silent(dict(silent=True, folder='relative', desktop=False, launch=False)) == 1


# ------------------------------------------------------------------------------------------- uninstall
def test_uninstall_removes_the_program_but_keeps_settings_by_default(env):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder)
    (env['root'] / 'data' / 'settings.json').write_text('{}')
    cache = env['root'] / 'data' / 'AirAlert' / 'cache'
    cache.mkdir(parents=True)
    (cache / 'x.qmlc').write_text('x')
    result = uninstall(folder)
    assert result.returncode == 0, result.stderr
    assert not folder.exists()
    assert not (env['root'] / 'startmenu' / 'AirAlert.lnk').exists() and not (env['root'] / 'desktop' / 'AirAlert.lnk').exists()
    with pytest.raises(OSError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, env['key'])
    assert (env['root'] / 'data' / 'settings.json').exists() and not cache.exists()


def test_uninstall_can_also_delete_settings_and_history(env):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder)
    data = env['root'] / 'data'
    (data / 'settings.json').write_text('{}')
    assert uninstall(folder, '-PurgeData').returncode == 0
    assert not folder.exists() and not data.exists()


def test_uninstall_leaves_a_shortcut_that_is_not_ours_alone(env):
    folder = env['root'] / 'Programs' / 'AirAlert'
    do_install(env, folder, desktop=False)
    link = env['root'] / 'desktop' / 'AirAlert.lnk'
    subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:P); "
                    "$s.TargetPath = \"$env:SystemRoot\\System32\\notepad.exe\"; $s.Save()"],
                   env=dict(os.environ, P=str(link)), check=True, timeout=60)
    (folder / 'install.json').write_text(json.dumps(dict(
        version='9.9.9', startMenuLink=str(env['root'] / 'startmenu' / 'AirAlert.lnk'), desktopLink=str(link),
        regKey=env['key'])), 'utf-8')       # pretend setup had recorded that desktop link
    assert uninstall(folder).returncode == 0
    assert link.exists() and shortcut_target(link).lower().endswith('notepad.exe')


def test_uninstall_refuses_to_delete_a_folder_that_is_not_an_installation(env):
    stray = env['root'] / 'Downloads'
    stray.mkdir()
    (stray / 'uninstall.ps1').write_text((ROOT / 'installer' / 'uninstall.ps1').read_text('utf-8'))
    (stray / 'precious.txt').write_text('keep me')
    result = uninstall(stray)
    assert result.returncode == 2
    assert (stray / 'precious.txt').read_text() == 'keep me'


def test_the_uninstaller_in_the_archive_is_the_repo_script(env):
    with zipfile.ZipFile(env['payload']) as zf:
        assert zf.read('uninstall.ps1') == (ROOT / 'installer' / 'uninstall.ps1').read_bytes()
        meta = json.loads(zf.read('setup-meta.json'))
    assert meta['version'] == '9.9.9' and meta['files'] == 4      # the 3 files of the fake app + the uninstaller
