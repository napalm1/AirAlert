import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

GlassPanel {
    id: panel
    readonly property var d: app.details
    readonly property bool active: d.mode === "target" || d.mode === "history"
    readonly property bool isHistory: d.mode === "history"
    readonly property color tone: d.kind === "vessel" ? Theme.vessel : Theme.accent
    // Optional online lookup (photo, likely route) for the selected aircraft; empty unless switched on in Settings.
    readonly property var look: typeof lookup !== "undefined" && lookup && lookup.info.key !== undefined
                                && lookup.info.key === d.key ? lookup.info : ({})
    width: 348

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        anchors.topMargin: 14
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Rectangle {
                Layout.preferredWidth: 46
                Layout.preferredHeight: 46
                radius: 13
                color: Util.tint(panel.tone, 0.16)
                Icon {
                    anchors.centerIn: parent
                    name: panel.d.kind === "vessel" ? "ship" : "plane"
                    size: 24
                    color: panel.tone
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: panel.isHistory ? "HISTORICAL TRACK" : (panel.d.kind === "vessel" ? "VESSEL" : "AIRCRAFT")
                    color: panel.isHistory ? Theme.alert : Theme.muted
                    font.pixelSize: 11
                    font.weight: Font.Bold
                    font.letterSpacing: 1.2
                }
                Text {
                    text: panel.d.title || ""
                    color: Theme.text
                    font.pixelSize: 20
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Text {
                    text: panel.d.subtitle || ""
                    visible: text !== ""
                    color: Theme.textDim
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
            IconButton {
                iconName: "close"
                tip: "Close inspector"
                Layout.alignment: Qt.AlignTop
                onClicked: app.clearSelection()
            }
        }

        Rectangle {
            objectName: "aircraftPhoto"
            visible: (panel.look.photo || "") !== ""
            Layout.fillWidth: true
            Layout.preferredHeight: 124
            radius: 11
            color: Theme.raised
            clip: true
            Image {
                anchors.fill: parent
                source: panel.look.photo || ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: false
            }
            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 6
                width: creditText.implicitWidth + 10
                height: 18
                radius: 5
                color: Qt.rgba(0, 0, 0, 0.6)
                Text { id: creditText; anchors.centerIn: parent; text: panel.look.photoCredit || ""; color: "#ffffff"; font.pixelSize: 10 }
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            Chip {
                visible: (panel.d.emergency || "") !== ""
                text: "SQUAWK " + (panel.d.squawk || "") + " · " + (panel.d.emergency || "").toUpperCase()
                tone: Theme.danger
                iconName: "warning"
            }
            Chip {
                visible: (panel.d.phase || "") !== ""
                text: panel.d.phase || ""
                tone: Theme.accent
                iconName: "route"
            }
            Chip {
                visible: panel.d.military === true
                text: "Military"
                tone: Theme.alert
                iconName: "warning"
            }
            Chip {
                objectName: "routeChip"
                visible: (panel.look.route || "") !== ""
                text: (panel.look.route || "") + ((panel.look.routeDetail || "") !== "" ? " · " + panel.look.routeDetail : "")
                tone: Theme.accent
                iconName: "globe"
                ATip { visible: routeHover.hovered; text: "Likely route for this callsign, from adsbdb.com. It can be out of date." }
                HoverHandler { id: routeHover }
            }
            Chip {
                text: panel.d.source || ""
                tone: panel.d.source === "Simulation" ? Theme.sim : Theme.success
                iconName: panel.d.source === "Simulation" ? "pulse" : "antenna"
            }
            Chip {
                visible: panel.d.stale === true && !panel.isHistory
                text: "Position older than 60 s"
                tone: Theme.stale
                iconName: "clock"
            }
            Chip {
                visible: panel.d.watched === true
                text: "On watchlist"
                tone: Theme.alert
                iconName: "star"
            }
            Chip {
                visible: panel.isHistory
                text: (panel.d.trackPoints || 0) + " recorded positions"
                tone: Theme.alert
                iconName: "route"
            }
            Chip {
                visible: !panel.isHistory && (panel.d.lastSeen || "") !== ""
                text: "Last heard " + (panel.d.lastSeen || "")
                tone: Theme.muted
                iconName: "signal"
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            rowSpacing: 8
            columnSpacing: 8
            Repeater {
                model: panel.d.metrics || []
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 62
                    radius: 11
                    color: Theme.raised
                    border.color: Theme.border
                    Column {
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3
                        Text {
                            text: modelData.label.toUpperCase()
                            color: Theme.muted
                            font.pixelSize: 10
                            font.weight: Font.Bold
                            font.letterSpacing: 0.9
                        }
                        Row {
                            spacing: 4
                            Text {
                                id: metricValue
                                text: modelData.value
                                color: Theme.text
                                font.pixelSize: 19
                                font.weight: Font.DemiBold
                                font.features: { "tnum": 1 }
                            }
                            Text {
                                text: modelData.unit
                                color: Theme.muted
                                font.pixelSize: 12
                                anchors.baseline: metricValue.baseline
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AButton {
                Layout.fillWidth: true
                text: panel.d.watched ? "Watching" : "Watch"
                iconName: panel.d.watched ? "star" : "starLine"
                enabled: !panel.d.watched
                compact: true
                onClicked: app.watchSelected()
            }
            AButton {
                Layout.fillWidth: true
                text: "Create alert"
                iconName: "bell"
                compact: true
                onClicked: app.alertForSelected()
            }
            IconButton {
                visible: !panel.isHistory
                iconName: "locate"
                tip: "Center map on this target"
                size: 30
                iconSize: 17
                onClicked: app.focusTarget(panel.d.key)
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: Theme.border
        }

        Text {
            text: "ALL FIELDS"
            color: Theme.muted
            font.pixelSize: 11
            font.weight: Font.Bold
            font.letterSpacing: 1.2
        }

        ListView {
            id: fields
            objectName: "inspectorFields"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            // A list model updated in place: the once-a-second refresh changes values without scrolling back up.
            model: app.detailFields
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Item {
                id: fieldRow
                required property string label
                required property var value
                required property int index
                width: fields.width - 8
                height: 30
                Rectangle {
                    anchors.fill: parent
                    radius: 6
                    color: index % 2 === 0 ? Util.tint(Theme.raised, Theme.dark ? 0.55 : 0.8) : "transparent"
                }
                Text {
                    text: fieldRow.label
                    color: Theme.muted
                    font.pixelSize: 12
                    anchors.left: parent.left
                    anchors.leftMargin: 9
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: String(fieldRow.value)
                    color: text === "—" ? Theme.muted : Theme.text
                    font.pixelSize: 13
                    font.weight: Font.Medium
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideLeft
                    width: parent.width * 0.58
                    anchors.right: parent.right
                    anchors.rightMargin: 9
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: "— means the field has not been received. Range is surface great-circle distance from your station."
            color: Theme.muted
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }
}
