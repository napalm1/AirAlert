"""Build the one-file installer: release/AirAlert-Setup-<version>.exe

Run tools/build.py first (it produces dist/AirAlert), then:

    .venv\\Scripts\\python.exe tools\\make_installer.py

The application folder is packed into one compressed archive, embedded in the graphical setup program
(installer/airalert_setup.py) with PyInstaller, and the result is a single file that installs AirAlert
for the current Windows user (Start menu entry, optional desktop shortcut, Installed-apps uninstall entry).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist' / 'AirAlert'
WORK = ROOT / 'build' / 'installer'
RELEASE = ROOT / 'release'


def app_version():
    text = (ROOT / 'airalert' / '__init__.py').read_text('utf-8')
    return re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text).group(1)


def build_payload(dist, out_zip, version, compression=zipfile.ZIP_LZMA, uninstaller=None):
    """Pack the application folder (files at the archive root) plus the uninstaller and a small manifest."""
    dist = Path(dist)
    files = sorted(p for p in dist.rglob('*') if p.is_file())
    if not (dist / 'AirAlert.exe').is_file():
        raise SystemExit(f'{dist} is not a built application folder. Run tools\\build.py first.')
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    total = sum(p.stat().st_size for p in files)
    uninstaller = Path(uninstaller or ROOT / 'installer' / 'uninstall.ps1')
    with zipfile.ZipFile(out_zip, 'w', compression) as zf:
        for path in files:
            zf.write(path, path.relative_to(dist).as_posix())
        zf.write(uninstaller, 'uninstall.ps1')
        total += uninstaller.stat().st_size
        meta = dict(name='AirAlert', version=version, size_bytes=total, files=len(files) + 1)
        zf.writestr('setup-meta.json', json.dumps(meta))
    return meta


def version_file(path, version):
    parts = (version.split('.') + ['0', '0', '0', '0'])[:4]
    tuple_text = ', '.join(parts)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tuple_text}), prodvers=({tuple_text}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'AirAlert'),
      StringStruct('FileDescription', 'AirAlert Setup'),
      StringStruct('FileVersion', '{'.'.join(parts)}'),
      StringStruct('InternalName', 'AirAlert-Setup'),
      StringStruct('OriginalFilename', 'AirAlert-Setup-{version}.exe'),
      StringStruct('ProductName', 'AirAlert'),
      StringStruct('ProductVersion', '{'.'.join(parts)}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", 'utf-8')


def main():
    version = app_version()
    started = time.time()
    shutil.rmtree(WORK, ignore_errors=True)
    payload_dir = WORK / 'payload'
    print(f'Packing {DIST} (AirAlert {version})...', flush=True)
    meta = build_payload(DIST, payload_dir / 'AirAlert.zip', version)
    packed = (payload_dir / 'AirAlert.zip').stat().st_size
    print(f'  {meta["files"]:,} files, {meta["size_bytes"] / 1024 ** 2:.0f} MB -> {packed / 1024 ** 2:.0f} MB archive '
          f'({time.time() - started:.0f} s)', flush=True)
    info = WORK / 'version_info.txt'
    version_file(info, version)
    name = f'AirAlert-Setup-{version}'
    build_env = os.environ.copy()
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    build_env['PATH'] = os.pathsep.join(map(str, [Path(sys.executable).parent, Path(sys.base_prefix),
                                                  windows / 'System32', windows]))
    RELEASE.mkdir(exist_ok=True)
    (RELEASE / f'{name}.exe').unlink(missing_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
               '--name', name, '--icon', str(ROOT / 'assets' / 'airalert.ico'), '--version-file', str(info),
               '--add-data', f'{payload_dir};payload', '--add-data', f'{ROOT / "assets" / "airalert.ico"};.',
               '--distpath', str(RELEASE), '--workpath', str(WORK / 'work'), '--specpath', str(WORK / 'spec'),
               '--exclude-module', 'PySide6', '--exclude-module', 'numpy', '--exclude-module', 'pyModeS',
               '--exclude-module', 'pyais', str(ROOT / 'installer' / 'airalert_setup.py')]
    print('Building the setup program...', flush=True)
    subprocess.run(command, cwd=ROOT, env=build_env, check=True, stdout=subprocess.DEVNULL)
    exe = RELEASE / f'{name}.exe'
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    # The update feed: upload it next to the installer and point airalert.UPDATE_URL at it (see README).
    sys.path.insert(0, str(ROOT))
    from airalert import UPDATE_URL
    base = UPDATE_URL.rsplit('/', 1)[0] if UPDATE_URL else 'https://YOUR-SITE/airalert'
    (RELEASE / 'latest.json').write_text(json.dumps(dict(version=version, url=f'{base}/{name}.exe', sha256=digest,
                                                         notes=''), indent=2), 'utf-8')
    print(f'\nBuilt {exe}\n  size   {exe.stat().st_size / 1024 ** 2:.0f} MB\n  sha256 {digest}\n'
          f'  took   {time.time() - started:.0f} s')


if __name__ == '__main__':
    main()
