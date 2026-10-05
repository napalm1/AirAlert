import json
from pathlib import Path
import pytest
from airalert.core.alerts import AlertEngine
from airalert.core.models import TargetStore,Target,destination
from airalert.core.database import Database,export_rows
from airalert.core.parsers import AISParser


def test_zone_exit_and_first():
    s=TargetStore(); engine=AlertEngine()
    zones=[dict(name='harbor',points=[[-1,-1],[-1,1],[1,1],[1,-1]])]
    rules=[dict(id='zone',name='Leaving',kind='vessel',field='mmsi',value='990000001',condition='zone_leave',zone='harbor',cooldown=0),
           dict(id='first',name='First',kind='vessel',condition='first',cooldown=0)]
    def step(lat,now):
        t,_=s.update(dict(kind='vessel',identifier='990000001',lat=lat,lon=0),now)
        return engine.evaluate(t,rules,[0,0],zones,now)
    assert len(step(0,100))==1
    assert not step(0,101)
    assert len(step(2,102))==1
    assert not step(2,103)
    assert not step(0,104)
    assert len(step(2,105))==1


def test_unknown_position_no_proximity_and_identity():
    e=AlertEngine(); t=Target('aircraft','A12345',100,100,{'registration':'N123AB'})
    r=dict(id='r',name='near',kind='aircraft',field='registration',value='N123AB',condition='enter',threshold=10)
    assert not e.evaluate(t,[r],[0,0],[],100)
    t.data.update(lat=0,lon=0); t.position_time=100
    assert not e.evaluate(t,[r],[0,0],[],200)
    t.position_time=200; assert e.evaluate(t,[r],[0,0],[],200)


def test_lookup_and_real_sim_separation(tmp_path):
    csv=tmp_path/'aircraft.csv'; csv.write_text('icao24,registration,typecode\nabcdef,N123AB,C172\n')
    db=Database(tmp_path/'db'); assert db.import_aircraft(csv)==1
    assert db.lookup('ABCDEF')=={'registration':'N123AB','type':'C172'}
    for sim in (True,False):
        t=Target('aircraft','ABCDEF',100,100,dict(lat=0,lon=0),position_time=100,simulated=sim)
        db.record(t,new=True)
    assert len(db.history())==2
    assert len(db.track('sim/aircraft:ABCDEF'))==1
    assert len(db.track('aircraft:ABCDEF'))==1
    export_rows(tmp_path/'safe.csv',[{'name':'=HYPERLINK("bad")','id':'123'}])
    assert "'=HYPERLINK" in (tmp_path/'safe.csv').read_text('utf-8-sig')
    db.close()


def test_ais_multipart_timeout_and_unknown_values():
    from pyais.encode import encode_dict
    p=AISParser()
    parts=encode_dict(dict(msg_type=5,mmsi=123456789,shipname='TEST'),talker_id='AI',sentence_type='VDM')
    assert p.parse(parts[0],100) is None
    assert p.parse(parts[1],120) is None
    line=encode_dict(dict(msg_type=1,mmsi=123456789,lat=91,lon=181,heading=511,speed=102.3,course=360),talker_id='AI',sentence_type='VDM')[0]
    r=p.parse(line); assert not any(k in r for k in ('lat','lon','heading','speed','course'))


def test_load_bounds():
    s=TargetStore()
    for i in range(10000): s.update(dict(kind='aircraft',identifier=f'{i:06X}',lat=32,lon=-118),100)
    assert len(s.targets)==10000
    assert len(s.expire(221))==10000
