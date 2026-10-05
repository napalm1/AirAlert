"""Receive-only live test with the real RTL-SDR: python tools/hardware_test.py [Aircraft|Marine] [seconds]

Uses the receiver serial from AirAlert's settings, a throwaway data folder, and the
same Station engine as the app. Never changes drivers.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'Aircraft'
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    real = Path(os.environ['LOCALAPPDATA']) / 'AirAlert' / 'settings.json'
    settings = json.loads(real.read_text('utf-8')) if real.exists() else {}
    folder = Path(tempfile.mkdtemp(prefix='airalert-hw-'))
    settings.update(mode=mode, setup_done=True, rules=settings.get('rules', []))
    (folder / 'settings.json').write_text(json.dumps(settings), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    from airalert.core.config import Config
    from airalert.engine import Station
    station = Station(Config(folder))
    error = station.start(mode)
    if error:
        raise SystemExit(error)
    print(f'{mode} on receiver {settings.get("aircraft_device")} for {seconds}s; data in {folder}', flush=True)
    deadline = time.time() + seconds
    alerts = 0
    last_report = 0
    while time.time() < deadline:
        notices, _ = station.tick()
        alerts += len(notices)
        if time.time() - last_report >= 10:
            last_report = time.time()
            with_position = sum(1 for t in station.store.targets.values() if t.position)
            print(f'  t+{int(seconds - (deadline - time.time())):3d}s  targets={len(station.store.targets)} '
                  f'with_position={with_position}  health={station.receiver_health}', flush=True)
        time.sleep(0.25)
    station.stop()
    targets = sorted(station.store.targets.values(), key=lambda t: t.label)
    for t in targets[:25]:
        pos = f'{t.position[0]:.3f},{t.position[1]:.3f}' if t.position else 'no position'
        print(f'  {t.kind:8s} {t.identifier:9s} {t.label:10s} alt={t.data.get("altitude")} {pos}')
    print(json.dumps(dict(targets=len(targets), with_position=sum(1 for t in targets if t.position),
                          alerts=alerts, health=station.receiver_health)))
    time.sleep(3)  # let the decoder release the receiver
    station.shutdown()


if __name__ == '__main__':
    main()
