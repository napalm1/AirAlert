# AirAlert

A Windows desktop station for tracking nearby aircraft (ADS-B, 1090 MHz) and vessels (AIS, 162 MHz) with an RTL-SDR receiver. It gives you alerts when something you care about comes close. Everything runs and stays on your computer, except the optional internet features listed under *Data and privacy*.

AirAlert keeps its program files, its data folder (`%LOCALAPPDATA%\AirAlert`) and its own copies of the decoders together. Only one program can use a USB receiver at a time, so close other SDR software before starting monitoring.

## Launch

Double-click **AirAlert** on the desktop, or run `dist\AirAlert\AirAlert.exe`. Keep the whole `dist\AirAlert` folder together (including `_internal`). Only one AirAlert runs at a time: opening it again brings the running window forward.

## Background alert station

Settings → **Startup**:
- **Keep running in the tray when the window is closed** (on by default): closing the window hides it, and monitoring and alerts continue. Quit from the tray icon's menu.
- **Start AirAlert when I sign in to Windows**: adds a shortcut to your Windows Startup folder that opens AirAlert quietly in the tray.
- **Start monitoring automatically** when AirAlert opens.

## The interface

- **Top bar**: page tabs (Live map, Alerts, History, Watchlist, Statistics; Left/Right/Home/End when focused), monitoring status, receiver mode picker, Start/Stop, light/dark toggle and Settings (Ctrl+,).
- **Live map**: a full-window map with floating panels.
  - *Traffic* (left): live list with search (Ctrl+F; Esc clears) and sort chips (distance, altitude, speed, bearing, name, ID, type, recent). Click a row to inspect it without moving the map; double-click to center on it. The panel collapses.
  - *Inspector* (right): slides in for the selected target, with key readouts, every received field (including model, manufacturer and operator from the aircraft database), **Watch**, **Create alert** and **Center** actions. A dash (—) means the field hasn't been received.
  - *Map tools* (right edge): zoom, center on station, **Layers**, **Geofences**, **Map style**, full screen (F11).
  - **Right-click** anywhere on the map to set your station (home) location at that point, or to center the map there. The same location can be typed or found with Windows location in Settings → Location.
  - **Layers**: aircraft, vessels, trails, range rings, labels, **airports** (with hover cards showing codes and radio frequencies), **heading lines** (where each target will be in 1–30 minutes), **coverage** (your receiver's best range in each direction) and an **altitude filter** (show only aircraft between two altitudes; the top of the slider means 50,000 ft and above).
  - *Latest alert ribbon* (bottom): flashes when an alert fires. **Show** jumps to the target.
  - Aircraft point along their heading and are colored by altitude (the legend in the lower left shows the scale and the other symbols). Aircraft that report no altitude are light blue. Vessels are teal, and their AIS ship type and navigation status are shown by name (Cargo, Tanker, Moored...). Positions older than 60 s turn gray. Watched targets get a ★. The selected target pulses. Aircraft squawking **7500 / 7600 / 7700** pulse red and are labeled as emergencies.
- **Map styles**: *Match app*, *Night map*, *Day map*, or *Radar scope*, a green PPI centered on your station with range rings, bearing marks and a cosmetic sweep. Drag moves the scope center and scrolling changes the range.
- **Street tiles** (optional, Settings → Map): OpenStreetMap tiles, restyled locally for the night map and cached up to 128 MB. Without tiles, an offline lat/lon grid is shown.

## Alerts

Alerts → **New alert** (or **Add emergency alert** for a ready-made squawk 7500/7600/7700 alert). Choose target type, an exact identity match (blank = everyone; fields include registration, ICAO, callsign, type, squawk, MMSI, name) and a condition:

- comes within / moves beyond a distance of home
- **will pass near home (predicted)**: uses the target's track and speed to warn you *before* it arrives, e.g. "passes 1.2 mi from home in 3 min"
- flies below an altitude, moves faster than a speed
- enters / leaves a geofence
- **squawks an emergency code**
- first detected, or heard

**Only when also…** adds up to six AND conditions (within/beyond a distance, below/above an altitude, faster/slower than a speed, inside/outside a geofence), e.g. "within 5 mi *and* below 3,000 ft". The plain-language preview at the top of the editor shows exactly what the alert means.

**Delivery**, per alert: sound, desktop pop-up, **spoken alert** (Windows voice, e.g. "Alert. N 1 2 3 A B, 4 miles north, 3,000 feet."), **phone** (ntfy and/or Pushover), and **Ignore quiet hours**. Every alert is also shown in the app and recorded in History. Entry fires once while inside; leaving and coming back re-arms it. Stale positions never trigger distance or zone alerts.

Settings → **Notifications**:
- **Quiet hours** (e.g. 22:00–07:00) hold back sound, voice, pop-ups and phone pushes except for alerts set to ignore quiet hours. Alerts are still recorded. The status bar shows when quiet hours are active.
- **ntfy**: install the free ntfy app, subscribe to a hard-to-guess topic (use **Generate**), enter it here and **Save & send test**. Works with ntfy.sh or your own server (optional access token).
- **Pushover**: enter your user key and an application API token, then **Save & send test**.
- **Test voice** plays a sample spoken alert.

## Receivers and reception check

Modes: Aircraft, Marine, Automatic switching (one receiver alternates bands and misses the other band while away), or Dual receivers (two different dongles). Settings → Receivers detects RTL-SDR devices; assign them by serial number. Gain, PPM and target expiry are under Advanced SDR. Settings can be changed while monitoring runs, except receiver settings.

**Trying it without a receiver**: Settings → General → **Show simulation mode** adds a *Simulation* mode that moves fictional aircraft and vessels near your home location, so alerts, geofences and the map can be tried with no hardware. It is off by default and the app never mentions simulation until it is switched on; simulated traffic is always recorded and counted separately from received RF.

**Run reception check…** (Settings → Receivers, with monitoring stopped) listens at several gains for 10–30 s each, counts frames, valid messages, targets and positions, and recommends the best gain with one click. If nothing is heard at any gain, check the antenna connection and placement; traffic is also much lighter at night.

If a receiver is found but can't be opened, close other SDR apps (such as SDR#) and check the WinUSB driver (Zadig). AirAlert never changes drivers.

## Geofences

Map tools → Geofences → **Draw new geofence**. Click at least three points, then double-click, press Enter or choose **Finish shape**. Right-click undoes the last point and Esc cancels. Name the zone (names are unique) and save. From the same menu you can show, rename or remove zones. Renaming updates the alerts that use the zone. Removing asks for confirmation and tells you how many alerts become inactive.

## History, watchlist, statistics

- **History**: search sightings or events (alerts, detections, receiver, app) by text, date range (quick 1 h–All presets), target type and maximum distance. Double-click a sighting, or use **Open track on map**, to show its dashed track. **Replay period on map** plays back all traffic from the chosen period with play/pause, speed (1×–1200×) and a time slider; the map is framed in amber while replaying. Export results or a track as CSV or JSON. **Database maintenance** shows size and oldest data and deletes old history after a confirmation step. Results are capped at 5,000.
- **Watchlist**: name, identifier (ICAO, registration or MMSI), type and notes, with last seen, encounter counts and a one-click alert. The **Aircraft database** card shows the built-in registration/type database (~570,000 aircraft, bundled). **Update from OpenSky** downloads the latest public database (about 25 MB); **Import CSV…** adds your own lookup (`icao`/`icao24`, `registration`, `type`/`typecode`), which takes precedence.
- **Statistics**: live counts and archive totals, plus charts for message rate (with a live 10-minute sparkline), aircraft by hour of day, unique aircraft and vessels per day, top aircraft types and operators, and a **coverage** rose of your receiver's range. Choose 24 hours / 7 days / 30 days (and, when simulation mode is switched on, whether to include simulated traffic).

## Data and privacy

`%LOCALAPPDATA%\AirAlert` holds `settings.json`, `history.sqlite`, `aircraft.sqlite` (aircraft database), `AirAlert.log` (rotating) and the optional tile cache. Retention runs at startup and daily.

Optional internet features, all off until you use them:
- **Street tiles**: OpenStreetMap receives your IP address and the map areas you view.
- **Update from OpenSky**: downloads a public aircraft database file. Nothing else is sent.
- **Phone notifications**: the alert text (target name, distance, rule) is sent to ntfy or Pushover.
- **Aircraft photos / likely routes**: the selected aircraft's ICAO address or callsign is sent to adsbdb.com; photos load from airport-data.com.
- **Update check**: asks github.com once a day whether a newer version exists (GitHub sees your IP address). Off in Settings → Connections.
- **Phone map**: serves the live map to paired devices on your home network only; nothing is sent to the internet.

Nothing else leaves your computer.

## More features (version 1.1)

- **Snooze**: silence one alert rule, one aircraft or everything for an hour or the rest of the day, from the alert
  ribbon, a rule card or the tray menu. Snoozed alerts are still recorded; the snooze ends by itself.
- **New and rare aircraft alerts**: conditions for an aircraft, aircraft type or airline never seen at your station
  before, a type seen fewer than N times in 30 days, **military** aircraft (reserved address blocks and operator
  names; a heuristic) and aircraft that are **circling**. They compare against your own History.
- **Flight phase and nearest airport**: the list and inspector show Climbing, Descending, Level, On approach to /
  Departing an airport, or Circling, worked out locally from the vertical rate, track and bundled airport data.
- **Alert history per rule**: each rule card shows how often it fired (24 h, 7 days, in all), when it last fired and a
  14-day bar strip, so noisy rules stand out.
- **Daily summary** (Settings → Notifications, off by default): one message a day with aircraft and vessels received,
  the farthest contact, alerts and hours monitored. Optional phone copy.
- **Reception warning**: tells you when the message rate falls below a quarter of what is normal for that hour at
  your station (it needs a few days of history). Usually an antenna, cable or USB problem.
- **Station records** (Statistics): farthest, highest and fastest aircraft, farthest vessel, busiest hour, day and minute.
- **Automatic backups** (Settings → Database): settings and history as one zip in a folder you choose, every N days,
  keeping the newest few. To restore, close AirAlert and unzip into the data folder.
- **Google Earth export**: History → Export track offers KML as well as CSV and JSON.
- **Live map on your phone** (Settings → Connections, off by default): open the address shown, on the same Wi-Fi, and
  pair the device once with the 6-digit code. Map, traffic list and alerts; view only. Only home-network addresses are
  answered, only a hash of each device's token is stored, and wrong codes are rate limited. Windows may ask to allow
  AirAlert through the firewall the first time: allow Private networks.
- **Aircraft photos and likely routes** (Settings → Connections, each off by default): for the selected aircraft, its
  ICAO address or callsign is sent to adsbdb.com and the photo is loaded from airport-data.com. Never for simulation.
- **Update check** (Settings → Connections, on by default): once a day AirAlert reads a small `latest.json` from the
  newest GitHub release and tells you when a newer version exists; it never downloads or installs by itself. To
  publish a release, attach both `release/AirAlert-Setup-<version>.exe` and `release/latest.json` (written by
  `make_installer.py`) to a GitHub release tagged `v<version>`; `UPDATE_URL` in `airalert/__init__.py` points at the
  `releases/latest/download/latest.json` address.

## Sharing AirAlert: the one-file installer

```
.venv\Scripts\python.exe tools\build.py            # 1. build and test the app (dist\AirAlert)
.venv\Scripts\python.exe tools\make_installer.py   # 2. pack it: release\AirAlert-Setup-<version>.exe (about 70 MB)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_installer.ps1   # 3. test the built installer end to end
```

Send friends the single `AirAlert-Setup-<version>.exe`. Double-clicking it opens a small setup window (install folder,
desktop shortcut, launch when finished). It installs for that Windows account only (**no administrator rights**) into
`%LOCALAPPDATA%\Programs\AirAlert`, adds a Start menu entry, and appears in Settings > Apps > Installed apps, where it
can be uninstalled (settings and history are kept unless you tick delete). Running a newer installer updates in place and
keeps settings and history. Friends get a fresh setup wizard on first launch; nothing of yours is inside the installer.

Windows 10 or 11 (64-bit). An RTL-SDR receiver needs its WinUSB driver (install it with Zadig); without a receiver,
switch on simulation mode in Settings → General to look around.

The installer is not code-signed (that needs a paid certificate), so Windows SmartScreen may say "Windows protected your
PC" the first time: choose **More info > Run anyway**. Some antivirus programs are cautious about any unsigned
single-file installer; the SHA-256 printed by `make_installer.py` lets a friend confirm the file arrived intact
(`Get-FileHash .\AirAlert-Setup-<version>.exe`).

Silent install for scripts: `AirAlert-Setup-<version>.exe /S [/D=C:\folder] [/NODESKTOP] [/LAUNCH]`; the exit code is 0 on
success. Problems are logged to `%TEMP%\AirAlert-setup.log`. An install never leaves a half-finished state: files are
unpacked beside the target and swapped in only when complete, and any failure restores the previous version.

## Development

```
.venv\Scripts\python.exe main.py                 # run from source (--minimized starts in the tray)
.venv\Scripts\python.exe -m pytest -q            # tests (engine, backend, alerts, notifications, UI offscreen)
.venv\Scripts\python.exe tools\decoder_test.py   # real dump1090 / AIS-catcher decoding
.venv\Scripts\python.exe tools\bench_map.py      # map paint-time benchmark
.venv\Scripts\python.exe tools\profile_app.py    # startup, engine tick and refresh timings (--profile: hottest functions)
.venv\Scripts\python.exe tools\profile_db.py     # history-database timings on a months-of-use archive
.venv\Scripts\python.exe tools\build.py          # tests + package + packaged self-test
.venv\Scripts\python.exe tools\make_installer.py # the one-file installer (see above)
dist\AirAlert\AirAlert.exe --self-test <empty-folder>
```

Speed notes: work that grows with your history (statistics, watchlist lookups, history search, startup maintenance)
is index-driven or cached and never rewrites the database on every launch; the interface stops refreshing while the
window is hidden in the tray; and there are no endless decorative animations, because Qt redraws the whole window at
60 frames a second while any animation runs (`tests/test_speed.py` lists the few deliberate ones).

This machine's Python is the Microsoft Store build, so running from source with it redirects `%LOCALAPPDATA%` writes into `AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_…\LocalCache\Local\AirAlert`, separate from the real folder the exe uses. Set `AIRALERT_DATA` to choose a data folder explicitly when running from source.

Layout: `airalert/core` (receivers, decoding, alert rules, settings and the history database), `airalert/engine.py` (monitoring engine), `airalert/notify.py` (quiet hours, voice, phone), `airalert/startup.py`, `airalert/aircraftdb.py` + `airalert/insights.py` (aircraft database, statistics), `airalert/airports.py`, `airalert/playback.py`, `airalert/reception.py`, `airalert/ui` (controller, feature bridges, map canvas, tiles, models), `airalert/qml` (interface), `vendor` (decoders, aircraft and airport data), `installer` (the setup program and uninstaller), `tools` (build, installer, icon, screenshots, benchmarks).

Source is GPL-3.0 (pyModeS is GPL-3.0); see LICENSE and THIRD_PARTY.md.
