import math
from .models import destination


class Simulation:
    """Deterministic synthetic traffic; never presented as received RF."""
    def __init__(self, home):
        self.home = home
        self.elapsed = 0

    def step(self, seconds=1):
        self.elapsed += seconds
        t = self.elapsed
        rows = []
        for i in range(5):
            angle = t*(0.7+i*.1)+i*67
            radius = 35+18*math.cos(t/25) if i == 0 else 12+i*9
            lat, lon = destination(self.home,radius,angle)
            rows.append(dict(kind='aircraft',identifier=f'A0{i+1:04X}',registration=f'N{123+i}AB',
                callsign=f'DEMO{i+1}',type=['C172','B738','A320','PA28','B77W'][i],
                lat=lat,lon=lon,heading=(angle+90)%360,altitude=4500+i*4000+1500*math.sin(t/30),
                speed=110+i*65,vertical_speed=300*math.cos(t/30),squawk='1200',simulated=True))
        for i in range(4):
            angle = i*85+20*math.sin(t/35)
            radius = 1.5+5*(1+math.cos(t/25+i))/2
            lat, lon = destination(self.home,radius,angle)
            rows.append(dict(kind='vessel',identifier=f'99000000{i+1}',name=['DEMO HORIZON','DEMO SEABIRD','DEMO NORTH STAR','DEMO PILOT'][i],
                callsign=f'TEST{i+1}',type=[70,36,80,50][i],lat=lat,lon=lon,heading=(angle+180)%360,
                course=(angle+180)%360,speed=8+i*2,navigation_status=0,destination='SIMULATION',simulated=True))
        return rows
