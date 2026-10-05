"""Backups of the settings and the history database as one zip file in a folder you choose.

``AirAlert-backup-YYYYMMDD-HHMMSS.zip`` holds ``settings.json`` and a consistent copy of ``history.sqlite``
(taken with SQLite's online backup, so it is safe while monitoring runs). The aircraft database, logs and
map tiles are left out: they can be downloaded or rebuilt. To restore, close AirAlert and unzip the two
files into the data folder.
"""
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

PREFIX = 'AirAlert-backup-'
FILES = ('settings.json',)
DATABASE = 'history.sqlite'


def backups(folder):
    """Existing backup files in folder, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob(PREFIX + '*.zip') if p.is_file()), key=lambda p: p.stat().st_mtime,
                  reverse=True)


def latest(folder):
    """(path, unix time) of the newest backup, or None."""
    found = backups(folder)
    return (found[0], found[0].stat().st_mtime) if found else None


def due(folder, every_days, now=None):
    now = time.time() if now is None else now
    newest = latest(folder)
    return newest is None or now - newest[1] >= every_days * 86400 - 600


def make_backup(data_folder, dest_folder, keep=5, now=None):
    """Write a backup zip into dest_folder and delete all but the newest `keep`. Returns the new file's path."""
    data_folder, dest_folder = Path(data_folder), Path(dest_folder)
    if not str(dest_folder).strip() or not dest_folder.is_absolute():
        raise ValueError('Choose a backup folder first.')
    dest_folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime('%Y%m%d-%H%M%S')
    target = dest_folder / f'{PREFIX}{stamp}.zip'
    partial = target.with_suffix('.partial')
    copy = dest_folder / f'{PREFIX}{stamp}.sqlite.tmp'
    try:
        source = data_folder / DATABASE
        if source.is_file():
            src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
            try:
                dst = sqlite3.connect(copy)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name in FILES:
                if (data_folder / name).is_file():
                    zf.write(data_folder / name, name)
            if copy.is_file():
                zf.write(copy, DATABASE)
        partial.replace(target)
    finally:
        copy.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    for old in backups(dest_folder)[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass
    return target
