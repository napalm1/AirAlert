"""Build the portable Windows app: dist/AirAlert/AirAlert.exe.

Runs the test suite and real-decoder checks, packages with PyInstaller, then
runs the packaged executable's --self-test. Use the project's .venv Python:

    .venv\\Scripts\\python.exe tools\\build.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args, env=None):
    print('>', ' '.join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, env=env)


def main():
    os.chdir(ROOT)
    python = sys.executable
    (ROOT / 'test-output').mkdir(exist_ok=True)
    if not (ROOT / 'assets/airalert.ico').exists():
        run([python, 'tools/make_icon.py'], env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    if '--skip-tests' not in sys.argv:
        # One fixed scratch folder (pytest empties it each run): a fresh one per build piled up gigabytes.
        run([python, '-m', 'pytest', '-q', '--basetemp=test-output/pytest'])
        run([python, 'tools/decoder_test.py'])
    args = [python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir', '--name', 'AirAlert',
            '--icon', 'assets/airalert.ico', '--collect-all', 'pyModeS',
            '--hidden-import', 'PySide6.QtQuick', '--hidden-import', 'PySide6.QtQml',
            '--hidden-import', 'PySide6.QtQuickControls2', '--hidden-import', 'PySide6.QtSvg',
            '--hidden-import', 'PySide6.QtTest', '--hidden-import', 'PySide6.QtNetwork',
            '--add-data', 'airalert/qml;airalert/qml', '--add-data', 'airalert/web;airalert/web',
            '--add-data', 'assets;assets',
            '--add-data', 'licenses;licenses', '--add-data', 'LICENSE;.',
            '--add-binary', 'vendor/adsb/dump1090.exe;vendor/adsb',
            '--add-binary', 'vendor/ais/AIS-catcher.exe;vendor/ais',
            '--add-data', 'vendor/ais/Licenses;vendor/ais/Licenses']
    for dll in sorted((ROOT / 'vendor/ais').glob('*.dll')):
        args += ['--add-binary', f'{dll};vendor/ais']
    # Feature bridges are imported by name (airalert/ui/bridges.py), so name them for PyInstaller.
    from importlib.util import find_spec
    sys.path.insert(0, str(ROOT))
    from airalert.ui.bridges import BRIDGES
    for module in list(BRIDGES.values()) + ['airalert.insights', 'airalert.aircraftdb', 'airalert.playback',
                                           'airalert.airports', 'airalert.reception']:
        if find_spec(module) is not None:
            args += ['--hidden-import', module]
    # Runtime data: the aircraft database snapshot and the compact airport file (the source CSVs stay out).
    for path, target in (('vendor/aircraft/aircraft.sqlite', 'vendor/aircraft'),
                         ('vendor/airports/airports.tsv.gz', 'vendor/airports')):
        if (ROOT / path).exists():
            args += ['--add-data', f'{path};{target}']
    args += ['main.py']
    build_env = os.environ.copy()
    # Keep unrelated programs on PATH (e.g. Node's ICU) from supplying DLLs to the package.
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    build_env['PATH'] = os.pathsep.join(map(str, [Path(python).parent, Path(sys.base_prefix),
                                                  windows / 'System32', windows]))
    run(args, build_env)
    package = ROOT / 'dist/AirAlert'
    for dll in (package / '_internal').glob('icu*.dll'):
        dll.unlink()
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY.md'):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, package / name)
    # dump1090 and AIS-catcher are separate programs that need the Visual C++ runtime. Give each its own copy
    # (PySide6 ships one) so they start on a PC that never installed it, rather than relying on the search path.
    runtime = [dll for pattern in ('MSVCP140*.dll', 'VCRUNTIME140*.dll')
               for dll in sorted((package / '_internal/PySide6').glob(pattern))]
    assert runtime, 'the Visual C++ runtime DLLs were not found in the PySide6 folder'
    for folder in ('vendor/adsb', 'vendor/ais'):
        for dll in runtime:
            shutil.copy2(dll, package / '_internal' / folder / dll.name)
    output = ROOT / 'test-output/packaged'
    shutil.rmtree(output, ignore_errors=True)  # a clean data folder for the self-test, and nothing piles up
    run([package / 'AirAlert.exe', '--self-test', output])
    results = json.loads((output / 'results.json').read_text('utf-8'))
    print(json.dumps(results, indent=2))
    run([python, 'tools/decoder_test.py', package / '_internal'])
    size = sum(f.stat().st_size for f in package.rglob('*') if f.is_file()) / 1024 ** 2
    print(f'Built and verified {package / "AirAlert.exe"} ({size:.0f} MB)')


if __name__ == '__main__':
    main()
