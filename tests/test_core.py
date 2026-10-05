import json
import pytest
from airalert.core.config import Config
from airalert.core.models import TargetStore, distance_bearing, destination, in_polygon, matches_identity
from airalert.core.alerts import AlertEngine
from airalert.core.database import Database, export_rows
from airalert.core.parsers import AISParser
from airalert.core.simulation import Simulation


def test_config(tmp_path):
    c = Config(tmp_path); c.values['home']=[32,-118]; c.save()
    assert Config(tmp_path)['home']==[32,-118]
    c.path.write_text('{broken')
    assert Config(tmp_path).warning
    assert list(tmp_path.glob('*.invalid-*.json'))
    with pytest.raises(ValueError): Config.validate({'home':[999,0]})


def test_geography():
    d,b = distance_bearing((0,0),(0,1))
    assert d == pytest.approx(111.195,abs=.01)
    assert b == pytest.approx(90)
    assert distance_bearing((0,0),destination((0,0),25,230)) == pytest.approx((25,230))
    assert distance_bearing((0,0),(0,180))[0] == pytest.approx(20015.11,abs=.1)
    assert in_polygon((1,1),[(0,0),(0,2),(2,2),(2,0)])
    assert not in_polygon((3,1),[(0,0),(0,2),(2,2),(2,0)])


def test_merge_and_expire():
    s=TargetStore()
    t,new=s.update(dict(kind='vessel',identifier='123456789',lat=1,lon=2),100)
    assert new
    s.update(dict(kind='vessel',identifier='123456789',name='Sea Bird'),101)
    assert t.position==(1,2) and t.position_time==100 and t.data['name']=='Sea Bird'
    assert matches_identity(t,'mmsi','123456789')
    assert s.expire(1000)==[]
    assert s.expire(1002)==['vessel:123456789']


def test_alert_reentry_cooldown():
    s=TargetStore(); a=AlertEngine()
    rule=dict(id='1',name='Tail watch',kind='aircraft',field='registration',value='N123AB',condition='enter',threshold=40,cooldown=10)
    def check(km,now):
        lat,lon=destination((0,0),km,90)
        t,_=s.update(dict(kind='aircraft',identifier='AB1234',registration='N123AB',lat=lat,lon=lon),now)
        return a.evaluate(t,[rule],(0,0),[],now)
    assert not check(50,100)
    assert len(check(30,101))==1
    assert not check(30,102)
    assert not check(50,103)
    assert not check(30,104)
    assert not check(30,120)
    assert not check(50,121)
    assert len(check(30,122))==1


def test_db_export(tmp_path):
    db=Database(tmp_path/'db.sqlite'); s=TargetStore()
    t,_=s.update(dict(kind='aircraft',identifier='ABCDEF',callsign='TEST1',lat=1,lon=2,simulated=True),100)
    db.record(t,5,new=True); db.record(t,5)
    assert len(db.track('sim/aircraft:ABCDEF'))==1
    assert len(db.history('test',distance=6))==1
    assert not db.history('test',kind='vessel')
    assert not db.history(start=101)
    db.event('alert',t.key,'hello',now=100)
    assert len(db.events('hello'))==1
    export_rows(tmp_path/'data.json', db.history())
    assert json.loads((tmp_path/'data.json').read_text())[0]['identifier']=='ABCDEF'
    db.conn.commit(); db.close()
    db=Database(tmp_path/'db.sqlite'); assert len(db.history())==1
    db.maintenance(101); assert not db.history(); db.close()


def test_ais():
    from pyais.encode import encode_dict
    parser=AISParser()
    lines=encode_dict({'msg_type':1,'mmsi':123456789,'lat':32.1,'lon':-118.1,'speed':12.3,'course':90},talker_id='AI',sentence_type='VDM')
    r=parser.parse(lines[0]); assert r['identifier']=='123456789' and r['lat']==pytest.approx(32.1)
    assert parser.parse(lines[0][:-2]+'00') is None
    lines=encode_dict({'msg_type':5,'mmsi':123456789,'shipname':'SEA BIRD','callsign':'ABC123','destination':'PORT'},talker_id='AI',sentence_type='VDM')
    result=None
    for line in lines: result=parser.parse(line)
    assert result['name']=='SEA BIRD' and 'lat' not in result


def test_simulation():
    sim=Simulation((32,-118)); a=sim.step(); b=sim.step()
    assert len(a)==9 and all(x['simulated'] for x in a)
    assert a[0]['lat']!=b[0]['lat']
    assert a[1]['altitude']!=b[1]['altitude']
