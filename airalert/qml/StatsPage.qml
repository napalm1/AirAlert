import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

Item {
    id: page
    property var stats: ({ rows: [], hours: 0, sessions: 0, live: {}, rules: 0, watch: 0, zones: 0 })
    readonly property var charts: insights.stats
    readonly property var live: insights.live
    readonly property string units: charts.units || app.units
    readonly property string scopeText: (charts.rangeLabel || "Last 24 hours").toLowerCase()
    readonly property string sourceText: insights.includeSimulation ? "received RF and simulation" : app.simulationEnabled ? "received RF only" : "received RF"
    // Simulation is only mentioned once it has been switched on in Settings > General.
    readonly property string simHint: app.simulationEnabled && !insights.includeSimulation ? " Simulated traffic appears when Include simulation is on." : ""

    function reload() { stats = app.statistics() }
    function rowsFor(source) { return (stats.rows || []).filter(function(r) { return r.source === source }) }
    function pad2(n) { return n < 10 ? "0" + n : String(n) }
    function num(v, digits) { return v === undefined || v === null ? "—" : Util.number(v, digits) }

    onVisibleChanged: insights.setActive(visible)
    Component.onCompleted: if (visible) insights.setActive(true)

    Timer {
        interval: 4000
        repeat: true
        running: page.visible
        triggeredOnStart: true
        onTriggered: page.reload()
    }
    // Heavy chart statistics refresh on open, on filter changes and every 45 s while shown.
    Timer {
        interval: 45000
        repeat: true
        running: page.visible
        onTriggered: insights.reload()
    }

    ChartStyle { id: cs }

    Flickable {
        id: flick
        objectName: "statsFlick"
        anchors.fill: parent
        contentHeight: content.implicitHeight + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: content
            x: 24
            y: 24
            width: parent.width - 48
            spacing: 18

            PageHeader {
                Layout.fillWidth: true
                title: "Statistics"
                subtitle: "Your station at a glance." + (app.simulationEnabled ? " Simulation and received RF observations are always counted separately." : "")
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 4
                rowSpacing: 12
                columnSpacing: 12
                Repeater {
                    model: [
                        { label: "Aircraft now", value: Util.number(page.stats.live.aircraft), icon: "plane", tone: Theme.accent },
                        { label: "Vessels now", value: Util.number(page.stats.live.vessels), icon: "ship", tone: Theme.vessel },
                        { label: "Messages / second", value: Util.number(page.stats.live.rate), icon: "pulse", tone: Theme.success },
                        { label: "Hours monitored", value: Util.number(page.stats.hours, 2), icon: "clock", tone: Theme.sim },
                        { label: "Sessions", value: Util.number(page.stats.sessions), icon: "antenna", tone: Theme.muted },
                        { label: "Active alerts", value: Util.number(page.stats.rules), icon: "bell", tone: Theme.alert },
                        { label: "Watchlist entries", value: Util.number(page.stats.watch), icon: "star", tone: Theme.alert },
                        { label: "Geofences", value: Util.number(page.stats.zones), icon: "polygon", tone: Theme.dark ? "#f472b6" : "#db2777" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 96
                        radius: Theme.radius
                        color: Theme.panel
                        border.color: Theme.border
                        Rectangle {
                            width: 40
                            height: 40
                            radius: 12
                            x: 16
                            anchors.verticalCenter: parent.verticalCenter
                            color: Util.tint(modelData.tone, 0.14)
                            Icon { anchors.centerIn: parent; name: modelData.icon; size: 20; color: modelData.tone }
                        }
                        Column {
                            x: 70
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text { text: modelData.value; color: Theme.text; font.pixelSize: 26; font.weight: Font.Bold }
                            Text { text: modelData.label; color: Theme.muted; font.pixelSize: 12 }
                        }
                    }
                }
            }

            // ------------------------------------------------------------ activity
            Text {
                text: "ACTIVITY AND COVERAGE"
                color: Theme.muted
                font.pixelSize: 11
                font.weight: Font.Bold
                font.letterSpacing: 1.2
                Layout.topMargin: 6
            }

            // One filter row scopes every chart below it (the daily chart always shows 14 days).
            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                Segmented {
                    objectName: "statsRange"
                    options: [{ value: "24h", label: "24 hours" }, { value: "7d", label: "7 days" }, { value: "30d", label: "30 days" }]
                    value: insights.timeRange
                    onPicked: (value) => insights.setTimeRange(value)
                }
                ASwitch {
                    objectName: "statsSimulation"
                    visible: app.simulationEnabled
                    Layout.preferredWidth: 180
                    text: "Include simulation"
                    checked: insights.includeSimulation
                    onToggled: insights.setIncludeSimulation(checked)
                }
                Item { Layout.fillWidth: true }
                Text {
                    text: page.charts.ready ? "Showing " + page.scopeText + " · " + page.sourceText + " · updated " + page.charts.updated : ""
                    color: Theme.muted
                    font.pixelSize: 12
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ChartCard {
                    id: rateCard
                    objectName: "rateCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 292
                    title: "Message rate"
                    subtitle: "Messages per second while monitoring, averaged per " + (page.charts.rate.bucketMinutes >= 60 ? page.charts.rate.bucketMinutes / 60 + " h" : page.charts.rate.bucketMinutes + " min") + " · " + page.scopeText
                    empty: !page.charts.rate.hasData
                    emptyIcon: "pulse"
                    emptyTitle: "No message history for this period"
                    emptyText: "AirAlert records the message rate once a minute while monitoring." + page.simHint
                    footnote: page.charts.rate.hasData ? "Average " + page.num(page.charts.rate.average, 1) + " msg/s · busiest minute " + page.num(page.charts.rate.peak, 1) + " msg/s · " + page.num(page.charts.rate.hours, 1) + " h monitored" : ""
                    tableHeader: ["Period starting", "Messages / s"]
                    tableRows: {
                        var pts = page.charts.rate.points || []
                        var out = []
                        for (var i = pts.length - 1; i >= 0; i--)
                            if (pts[i].v !== null && pts[i].v !== undefined)
                                out.push([Qt.formatDateTime(new Date(pts[i].t * 1000), "ddd MMM d, HH:mm"), Util.number(pts[i].v, 1)])
                        return out
                    }
                    ChartLine {
                        anchors.fill: parent
                        points: page.charts.rate.points || []
                        start: page.charts.start || 0
                        end: page.charts.end || 1
                        bucket: (page.charts.rate.bucketMinutes || 10) * 60
                        color: cs.aircraft
                        tickMode: page.charts.range === "30d" ? "weeks" : page.charts.range === "7d" ? "days" : "hours"
                    }
                }

                ChartCard {
                    objectName: "liveCard"
                    Layout.preferredWidth: 340
                    Layout.preferredHeight: 292
                    title: "Live message rate"
                    subtitle: "Last 10 minutes · updates every second"
                    empty: !page.live.running && !(page.live.peak > 0)
                    emptyIcon: "antenna"
                    emptyTitle: "Monitoring is stopped"
                    emptyText: "Start monitoring to see messages per second as they arrive."
                    footnote: page.live.peak !== null && page.live.peak !== undefined ? "10-minute average " + page.num(page.live.average, 1) + " · peak " + page.num(page.live.peak, 1) + " msg/s" : ""
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 6
                        RowLayout {
                            spacing: 8
                            Text {
                                text: Util.number(page.live.current)
                                color: Theme.text
                                font.pixelSize: 34
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: "msg/s"
                                color: Theme.muted
                                font.pixelSize: 13
                                Layout.alignment: Qt.AlignBaseline
                            }
                            Item { Layout.fillWidth: true }
                            Chip {
                                text: !page.live.running ? "Stopped" : page.live.simulated ? "Simulation" : "Live RF"
                                tone: !page.live.running ? Theme.muted : page.live.simulated ? Theme.sim : Theme.success
                            }
                        }
                        ChartLine {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            compact: true
                            color: cs.aircraft
                            bucket: page.live.bin || 5
                            start: page.live.start || 0
                            end: page.live.end || 1
                            points: {
                                var values = page.live.points || []
                                var out = []
                                for (var i = 0; i < values.length; i++)
                                    out.push({ t: page.live.start + i * page.live.bin, v: values[i] })
                                return out
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ChartCard {
                    objectName: "hoursCard"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 320
                    title: "Aircraft by hour of day"
                    subtitle: "Unique aircraft seen in each local hour · " + page.scopeText
                    empty: !page.charts.hours.hasData
                    emptyIcon: "clock"
                    emptyTitle: "No aircraft positions in this period"
                    emptyText: "Aircraft appear here once positions are decoded. Traffic near you can be light at night." + page.simHint
                    footnote: page.charts.hours.peakHour >= 0 ? "Busiest hour " + page.pad2(page.charts.hours.peakHour) + ":00–" + page.pad2((page.charts.hours.peakHour + 1) % 24) + ":00" : ""
                    tableHeader: ["Hour", "Aircraft"]
                    tableRows: {
                        var v = page.charts.hours.values || []
                        var out = []
                        for (var h = 0; h < v.length; h++)
                            out.push([page.pad2(h) + ":00–" + page.pad2((h + 1) % 24) + ":00", Util.number(v[h])])
                        return out
                    }
                    ChartBars {
                        anchors.fill: parent
                        categories: { var c = []; for (var h = 0; h < 24; h++) c.push(page.pad2(h)); return c }
                        tipTitles: { var c = []; for (var h = 0; h < 24; h++) c.push(page.pad2(h) + ":00–" + page.pad2((h + 1) % 24) + ":00"); return c }
                        series: [{ name: "Aircraft", color: cs.aircraft, values: page.charts.hours.values || [] }]
                        labelEvery: 3
                        unitLabel: "aircraft"
                    }
                }

                ChartCard {
                    objectName: "dailyCard"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 320
                    title: "Unique targets per day"
                    subtitle: "Aircraft and vessels with a position · last 14 days"
                    empty: !page.charts.daily.hasData
                    emptyIcon: "chart"
                    emptyTitle: "No traffic in the last 14 days"
                    emptyText: "Start monitoring to build daily totals." + page.simHint
                    footnote: page.charts.daily.busiest ? "Busiest day " + page.charts.daily.busiest + " · each panel has its own scale" : ""
                    tableHeader: ["Day", "Aircraft", "Vessels"]
                    tableRows: {
                        var rows = page.charts.daily.rows || []
                        var out = []
                        for (var i = rows.length - 1; i >= 0; i--)
                            out.push([rows[i].label + " " + rows[i].short, Util.number(rows[i].aircraft), Util.number(rows[i].vessels)])
                        return out
                    }
                    // Small multiples: aircraft and vessel counts differ by orders of magnitude, so each has its own axis.
                    Column {
                        id: dailyPanels
                        readonly property var rows: page.charts.daily.rows || []
                        readonly property real panelSpace: height - aircraftKey.height - vesselKey.height - 10
                        anchors.fill: parent
                        spacing: 2
                        ChartLegendItem { id: aircraftKey; color: cs.aircraft; text: "Aircraft" }
                        ChartBars {
                            width: parent.width
                            height: dailyPanels.panelSpace * 0.52
                            categories: dailyPanels.rows.map(function(r) { return r.today ? "Today" : r.short })
                            tipTitles: dailyPanels.rows.map(function(r) { return r.label + " " + r.short })
                            series: [{ name: "Aircraft", color: cs.aircraft, values: dailyPanels.rows.map(function(r) { return r.aircraft }) }]
                            unitLabel: "aircraft"
                            ticks: 2
                            showCategories: false
                        }
                        Item { width: 1; height: 6 }
                        ChartLegendItem { id: vesselKey; color: cs.vessel; text: "Vessels" }
                        ChartBars {
                            width: parent.width
                            height: dailyPanels.panelSpace * 0.48
                            categories: dailyPanels.rows.map(function(r) { return r.today ? "Today" : r.short })
                            tipTitles: dailyPanels.rows.map(function(r) { return r.label + " " + r.short })
                            series: [{ name: "Vessels", color: cs.vessel, values: dailyPanels.rows.map(function(r) { return r.vessels }) }]
                            unitLabel: "vessels"
                            ticks: 2
                            labelEvery: 2
                            labelFromEnd: true
                            emphasis: dailyPanels.rows.length - 1
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ChartCard {
                    objectName: "coverageCard"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 392
                    title: "Receiver coverage"
                    subtitle: "Farthest position per 10° sector · " + page.scopeText
                    empty: !page.charts.coverage.hasData
                    emptyIcon: page.charts.coverage.homeSet ? "radar" : "home"
                    emptyTitle: page.charts.coverage.homeSet ? "No positions received in this period" : "Set your home location"
                    emptyText: page.charts.coverage.homeSet
                               ? "Coverage grows as your receiver decodes positions in each direction. Real RF can be quiet, especially at night." + page.simHint
                               : "Coverage is measured from your station. Add it in Settings → Location."
                    footnote: page.charts.coverage.hasData ? page.charts.coverage.sectors + " of 36 sectors heard · surface distance from home" : ""
                    tableHeader: ["Bearing", "Aircraft (" + page.units + ")", "Vessels (" + page.units + ")"]
                    tableRows: {
                        var a = page.charts.coverage.aircraft || [], v = page.charts.coverage.vessel || []
                        var out = []
                        for (var i = 0; i < a.length; i++)
                            out.push([Util.pad3(i * 10) + "°–" + Util.pad3(i * 10 + 10) + "°", a[i] === null ? "—" : Util.number(a[i], 1), v[i] === null ? "—" : Util.number(v[i], 1)])
                        return out
                    }
                    ChartPolar {
                        anchors.fill: parent
                        units: page.units
                        series: {
                            var out = []
                            if (page.charts.coverage.hasAircraft)
                                out.push({ name: "Aircraft", color: cs.aircraft, values: page.charts.coverage.aircraft })
                            if (page.charts.coverage.hasVessel)
                                out.push({ name: "Vessels", color: cs.vessel, values: page.charts.coverage.vessel })
                            return out
                        }
                    }
                    // Direct labels: series key with its farthest range.
                    Column {
                        spacing: 4
                        ChartLegendItem {
                            visible: page.charts.coverage.hasAircraft === true
                            line: true
                            color: cs.aircraft
                            text: "Aircraft · " + page.num(page.charts.coverage.maxAircraft, 0) + " " + page.units + " max"
                        }
                        ChartLegendItem {
                            visible: page.charts.coverage.hasVessel === true
                            line: true
                            color: cs.vessel
                            text: "Vessels · " + page.num(page.charts.coverage.maxVessel, 0) + " " + page.units + " max"
                        }
                    }
                }

                ChartCard {
                    objectName: "typesCard"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 392
                    title: "Top aircraft types"
                    subtitle: "Unique aircraft by type code · " + page.scopeText
                    empty: !page.charts.types.hasData
                    emptyIcon: "plane"
                    emptyTitle: "No aircraft types yet"
                    emptyText: "Types come from the aircraft database for aircraft your receiver hears. You can update it on the Watchlist page."
                    footnote: page.charts.types.hasData ? Util.number(page.charts.types.withType) + " shown of " + Util.number(page.charts.types.aircraft) + " aircraft in this period" : ""
                    tableHeader: ["Type", "Model", "Aircraft"]
                    tableTextColumns: 2
                    tableRows: (page.charts.types.rows || []).map(function(r) { return [r.label, r.detail || "—", Util.number(r.value)] })
                    ChartHBars {
                        anchors.fill: parent
                        rows: page.charts.types.rows || []
                        color: cs.aircraft
                        total: page.charts.types.aircraft || 0
                        shareLabel: "of aircraft in this period"
                    }
                }

                ChartCard {
                    objectName: "operatorsCard"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 392
                    title: "Top operators"
                    subtitle: "Airline callsign prefixes such as UAL123 · " + page.scopeText
                    empty: !page.charts.operators.hasData
                    emptyIcon: "route"
                    emptyTitle: "No airline callsigns yet"
                    emptyText: "Operators are read from airline-style callsigns (three letters and a flight number). Private and military flights often use other formats."
                    tableHeader: ["Code", "Airline", "Aircraft"]
                    tableTextColumns: 2
                    tableRows: (page.charts.operators.rows || []).map(function(r) { return [r.label, r.detail || "—", Util.number(r.value)] })
                    ChartHBars {
                        anchors.fill: parent
                        rows: page.charts.operators.rows || []
                        color: cs.aircraft
                        total: page.charts.operators.aircraft || 0
                        shareLabel: "of aircraft in this period"
                    }
                }
            }

            // ------------------------------------------------------------ records
            Text {
                text: "STATION RECORDS"
                color: Theme.muted
                font.pixelSize: 11
                font.weight: Font.Bold
                font.letterSpacing: 1.2
                Layout.topMargin: 6
            }
            GridLayout {
                objectName: "recordsGrid"
                Layout.fillWidth: true
                visible: insights.records.length > 0
                columns: 4
                rowSpacing: 12
                columnSpacing: 12
                Repeater {
                    model: insights.records
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        Layout.preferredHeight: 92
                        radius: Theme.radius
                        color: Theme.panel
                        border.color: Theme.border
                        Rectangle {
                            id: recordIcon
                            width: 36
                            height: 36
                            radius: 11
                            x: 14
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.alertSoft
                            Icon { anchors.centerIn: parent; name: modelData.icon; size: 18; color: Theme.alert }
                        }
                        Column {
                            anchors.left: recordIcon.right
                            anchors.leftMargin: 12
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 1
                            Text { text: modelData.label.toUpperCase(); color: Theme.muted; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.9 }
                            Text { text: modelData.value; color: Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            Text { width: parent.width; text: modelData.detail; color: Theme.textDim; font.pixelSize: 12; elide: Text.ElideRight }
                        }
                    }
                }
            }
            Text {
                visible: insights.records.length === 0
                Layout.fillWidth: true
                text: "Records such as your farthest contact and busiest hour appear here once positions have been received." + page.simHint
                color: Theme.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }

            // ------------------------------------------------------------ archive
            Text {
                text: "OBSERVATION ARCHIVE"
                color: Theme.muted
                font.pixelSize: 11
                font.weight: Font.Bold
                font.letterSpacing: 1.2
                Layout.topMargin: 6
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Repeater {
                    model: app.simulationEnabled ? ["Local RF", "Simulation"] : ["Local RF"]
                    delegate: Rectangle {
                        id: sourceCard
                        required property string modelData
                        readonly property color tone: modelData === "Simulation" ? Theme.sim : Theme.success
                        Layout.fillWidth: true
                        Layout.preferredHeight: sourceColumn.implicitHeight + 32
                        radius: Theme.radius
                        color: Theme.panel
                        border.color: Theme.border
                        ColumnLayout {
                            id: sourceColumn
                            x: 16
                            y: 16
                            width: parent.width - 32
                            spacing: 12
                            RowLayout {
                                spacing: 10
                                Icon { name: sourceCard.modelData === "Simulation" ? "pulse" : "antenna"; size: 18; color: sourceCard.tone }
                                Text { text: sourceCard.modelData; color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
                                Chip {
                                    text: sourceCard.modelData === "Simulation" ? "Synthetic traffic" : "Decoded from your receiver"
                                    tone: sourceCard.tone
                                }
                            }
                            Repeater {
                                model: page.rowsFor(sourceCard.modelData)
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 74
                                    radius: 11
                                    color: Theme.raised
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 14
                                        spacing: 14
                                        Icon { name: modelData.kind === "vessel" ? "ship" : "plane"; size: 20; color: modelData.kind === "vessel" ? Theme.vessel : Theme.accent }
                                        Column {
                                            Layout.preferredWidth: 110
                                            Text { text: Util.number(modelData.unique); color: Theme.text; font.pixelSize: 22; font.weight: Font.Bold }
                                            Text { text: "unique " + (modelData.kind === "vessel" ? "vessels" : "aircraft"); color: Theme.muted; font.pixelSize: 12 }
                                        }
                                        Column {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            Text { text: "Maximum range  " + modelData.maxDistance; color: Theme.textDim; font.pixelSize: 12 }
                                            Text { text: "Last observation  " + modelData.last; color: Theme.textDim; font.pixelSize: 12 }
                                            Text { text: "Alerts fired  " + Util.number(modelData.alerts); color: Theme.textDim; font.pixelSize: 12 }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: "Distances are surface great-circle distances from the home location, not slant range. Monitoring time adds up every Start–Stop session. Charts use hourly summaries of your local history and follow the retention period in Settings."
                color: Theme.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }
    }
}
