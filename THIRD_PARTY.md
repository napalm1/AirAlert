# Dependencies and notices

AirAlert's source is GPL-3.0 because it uses GPL-3.0 pyModeS. Its backend (`airalert/core`) is adapted from HarborScope (GPL-3.0).

| Component | Version / source | License / purpose |
|---|---|---|
| Python | 3.13 | PSF; packaged runtime |
| PySide6 Essentials / Qt / shiboken6 | 6.11.2 | LGPL-3.0/GPL; Qt Quick interface, networking, rendering |
| pyModeS | 3.6.0, https://github.com/junzis/pyModeS | GPL-3.0; Mode-S decoding and CPR validation |
| pyais | 3.2.3, https://github.com/M0r13n/pyais | MIT; AIS message parsing |
| Dump1090 for Windows | v1.0.1, https://github.com/gvanem/Dump1090 | MIT; RTL-SDR Mode-S demodulation |
| AIS-catcher | v0.70, https://github.com/jvde-github/AIS-catcher | GPL-3.0; AIS demodulation (with upstream RTL-SDR/libusb and other DLLs, licenses in vendor/ais/Licenses) |
| PyInstaller | 6.22.3 | GPL with bootloader exception; build only |
| Pillow | 12.3.0 | HPND; icon generation only |
| pytest | 9.1.1 | MIT; tests only |
| OpenStreetMap | https://www.openstreetmap.org/copyright | ODbL data / tile usage policy; optional street tiles |
| OpenSky Network aircraft database | https://opensky-network.org (aircraftDatabase) | Registrations/types in the bundled `vendor/aircraft/aircraft.sqlite` snapshot and the optional in-app update; see OpenSky's terms of use |
| dump1090-fa aircraft data | https://github.com/flightaware/dump1090 (public_html/db) | GPL-2.0-or-later; ICAO type codes merged into the bundled aircraft snapshot |
| OurAirports data (via Dump1090 for Windows) | https://ourairports.com/data/ | Public domain; airports and radio frequencies in `vendor/airports` |

`licenses/` contains the dump1090 license, upstream dependency licenses and Qt license texts. `tests/fixtures/modes1.bin` comes from dump1090 v1.0.1's test files.
