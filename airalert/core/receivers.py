"""Owned decoder subprocesses, bounded queues and exclusive receiver scheduling."""
import logging
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from .parsers import ADSBParser, AISParser

log=logging.getLogger(__name__)
FLAGS=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0


def resource_root():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))


def detect_devices():
    exe=resource_root()/'vendor/ais/AIS-catcher.exe'
    r=subprocess.run([str(exe),'-l'],capture_output=True,text=True,timeout=10,creationflags=FLAGS)
    output=r.stdout+r.stderr
    return [dict(index=m[0],name=m[1].strip(),serial=m[2].strip()) for m in
            re.findall(r'(?m)^(\d+):\s*(.+?),\s*SN:\s*(\S+)',output)]


def assignments(settings, elapsed):
    mode=settings['mode']
    a=settings['aircraft_device']; m=settings['marine_device']
    if mode=='Aircraft': return {'aircraft':a}
    if mode=='Marine': return {'vessel':a}
    if mode=='Dual receivers':
        if a==m: raise ValueError('Choose two different receivers for continuous dual reception.')
        return {'aircraft':a,'vessel':m}
    if mode=='Automatic switching':
        total=settings['aircraft_seconds']+settings['marine_seconds']
        return {'aircraft' if elapsed%total<settings['aircraft_seconds'] else 'vessel':a}
    return {}


def validate_device_identity(settings,devices):
    if settings['mode']!='Dual receivers': return
    resolved=[]
    for selection in (settings['aircraft_device'],settings['marine_device']):
        device=next((d for d in devices if d['serial']==selection),None)
        if device is None:
            device=next((d for d in devices if d['index']==selection),None)
        resolved.append(device['index'] if device else 'unresolved:'+selection)
    if resolved[0]==resolved[1]:
        raise ValueError('Both assignments resolve to the same physical receiver. Select two distinct dongles.')


def adsb_config(path, settings):
    # Explicitly disable decoder geolocation, databases, feeder services and all listeners.
    home=settings.get('home') or [0,0]
    gain='0' if settings['gain']=='auto' else str(float(settings['gain']))
    path.write_text('\n'.join([
        'aircrafts = NUL','airports = NUL','location = false',f'homepos = {home[0]},{home[1]}',
        'prefer-adsb-lol = false','error-correct1 = false','error-correct2 = false',
        'cpr-trace = false','loops = 0','samplerate = 2.4M','freq = 1090M',
        'DC-filter = true','measure-noise = true',
        f'gain = {gain}',f'rtlsdr-ppm = {settings["ppm"]}','agc = false',
        f'logfile = {path.parent / "dump1090.log"}','logfile-daily = 1','speech-enable = false',
    ]),encoding='utf-8')


class DecoderWorker(threading.Thread):
    def __init__(self,kind,device,settings,folder,messages,status):
        super().__init__(daemon=True,name=f'decoder-{kind}')
        self.kind=kind; self.device=device; self.settings=settings; self.folder=folder
        self.messages=messages; self.status=status; self.stop_event=threading.Event(); self.process=None
        self.dropped=0

    def stop(self):
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)

    def report(self,text):
        log.info('%s: %s',self.kind,text)
        try: self.status.put_nowait((self.kind,text))
        except queue.Full: pass

    def run(self):
        backoff=2
        while not self.stop_event.is_set():
            try:
                devices=detect_devices()
                device=next((d for d in devices if d['serial']==self.device),None)
                if device is None and self.device.isdigit() and len(self.device)<3:
                    device=next((d for d in devices if d['index']==self.device),None)
                if device is None: raise RuntimeError('Receiver absent. Check USB connection and WinUSB driver; retrying.')
                root=resource_root()
                if self.kind=='aircraft':
                    exe=root/'vendor/adsb/dump1090.exe'
                    cfg=self.folder/'receiver-aircraft.cfg'; adsb_config(cfg,self.settings)
                    args=[str(exe),'--config',str(cfg),'--device',device['index'],'--raw']
                    parser=ADSBParser(self.settings.get('home'))
                else:
                    exe=root/'vendor/ais/AIS-catcher.exe'
                    args=[str(exe),'-d',device['serial'],'-o','1','-s','1536000','-p',str(self.settings['ppm']),'-gr','TUNER',str(self.settings['gain'])]
                    parser=AISParser()
                self.report('Starting · '+device['name']+' · '+device['serial'])
                self.process=subprocess.Popen(args,cwd=self.folder,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    text=True,encoding='utf-8',errors='replace',bufsize=1,creationflags=FLAGS)
                self.report('Running · '+('1090 MHz · 2.4 MS/s' if self.kind=='aircraft' else '161.975 / 162.025 MHz · 1.536 MS/s'))
                for line in self.process.stdout:
                    if self.stop_event.is_set(): break
                    try: msg=parser.parse(line)
                    except Exception:
                        log.exception('Malformed decoder output'); continue
                    if msg:
                        msg['_received']=time.time()
                        try: self.messages.put_nowait(msg)
                        except queue.Full: self.dropped+=1
                        backoff=2
                    elif line.strip():
                        log.info('%s: %s',self.kind,line.strip()[:500])
                if not self.stop_event.is_set():
                    raise RuntimeError('Decoder stopped. Receiver may be disconnected, busy, or missing WinUSB. Retrying.')
            except Exception as e:
                self.report(str(e))
            finally:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    try: self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
                if self.process and self.process.stdout: self.process.stdout.close()
                self.process=None
            if self.stop_event.wait(backoff): break
            backoff=min(30,backoff*2)
        self.report('Stopped')


class ReceiverManager(threading.Thread):
    def __init__(self,settings,folder):
        super().__init__(daemon=True,name='receiver-manager')
        self.settings=settings.copy(); self.folder=folder
        assignments(settings,0)
        self.messages=queue.Queue(maxsize=20000); self.status=queue.Queue(maxsize=200)
        self.stop_event=threading.Event(); self.workers={}

    def stop(self):
        self.stop_event.set()

    def run(self):
        start=time.monotonic()
        try:
            if self.settings['mode']=='Dual receivers':
                try: validate_device_identity(self.settings,detect_devices())
                except (ValueError,OSError,subprocess.TimeoutExpired) as e:
                    self.status.put(('receiver',str(e))); return
            while not self.stop_event.is_set():
                desired=assignments(self.settings,time.monotonic()-start)
                for kind in list(self.workers):
                    if kind not in desired:
                        w=self.workers.pop(kind); w.stop(); w.join(timeout=12)
                        if w.is_alive():
                            self.status.put(('receiver','Could not release receiver; switching stopped.'))
                            return
                for kind,device in desired.items():
                    if kind not in self.workers:
                        w=DecoderWorker(kind,device,self.settings,self.folder,self.messages,self.status)
                        self.workers[kind]=w; w.start()
                self.stop_event.wait(.25)
        finally:
            for w in self.workers.values(): w.stop()
            for w in self.workers.values(): w.join(timeout=12)
