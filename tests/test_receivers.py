from airalert.core.receivers import assignments,validate_device_identity
from airalert.core.config import DEFAULTS
from airalert.core.parsers import ADSBParser
import pytest


def test_assignments():
    c=DEFAULTS.copy(); c['mode']='Aircraft'
    assert assignments(c,0)=={'aircraft':'0'}
    c['mode']='Marine'; assert assignments(c,0)=={'vessel':'0'}
    c['mode']='Automatic switching'
    assert assignments(c,59)=={'aircraft':'0'}
    assert assignments(c,60)=={'vessel':'0'}
    assert assignments(c,120)=={'aircraft':'0'}
    c['mode']='Dual receivers'; assert assignments(c,60)=={'aircraft':'0','vessel':'1'}
    c['marine_device']='0'
    with pytest.raises(ValueError): assignments(c,0)
    c['aircraft_device']='0'; c['marine_device']='12345678'
    with pytest.raises(ValueError): validate_device_identity(c,[dict(index='0',serial='12345678')])


def test_raw_adsb():
    p=ADSBParser()
    assert p.parse('*8D40621D58C382D690C8AC2863A7;',100)['altitude']==38000
    # Several coherent pairs establish pyModeS's validated CPR track.
    for i in range(1,8):
        r=p.parse('*'+('8D40621D58C382D690C8AC2863A7' if i%2==0 else '8D40621D58C386435CC412692AD6')+';',100+i*.1)
    assert r['lat']==pytest.approx(52.26,abs=.02)
    assert r['lon']==pytest.approx(3.94,abs=.02)
    assert p.parse('*8D40621D58C382D690C8AC286300;',102) is None
    assert p.parse('noise') is None
