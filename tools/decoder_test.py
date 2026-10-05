"""Synthetic AIS IQ and upstream recorded ADS-B IQ decoder integration tests."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import cmath
import json
import math
import struct
import subprocess
import sys
from pathlib import Path
from pyais.encode import encode_dict
from airalert.core.config import DEFAULTS
from airalert.core.receivers import adsb_config,resource_root,FLAGS
from airalert.core.parsers import ADSBParser,AISParser


def make_ais_iq(path):
    nmea=encode_dict(dict(msg_type=1,mmsi=990000001,lat=32.1,lon=-118.1,speed=12.3,course=90),talker_id='AI',sentence_type='VDM')[0]
    p=nmea.split(','); armored=p[5]
    bits=''.join(f'{ord(c)-48-(8 if ord(c)>87 else 0):06b}' for c in armored)
    fill=int(p[6].split('*')[0]); bits=bits[:-fill] if fill else bits
    payload=bytes(int(bits[i:i+8],2) for i in range(0,len(bits),8))
    crc=0xffff
    for byte in payload:
        crc^=byte
        for _ in range(8): crc=(crc>>1)^0x8408 if crc&1 else crc>>1
    crc^=0xffff
    payload+=bytes((crc&255,crc>>8))
    stream=[]; ones=0
    for byte in payload:
        for j in range(8):
            bit=(byte>>j)&1; stream.append(bit)
            ones=ones+1 if bit else 0
            if ones==5: stream.append(0); ones=0
    flag=[0,1,1,1,1,1,1,0]
    stream=[0,1]*12+flag+stream+flag+[0]*24
    level=1; symbols=[]
    for bit in stream:
        if bit==0: level=-level
        symbols.extend([level]*10)
    sigma=math.sqrt(math.log(2))/(2*math.pi*.4)*10
    kernel=[math.exp(-i*i/(2*sigma*sigma)) for i in range(-20,21)]; total=sum(kernel)
    phase=0; wave=[]
    for i in range(len(symbols)):
        smooth=sum(symbols[min(len(symbols)-1,max(0,i+j-20))]*v for j,v in enumerate(kernel))/total
        phase+=2*math.pi*(2400*smooth-25000)/96000
        wave.append(.6*cmath.exp(1j*phase))
    out=bytearray()
    for _ in range(12):
        for v in [0j]*9600+wave: out.extend(struct.pack('<ff',v.real,v.imag))
    path.write_bytes(out)
    return nmea


def main():
    packaged=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else None
    folder=Path('test-output/packaged-decoder-iq' if packaged else 'test-output/decoder-iq').resolve(); folder.mkdir(parents=True,exist_ok=True)
    expected=make_ais_iq(folder/'ais.cf32')
    root=packaged or resource_root(); results={}
    args=[str(root/'vendor/ais/AIS-catcher.exe'),'-r','CF32',str(folder/'ais.cf32'),'-s','96000','-o','1']
    r=subprocess.run(args,capture_output=True,text=True,timeout=30,creationflags=FLAGS)
    (folder/'ais-output.txt').write_text(r.stdout+r.stderr)
    parser=AISParser(); rows=[v for line in r.stdout.splitlines() if (v:=parser.parse(line))]
    results['ais']={'exit':r.returncode,'messages':len(rows),'expected_nmea':expected,'decoded':rows[:1]}
    adsb_config(folder/'adsb.cfg',DEFAULTS)
    iq=bytearray()
    for i in range(100):
        frame='8D40621D58C382D690C8AC2863A7' if i%2==0 else '8D40621D58C386435CC412692AD6'
        pulse=[1 if i in (0,2,7,9) else 0 for i in range(16)]
        for bit in ''.join(f'{int(c,16):04b}' for c in frame): pulse.extend([1,0] if bit=='1' else [0,1])
        iq.extend(bytes([127,127])*4096)
        for v in pulse: iq.extend(bytes([227 if v else 127,127]))
    (folder/'adsb.cu8').write_bytes(iq)
    args=[str(root/'vendor/adsb/dump1090.exe'),'--config',str(folder/'adsb.cfg'),'--infile',str(folder/'adsb.cu8'),'--samplerate','2M','--raw']
    r=subprocess.run(args,capture_output=True,text=True,timeout=30,creationflags=FLAGS)
    (folder/'adsb-output.txt').write_text(r.stdout+r.stderr)
    parser=ADSBParser(); rows=[v for line in r.stdout.splitlines() if (v:=parser.parse(line))]
    results['adsb']={'exit':r.returncode,'messages':len(rows),'decoded':rows[:2]}
    args[args.index('--infile')+1]=str(Path(__file__).resolve().parent.parent/'tests/fixtures/modes1.bin')
    r=subprocess.run(args,capture_output=True,text=True,timeout=30,creationflags=FLAGS)
    (folder/'adsb-recorded-output.txt').write_text(r.stdout+r.stderr)
    parser=ADSBParser(); recorded=[v for line in r.stdout.splitlines() if (v:=parser.parse(line))]
    results['adsb_recorded']={'exit':r.returncode,'messages':len(recorded),'decoded':recorded[:2]}
    (folder/'results.json').write_text(json.dumps(results,indent=2)); print(json.dumps(results,indent=2))
    assert results['ais']['messages']>0 and results['adsb']['messages']>0 and results['adsb_recorded']['messages']>0
    assert all(result['exit']==0 for result in results.values())


if __name__=='__main__': main()
