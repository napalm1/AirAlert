import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

AModal {
    id: dlg
    property bool first: false
    property int sectionIndex: 0
    property var devices: []
    property string deviceStatus: ""
    property bool detecting: false
    property bool locating: false
    property string locationStatus: "Manual coordinates stay on this computer. Windows location is optional and needs an enabled location provider."
    property string error: ""
    property string folder: ""
    property string backupStatus: ""
    // Form state
    property string mode: "Aircraft"
    property bool showSimulation: false
    property string units: "mi"
    property string theme: "Dark"
    property string mapTheme: "Follow app"
    property bool homeEnabled: false
    property bool tiles: false
    property bool running: false
    property bool trayAvailable: true
    property bool closeToTray: true
    property bool startWithWindows: false
    property bool autoStart: false
    property bool quietEnabled: false
    property bool ntfyEnabled: false
    property bool pushoverEnabled: false
    property bool summaryEnabled: false
    property bool summaryPhone: true
    property bool healthWarning: true
    property bool backupEnabled: false
    property bool lookupPhotos: false
    property bool lookupRoutes: false
    property bool checkUpdates: true
    property bool phoneMapEnabled: false
    readonly property var web: typeof phonemap !== "undefined" && phonemap !== null ? phonemap : null

    readonly property var sections: [
        { key: "general", label: "General", icon: "globe", note: "Mode, units, appearance" },
        { key: "location", label: "Location", icon: "pin", note: "Your station position" },
        { key: "receivers", label: "Receivers", icon: "antenna", note: "RTL-SDR assignment" },
        { key: "sdr", label: "Advanced SDR", icon: "signal", note: "Gain, PPM, expiry" },
        { key: "map", label: "Map", icon: "map", note: "Style, tiles, rings" },
        { key: "notifications", label: "Notifications", icon: "bell", note: "Quiet hours, phone, voice" },
        { key: "connections", label: "Connections", icon: "monitor", note: "Phone map, photos, updates" },
        { key: "startup", label: "Startup", icon: "home", note: "Tray, Windows sign-in" },
        { key: "database", label: "Database", icon: "database", note: "Sampling & retention" }
    ]
    readonly property var modeList: app.modesFor(showSimulation)
    readonly property var modeNotes: ({
        "Simulation": "Fictional aircraft and vessels move near your home location. No hardware needed — ideal for trying alerts and geofences.",
        "Aircraft": "The primary receiver listens for ADS-B at 1090 MHz, 2.4 MS/s.",
        "Marine": "The primary receiver listens for AIS channels A and B (161.975 / 162.025 MHz) in one 1.536 MS/s passband.",
        "Automatic switching": "One receiver alternates between aircraft and marine bands using the dwell times on the Receivers page. Transmissions on the other band are missed while it is tuned away.",
        "Dual receivers": "Two different receivers run continuously: primary for aircraft, second for marine. Assign distinct serial numbers."
    })

    title: first ? "Welcome to AirAlert" : "Settings"
    subtitle: first ? "Set up your station in a few steps — you can change everything later."
                    : (running ? "Monitoring is running: receiver settings are locked; everything else can change now."
                               : "Station, receivers, map, notifications, startup and storage.")
    iconName: first ? "radar" : "sliders"
    width: 920
    height: Math.min(Overlay.overlay ? Overlay.overlay.height - 50 : 700, 700)

    function openWith(isFirst, section) {
        var d = app.settingsData()
        first = isFirst
        mode = d.mode; units = d.units; theme = d.theme; mapTheme = d.map_theme; showSimulation = d.show_simulation
        homeEnabled = d.homeEnabled; tiles = d.tiles; folder = d.folder
        latField.text = d.homeEnabled ? Number(d.lat).toFixed(6) : ""
        lonField.text = d.homeEnabled ? Number(d.lon).toFixed(6) : ""
        primaryField.text = d.aircraft_device; secondField.text = d.marine_device
        aircraftDwell.text = String(d.aircraft_seconds); marineDwell.text = String(d.marine_seconds)
        gainField.text = d.gain; ppmField.text = String(d.ppm)
        aircraftTtl.text = String(d.aircraft_ttl); vesselTtl.text = String(d.vessel_ttl)
        ringsField.text = d.rings; trailField.text = String(d.trail_minutes)
        sampleField.text = String(d.sample_seconds); retentionField.text = String(d.retention_days)
        running = d.running; trayAvailable = d.trayAvailable
        closeToTray = d.close_to_tray; startWithWindows = d.start_with_windows; autoStart = d.auto_start
        quietEnabled = d.quietEnabled; quietStartField.text = d.quietStart; quietEndField.text = d.quietEnd
        ntfyEnabled = d.ntfy_enabled; ntfyServerField.text = d.ntfy_server || "https://ntfy.sh"
        ntfyTopicField.text = d.ntfy_topic; ntfyTokenField.text = d.ntfy_token
        pushoverEnabled = d.pushover_enabled; pushoverUserField.text = d.pushover_user
        pushoverTokenField.text = d.pushover_token
        summaryEnabled = d.summaryEnabled; summaryPhone = d.summaryPhone; summaryTimeField.text = d.summaryTime
        healthWarning = d.health_warning
        backupEnabled = d.backupEnabled; backupFolderField.text = d.backupFolder
        backupDaysField.text = String(d.backupDays); backupKeepField.text = String(d.backupKeep)
        backupStatus = app.lastBackupText(d.backupFolder)
        lookupPhotos = d.lookupPhotos; lookupRoutes = d.lookupRoutes; checkUpdates = d.check_updates
        phoneMapEnabled = d.phoneMapEnabled; phoneMapPortField.text = String(d.phoneMapPort)
        modeBox.currentIndex = Math.max(0, dlg.modeList.indexOf(mode))
        mapBox.currentIndex = Math.max(0, mapBox.indexOfValue(mapTheme))
        var index = 0
        for (var i = 0; i < sections.length; i++) if (sections[i].key === section) index = i
        sectionIndex = index
        error = ""
        devices = []
        if (running) deviceStatus = "Receiver detection is paused while monitoring uses the receiver."
        else detect()
        open()
    }

    function detect() {
        detecting = true
        deviceStatus = "Looking for RTL-SDR receivers…"
        app.detectReceivers()
    }

    function save(keepOpen) {
        var result = app.saveSettings({
            mode: mode, units: units, theme: theme, show_simulation: showSimulation,
            homeEnabled: homeEnabled, lat: latField.text, lon: lonField.text,
            aircraft_device: primaryField.text, marine_device: secondField.text,
            aircraft_seconds: aircraftDwell.text, marine_seconds: marineDwell.text, gain: gainField.text || "auto",
            ppm: ppmField.text || "0", aircraft_ttl: aircraftTtl.text, vessel_ttl: vesselTtl.text, map_theme: mapTheme,
            tiles: tiles, rings: ringsField.text, trail_minutes: trailField.text, sample_seconds: sampleField.text,
            retention_days: retentionField.text, close_to_tray: closeToTray, start_with_windows: startWithWindows,
            auto_start: autoStart, quietEnabled: quietEnabled, quietStart: quietStartField.text,
            quietEnd: quietEndField.text, ntfy_enabled: ntfyEnabled, ntfy_server: ntfyServerField.text,
            ntfy_topic: ntfyTopicField.text, ntfy_token: ntfyTokenField.text, pushover_enabled: pushoverEnabled,
            pushover_user: pushoverUserField.text, pushover_token: pushoverTokenField.text,
            summaryEnabled: summaryEnabled, summaryTime: summaryTimeField.text, summaryPhone: summaryPhone,
            health_warning: healthWarning, backupEnabled: backupEnabled, backupFolder: backupFolderField.text,
            backupDays: backupDaysField.text || "7", backupKeep: backupKeepField.text || "5",
            lookupPhotos: lookupPhotos, lookupRoutes: lookupRoutes, check_updates: checkUpdates,
            phoneMapEnabled: phoneMapEnabled, phoneMapPort: phoneMapPortField.text || "8765" })
        if (result !== "") error = result
        else if (!keepOpen) close()
        return result
    }

    Connections {
        target: app
        function onReceiversDetected(text, list) {
            dlg.detecting = false
            dlg.deviceStatus = text
            dlg.devices = list
            if (list.length > 0 && primaryField.text === "0") primaryField.text = list[0].serial
            if (list.length > 1 && secondField.text === "1") secondField.text = list[1].serial
        }
        function onLocationResolved(ok, lat, lon, text) {
            dlg.locating = false
            dlg.locationStatus = text
            if (ok) {
                latField.text = lat.toFixed(6)
                lonField.text = lon.toFixed(6)
                dlg.homeEnabled = true
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 230
            Layout.fillHeight: true
            color: Theme.raised
            Column {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 4
                Repeater {
                    model: dlg.sections
                    delegate: Rectangle {
                        id: navItem
                        required property var modelData
                        required property int index
                        readonly property bool current: dlg.sectionIndex === index
                        width: parent.width
                        height: 54
                        radius: 10
                        color: current ? Theme.panel : navArea.containsMouse ? Theme.hover : "transparent"
                        border.color: current ? Theme.border : "transparent"
                        Rectangle {
                            width: 30
                            height: 30
                            radius: 9
                            x: 10
                            anchors.verticalCenter: parent.verticalCenter
                            color: navItem.current ? Theme.accentSoft : "transparent"
                            Text {
                                visible: dlg.first
                                anchors.centerIn: parent
                                text: navItem.index + 1
                                color: navItem.current ? Theme.accent : Theme.muted
                                font.pixelSize: 13
                                font.weight: Font.Bold
                            }
                            Icon {
                                visible: !dlg.first
                                anchors.centerIn: parent
                                name: navItem.modelData.icon
                                size: 17
                                color: navItem.current ? Theme.accent : Theme.textDim
                            }
                        }
                        Column {
                            x: 50
                            anchors.verticalCenter: parent.verticalCenter
                            Text { text: navItem.modelData.label; color: navItem.current ? Theme.text : Theme.textDim; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Text { text: navItem.modelData.note; color: Theme.muted; font.pixelSize: 11 }
                        }
                        MouseArea {
                            id: navArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: dlg.sectionIndex = navItem.index
                        }
                    }
                }
            }
        }
        Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.border }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: dlg.sectionIndex

            // General
            SettingsSection {
                FormRow {
                    label: "Monitoring mode"
                    ACombo {
                        id: modeBox; enabled: !dlg.running
                        Layout.preferredWidth: 280
                        model: dlg.modeList
                        onActivated: (i) => dlg.mode = dlg.modeList[i]
                    }
                    Text {
                        Layout.fillWidth: true
                        text: dlg.modeNotes[dlg.mode] || ""
                        color: Theme.muted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }
                FormRow {
                    label: "Distance units"
                    Segmented {
                        options: [{ value: "mi", label: "Miles" }, { value: "nm", label: "Nautical miles" }, { value: "km", label: "Kilometers" }]
                        value: dlg.units
                        onPicked: (v) => dlg.units = v
                    }
                }
                FormRow {
                    label: "Appearance"
                    Segmented {
                        options: [{ value: "Dark", label: "Dark" }, { value: "Light", label: "Light" }]
                        value: dlg.theme
                        onPicked: (v) => dlg.theme = v
                    }
                }
                ASwitch {
                    objectName: "showSimulation"
                    Layout.fillWidth: true
                    text: "Show simulation mode"
                    subtitle: "Adds a Simulation monitoring mode with fictional aircraft and vessels near your home location, for trying alerts and geofences without a receiver."
                    checked: dlg.showSimulation
                    onToggled: {
                        dlg.showSimulation = checked
                        if (!checked && dlg.mode === "Simulation") dlg.mode = "Aircraft"
                        modeBox.currentIndex = Math.max(0, dlg.modeList.indexOf(dlg.mode))
                    }
                }
                InfoBox {
                    text: "One receiver tunes to one service at a time. Automatic switching misses transmissions on the other band; two distinct receivers are required for continuous dual reception. AirAlert and other SDR programs (such as SDR#) cannot use the same receiver at the same time."
                }
            }

            // Location
            SettingsSection {
                ASwitch {
                    text: "Use a home / receiver location"
                    subtitle: "Needed for distance, bearing, range rings and proximity alerts" + (dlg.showSimulation ? ", and for simulation." : ".")
                    checked: dlg.homeEnabled
                    onToggled: dlg.homeEnabled = checked
                    Layout.fillWidth: true
                }
                RowLayout {
                    spacing: 12
                    enabled: dlg.homeEnabled
                    opacity: enabled ? 1 : 0.5
                    FormRow {
                        label: "Latitude"
                        AField { id: latField; Layout.preferredWidth: 200; placeholderText: "e.g. 40.712800"; validator: DoubleValidator { bottom: -90; top: 90 } }
                    }
                    FormRow {
                        label: "Longitude"
                        AField { id: lonField; Layout.preferredWidth: 200; placeholderText: "e.g. -74.006000"; validator: DoubleValidator { bottom: -180; top: 180 } }
                    }
                }
                RowLayout {
                    spacing: 12
                    AButton {
                        text: dlg.locating ? "Locating…" : "Use computer location"
                        iconName: "locate"
                        enabled: !dlg.locating
                        onClicked: { dlg.locating = true; dlg.locationStatus = "Waiting for Windows location (up to 12 seconds)…"; app.locateComputer() }
                    }
                }
                InfoBox { text: dlg.locationStatus }
            }

            // Receivers
            SettingsSection {
                RowLayout {
                    spacing: 12
                    Layout.fillWidth: true
                    AButton { text: dlg.detecting ? "Detecting…" : "Detect receivers"; iconName: "antenna"; enabled: !dlg.detecting; onClicked: dlg.detect() }
                    Text { Layout.fillWidth: true; text: dlg.deviceStatus; color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap }
                }
                Repeater {
                    model: dlg.devices
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 58
                        radius: 11
                        color: Theme.raised
                        border.color: primaryField.text === modelData.serial || secondField.text === modelData.serial ? Util.tint(Theme.accent, 0.6) : Theme.border
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 12
                            Icon { name: "antenna"; size: 20; color: Theme.accent }
                            Column {
                                Layout.fillWidth: true
                                Text { text: "Receiver " + modelData.index + " · " + modelData.name; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                                Text { text: "Serial " + modelData.serial; color: Theme.muted; font.pixelSize: 12 }
                            }
                            AButton { text: primaryField.text === modelData.serial ? "Primary ✓" : "Use as primary"; compact: true; onClicked: primaryField.text = modelData.serial }
                            AButton { text: secondField.text === modelData.serial ? "Second ✓" : "Use as second"; compact: true; variant: "ghost"; onClicked: secondField.text = modelData.serial }
                        }
                    }
                }
                RowLayout {
                    visible: typeof reception !== "undefined"
                    spacing: 12
                    AButton {
                        text: "Run reception check\u2026"
                        iconName: "signal"
                        enabled: !dlg.running
                        onClicked: { dlg.close(); reception.open() }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: dlg.running ? "Stop monitoring to run a reception check." : "Tries several gains and shows which one hears the most traffic."
                        color: Theme.muted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }
                RowLayout {
                    spacing: 12
                    FormRow { label: "Primary receiver (serial or index)"; AField { id: primaryField; enabled: !dlg.running; Layout.preferredWidth: 240 } }
                    FormRow { label: "Second receiver (dual mode)"; AField { id: secondField; enabled: !dlg.running; Layout.preferredWidth: 240 } }
                }
                RowLayout {
                    spacing: 12
                    FormRow { label: "Aircraft dwell (switching)"; AField { id: aircraftDwell; enabled: !dlg.running; Layout.preferredWidth: 180; suffix: "seconds"; validator: IntValidator { bottom: 5; top: 3600 } } }
                    FormRow { label: "Marine dwell (switching)"; AField { id: marineDwell; enabled: !dlg.running; Layout.preferredWidth: 180; suffix: "seconds"; validator: IntValidator { bottom: 5; top: 3600 } } }
                }
                InfoBox {
                    text: "Use unique hardware serial numbers; numeric indices can change after reconnecting. If a dongle is found but cannot be opened, close other SDR apps and check that its Bulk-In Interface 0 uses the WinUSB driver (Zadig). AirAlert never installs or changes drivers."
                }
            }

            // Advanced SDR
            SettingsSection {
                RowLayout {
                    spacing: 12
                    FormRow { label: "Gain"; hint: "auto, or 0–50 dB"; AField { id: gainField; enabled: !dlg.running; Layout.preferredWidth: 180; placeholderText: "auto" } }
                    FormRow { label: "Frequency correction"; hint: "−150 to 150"; AField { id: ppmField; enabled: !dlg.running; Layout.preferredWidth: 180; suffix: "PPM"; validator: IntValidator { bottom: -150; top: 150 } } }
                }
                RowLayout {
                    spacing: 12
                    FormRow { label: "Aircraft expire after"; AField { id: aircraftTtl; Layout.preferredWidth: 180; suffix: "seconds"; validator: IntValidator { bottom: 10; top: 86400 } } }
                    FormRow { label: "Vessels expire after"; AField { id: vesselTtl; Layout.preferredWidth: 180; suffix: "seconds"; validator: IntValidator { bottom: 10; top: 86400 } } }
                }
                InfoBox {
                    text: "Fixed, tested service rates: ADS-B 1090 MHz at 2.4 MS/s · AIS A+B 161.975 / 162.025 MHz at 1.536 MS/s. Bias tee stays off. Gain and PPM apply to both roles."
                }
            }

            // Map
            SettingsSection {
                FormRow {
                    label: "Map style"
                    ACombo {
                        id: mapBox
                        Layout.preferredWidth: 280
                        model: [{ value: "Follow app", label: "Match app appearance" }, { value: "Dark", label: "Night map" },
                                { value: "Light", label: "Day map" }, { value: "Scope only", label: "Radar scope" }]
                        textRole: "label"
                        valueRole: "value"
                        onActivated: dlg.mapTheme = currentValue
                    }
                }
                ASwitch {
                    Layout.fillWidth: true
                    text: "OpenStreetMap street tiles (uses the internet)"
                    subtitle: "Streets and coastlines under your traffic. The offline grid is used otherwise."
                    checked: dlg.tiles
                    onToggled: dlg.tiles = checked
                }
                InfoBox {
                    text: "When enabled, OpenStreetMap receives your IP address and the map areas you view. Observations, locations and alert rules are never uploaded. Tiles are cached locally (up to 128 MB) and cached areas keep working offline."
                }
                RowLayout {
                    spacing: 12
                    FormRow { label: "Range rings"; hint: "Comma-separated, up to 12"; AField { id: ringsField; Layout.preferredWidth: 240; suffix: dlg.units } }
                    FormRow { label: "Trail length"; hint: "0 keeps the whole session (bounded)"; AField { id: trailField; Layout.preferredWidth: 180; suffix: "minutes"; validator: IntValidator { bottom: 0; top: 1440 } } }
                }
            }

            // Notifications
            SettingsSection {
                ASwitch {
                    Layout.fillWidth: true
                    text: "Quiet hours"
                    subtitle: "Hold back sound, voice, desktop pop-ups and phone pushes overnight. Alerts are still recorded, and alerts set to ignore quiet hours (e.g. emergencies) still notify."
                    checked: dlg.quietEnabled
                    onToggled: dlg.quietEnabled = checked
                }
                RowLayout {
                    spacing: 12
                    enabled: dlg.quietEnabled
                    opacity: enabled ? 1 : 0.5
                    FormRow { label: "From"; AField { id: quietStartField; Layout.preferredWidth: 120; placeholderText: "22:00"; inputMask: "99:99" } }
                    FormRow { label: "Until"; AField { id: quietEndField; Layout.preferredWidth: 120; placeholderText: "07:00"; inputMask: "99:99" } }
                    Text { text: "24-hour clock"; color: Theme.muted; font.pixelSize: 12; Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 10 }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Daily summary"
                    subtitle: "One message a day with what your station received: aircraft and vessels, the farthest contact, alerts and hours monitored."
                    checked: dlg.summaryEnabled
                    onToggled: dlg.summaryEnabled = checked
                }
                RowLayout {
                    spacing: 12
                    enabled: dlg.summaryEnabled
                    opacity: enabled ? 1 : 0.5
                    FormRow { label: "Send at"; AField { id: summaryTimeField; Layout.preferredWidth: 120; placeholderText: "21:00"; inputMask: "99:99" } }
                    ACheck { text: "Also send to my phone"; checked: dlg.summaryPhone; onToggled: dlg.summaryPhone = checked; Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 6 }
                    AButton { text: "Send one now"; compact: true; variant: "ghost"; Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 3; onClicked: app.sendSummaryNow() }
                }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Warn me when reception drops"
                    subtitle: "Compares the message rate with what is normal for the hour at your station. A sudden drop usually means an antenna, cable or USB problem."
                    checked: dlg.healthWarning
                    onToggled: dlg.healthWarning = checked
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Phone notifications with ntfy"
                    subtitle: "Free: install the ntfy app, subscribe to your topic, and AirAlert posts alerts to it."
                    checked: dlg.ntfyEnabled
                    onToggled: dlg.ntfyEnabled = checked
                }
                ColumnLayout {
                    visible: dlg.ntfyEnabled
                    Layout.fillWidth: true
                    spacing: 10
                    RowLayout {
                        spacing: 10
                        FormRow {
                            label: "Topic"
                            hint: "Anyone who knows the topic name can read it \u2014 use a hard-to-guess one."
                            RowLayout {
                                spacing: 8
                                AField { id: ntfyTopicField; Layout.preferredWidth: 260; placeholderText: "airalert-\u2026" }
                                AButton { text: "Generate"; compact: true; variant: "ghost"; onClicked: ntfyTopicField.text = app.generateTopic() }
                            }
                        }
                    }
                    RowLayout {
                        spacing: 10
                        FormRow { label: "Server"; AField { id: ntfyServerField; Layout.preferredWidth: 260; placeholderText: "https://ntfy.sh" } }
                        FormRow { label: "Access token (optional)"; AField { id: ntfyTokenField; Layout.preferredWidth: 220; echoMode: TextInput.Password } }
                    }
                    AButton {
                        text: "Save & send test to ntfy"
                        iconName: "bell"
                        compact: true
                        onClicked: if (dlg.save(true) === "") app.testPhone("ntfy")
                    }
                }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Phone notifications with Pushover"
                    subtitle: "Uses the Pushover app (one-time purchase). Needs your user key and an application API token from pushover.net."
                    checked: dlg.pushoverEnabled
                    onToggled: dlg.pushoverEnabled = checked
                }
                ColumnLayout {
                    visible: dlg.pushoverEnabled
                    Layout.fillWidth: true
                    spacing: 10
                    RowLayout {
                        spacing: 10
                        FormRow { label: "User key"; AField { id: pushoverUserField; Layout.preferredWidth: 260; echoMode: TextInput.Password } }
                        FormRow { label: "Application API token"; AField { id: pushoverTokenField; Layout.preferredWidth: 260; echoMode: TextInput.Password } }
                    }
                    AButton {
                        text: "Save & send test to Pushover"
                        iconName: "bell"
                        compact: true
                        onClicked: if (dlg.save(true) === "") app.testPhone("pushover")
                    }
                }
                InfoBox {
                    tone: Theme.alert
                    iconName: "info"
                    text: "Phone alerts send the alert text (target name, distance, rule) to the service you choose. Nothing else leaves this computer. Turn on \u201cPhone\u201d for each alert that should reach your phone."
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                RowLayout {
                    spacing: 12
                    AButton { text: "Test voice"; iconName: "sound"; compact: true; onClicked: app.testVoice() }
                    Text {
                        Layout.fillWidth: true
                        text: "Spoken alerts use the Windows voice. Turn on \u201cSpeak aloud\u201d for each alert that should talk."
                        color: Theme.muted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // Connections
            SettingsSection {
                ASwitch {
                    Layout.fillWidth: true
                    text: "Live map on your phone or tablet"
                    subtitle: "Shows the live map, traffic list and alerts in the browser of a device on your home network. View only."
                    checked: dlg.phoneMapEnabled
                    onToggled: dlg.phoneMapEnabled = checked
                }
                RowLayout {
                    spacing: 12
                    enabled: dlg.phoneMapEnabled
                    opacity: enabled ? 1 : 0.5
                    FormRow { label: "Port"; AField { id: phoneMapPortField; Layout.preferredWidth: 120; validator: IntValidator { bottom: 1024; top: 65535 } } }
                    AButton {
                        text: "Save & turn " + (dlg.phoneMapEnabled ? "on" : "off")
                        compact: true
                        enabled: true
                        Layout.alignment: Qt.AlignBottom
                        Layout.bottomMargin: 3
                        onClicked: dlg.save(true)
                    }
                }
                Rectangle {
                    objectName: "phoneMapCard"
                    visible: dlg.web !== null && dlg.web.running
                    Layout.fillWidth: true
                    Layout.preferredHeight: pairColumn.implicitHeight + 26
                    radius: 12
                    color: Theme.raised
                    border.color: Theme.border
                    Column {
                        id: pairColumn
                        x: 14
                        y: 13
                        width: parent.width - 28
                        spacing: 6
                        Text { text: "ON YOUR PHONE, ON THE SAME WI-FI"; color: Theme.muted; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.9 }
                        Text { text: "1. Open this address in the browser:"; color: Theme.textDim; font.pixelSize: 13 }
                        TextInput { text: dlg.web ? dlg.web.url : ""; readOnly: true; selectByMouse: true; color: Theme.accent; font.pixelSize: 18; font.weight: Font.DemiBold }
                        Text { text: "2. Enter this pairing code (it changes after each device):"; color: Theme.textDim; font.pixelSize: 13 }
                        Text { text: dlg.web ? dlg.web.code.substr(0, 3) + " " + dlg.web.code.substr(3) : ""; color: Theme.text; font.pixelSize: 26; font.weight: Font.Bold; font.letterSpacing: 3 }
                        Row {
                            spacing: 12
                            Text { text: dlg.web ? dlg.web.devices + (dlg.web.devices === 1 ? " device paired" : " devices paired") : ""; color: Theme.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                            AButton { text: "Forget paired devices"; compact: true; variant: "ghost"; enabled: dlg.web !== null && dlg.web.devices > 0; onClicked: dlg.web.forgetDevices() }
                        }
                    }
                }
                Text {
                    visible: dlg.web !== null && dlg.web.error !== ""
                    Layout.fillWidth: true
                    text: dlg.web ? dlg.web.error : ""
                    color: Theme.danger
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
                InfoBox {
                    text: "The first time, Windows may ask whether to allow AirAlert on your network: allow it for Private networks. Only devices on your home network are answered, each must be paired with the code, and nothing can be changed from the phone. It works while AirAlert is running here, including in the tray."
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Aircraft photos (uses the internet)"
                    subtitle: "Shows a photo of the selected aircraft in the inspector."
                    checked: dlg.lookupPhotos
                    onToggled: dlg.lookupPhotos = checked
                }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Likely routes (uses the internet)"
                    subtitle: "Shows where the selected flight usually flies from and to, looked up by callsign."
                    checked: dlg.lookupRoutes
                    onToggled: dlg.lookupRoutes = checked
                }
                InfoBox {
                    text: "When on, the selected aircraft's ICAO address or callsign is sent to adsbdb.com, and photos are loaded from airport-data.com. Both see your IP address. Nothing about your station, location or other traffic is sent" + (dlg.showSimulation ? ", and simulated traffic is never looked up." : ".")
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Check for updates automatically"
                    subtitle: "Once a day, asks whether a newer AirAlert exists. It never downloads or installs anything by itself."
                    checked: dlg.checkUpdates
                    onToggled: dlg.checkUpdates = checked
                }
                RowLayout {
                    spacing: 12
                    Layout.fillWidth: true
                    AButton { text: "Check now"; compact: true; onClicked: app.checkUpdates(true) }
                    AButton { visible: app.updateInfo.available === true && (app.updateInfo.url || "") !== ""; text: "Open download page"; compact: true; variant: "primary"; onClicked: app.openUpdate() }
                    Text {
                        objectName: "updateStatus"
                        Layout.fillWidth: true
                        text: (app.updateInfo.status || "") !== "" ? app.updateInfo.status
                              : app.updateInfo.configured ? "You have version " + app.updateInfo.current + "."
                              : "Version " + app.updateInfo.current + ". Update checks are not set up for this copy of AirAlert."
                        color: Theme.muted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // Startup
            SettingsSection {
                ASwitch {
                    Layout.fillWidth: true
                    enabled: dlg.trayAvailable
                    text: "Keep running in the tray when the window is closed"
                    subtitle: "Monitoring and alerts continue in the background. Quit from the tray icon."
                    checked: dlg.closeToTray
                    onToggled: dlg.closeToTray = checked
                }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Start AirAlert when I sign in to Windows"
                    subtitle: "Opens quietly in the tray. Adds an AirAlert shortcut to your Windows Startup folder."
                    checked: dlg.startWithWindows
                    onToggled: dlg.startWithWindows = checked
                }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Start monitoring automatically"
                    subtitle: "Begins monitoring in the saved mode as soon as AirAlert opens."
                    checked: dlg.autoStart
                    onToggled: dlg.autoStart = checked
                }
                InfoBox {
                    text: "Together these turn AirAlert into a background alert station: it starts with Windows, listens all day and tells you when something matches your alerts. Only one copy runs at a time \u2014 opening AirAlert again brings the running window forward."
                }
            }

            // Database
            SettingsSection {
                RowLayout {
                    spacing: 12
                    FormRow { label: "Minimum position sampling"; hint: "Stationary targets are saved at most once a minute."; AField { id: sampleField; Layout.preferredWidth: 200; suffix: "seconds"; validator: IntValidator { bottom: 1; top: 36500 } } }
                    FormRow { label: "Automatic retention"; hint: "Runs at startup and daily."; AField { id: retentionField; Layout.preferredWidth: 200; suffix: "days"; validator: IntValidator { bottom: 1; top: 36500 } } }
                }
                FormRow {
                    label: "Data folder"
                    hint: "Settings, history database, logs and the tile cache. Back up the whole folder with AirAlert closed."
                    RowLayout {
                        spacing: 10
                        Text { Layout.fillWidth: true; text: dlg.folder; color: Theme.text; font.pixelSize: 13; elide: Text.ElideMiddle }
                        AButton { text: "Open folder"; iconName: "folder"; compact: true; onClicked: app.openDataFolder() }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                ASwitch {
                    Layout.fillWidth: true
                    text: "Back up automatically"
                    subtitle: "Saves your settings and history as one zip file in a folder you choose, such as a cloud-synced or external drive."
                    checked: dlg.backupEnabled
                    onToggled: dlg.backupEnabled = checked
                }
                FormRow {
                    label: "Backup folder"
                    RowLayout {
                        spacing: 8
                        Layout.fillWidth: true
                        AField { id: backupFolderField; Layout.fillWidth: true; placeholderText: "Choose a folder" }
                        AButton {
                            text: "Browse…"
                            compact: true
                            onClicked: {
                                var chosen = app.chooseFolder(backupFolderField.text)
                                if (chosen !== "") { backupFolderField.text = chosen; dlg.backupStatus = app.lastBackupText(chosen) }
                            }
                        }
                    }
                }
                RowLayout {
                    spacing: 12
                    FormRow { label: "Back up every"; AField { id: backupDaysField; Layout.preferredWidth: 130; suffix: "days"; validator: IntValidator { bottom: 1; top: 365 } } }
                    FormRow { label: "Keep the newest"; AField { id: backupKeepField; Layout.preferredWidth: 150; suffix: "backups"; validator: IntValidator { bottom: 1; top: 50 } } }
                    AButton {
                        text: "Back up now"
                        iconName: "download"
                        compact: true
                        Layout.alignment: Qt.AlignBottom
                        Layout.bottomMargin: 3
                        onClicked: {
                            var result = app.backupNow(backupFolderField.text)
                            dlg.backupStatus = result !== "" ? result : "Backing up…"
                        }
                    }
                }
                Text {
                    objectName: "backupStatus"
                    Layout.fillWidth: true
                    text: dlg.backupStatus + ". A backup holds your settings (including any phone notification keys) and history. To restore, close AirAlert and unzip it into the data folder."
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
                Connections {
                    target: app
                    function onBackupFinished(ok, text, announce) { dlg.backupStatus = ok ? app.lastBackupText(backupFolderField.text) : "The backup failed" }
                }
            }
        }
    }

    footer: [
        Text { Layout.fillWidth: true; text: dlg.error; color: Theme.danger; font.pixelSize: 13; wrapMode: Text.WordWrap },
        AButton { visible: dlg.first && dlg.sectionIndex > 0; text: "Back"; variant: "ghost"; onClicked: dlg.sectionIndex -= 1 },
        AButton { visible: !dlg.first; text: "Cancel"; variant: "ghost"; onClicked: dlg.close() },
        AButton { visible: dlg.first && dlg.sectionIndex < dlg.sections.length - 1; text: "Next"; iconName: "chevronRight"; onClicked: dlg.sectionIndex += 1 },
        AButton { text: dlg.first ? "Save and finish" : "Save settings"; variant: "primary"; iconName: "check"; onClicked: dlg.save() }
    ]
}
