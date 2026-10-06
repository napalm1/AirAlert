import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

GlassPanel {
    id: panel
    property bool collapsed: false
    property alias searchField: search
    readonly property var model: app.trafficModel
    width: collapsed ? 236 : 348
    height: collapsed ? 58 : parent.height
    Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

    readonly property var sorts: [
        { key: "distance", label: "Distance" },
        { key: "altitude", label: "Altitude" },
        { key: "speed", label: "Speed" },
        { key: "bearing", label: "Bearing" },
        { key: "label", label: "Name" },
        { key: "identifier", label: "ID" },
        { key: "kind", label: "Type" },
        { key: "lastSeen", label: "Recent" }
    ]

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        anchors.topMargin: 12
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Column {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    text: "TRAFFIC"
                    color: Theme.muted
                    font.pixelSize: 11
                    font.weight: Font.Bold
                    font.letterSpacing: 1.2
                }
                Text {
                    text: app.visibleCount + (app.visibleCount === 1 ? " target in view list" : " targets in view list")
                    color: Theme.text
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    visible: !panel.collapsed
                }
            }
            Rectangle {
                implicitWidth: aRow.implicitWidth + 16
                implicitHeight: 26
                radius: 13
                color: Theme.accentSoft
                Row {
                    id: aRow
                    anchors.centerIn: parent
                    spacing: 5
                    Icon { name: "plane"; size: 14; color: Theme.accent; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: app.aircraftCount; color: Theme.accent; font.pixelSize: 13; font.weight: Font.Bold; anchors.verticalCenter: parent.verticalCenter }
                }
                ATip { visible: aHover.hovered; text: "Aircraft tracked" }
                HoverHandler { id: aHover }
            }
            Rectangle {
                implicitWidth: vRow.implicitWidth + 16
                implicitHeight: 26
                radius: 13
                color: Util.tint(Theme.vessel, 0.14)
                Row {
                    id: vRow
                    anchors.centerIn: parent
                    spacing: 5
                    Icon { name: "ship"; size: 14; color: Theme.vessel; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: app.vesselCount; color: Theme.vessel; font.pixelSize: 13; font.weight: Font.Bold; anchors.verticalCenter: parent.verticalCenter }
                }
                ATip { visible: vHover.hovered; text: "Vessels tracked" }
                HoverHandler { id: vHover }
            }
            IconButton {
                iconName: panel.collapsed ? "chevronRight" : "chevronLeft"
                tip: panel.collapsed ? "Show traffic list" : "Hide traffic list"
                size: 30
                iconSize: 16
                onClicked: panel.collapsed = !panel.collapsed
            }
        }

        AField {
            id: search
            visible: !panel.collapsed
            Layout.fillWidth: true
            iconName: "search"
            clearable: true
            placeholderText: "Search tail, callsign, ICAO, MMSI or name"
            onTextChanged: app.setSearch(text)
            Keys.onEscapePressed: clear()
        }

        Flow {
            visible: !panel.collapsed
            Layout.fillWidth: true
            spacing: 5
            Repeater {
                model: panel.sorts
                delegate: Rectangle {
                    id: chip
                    required property var modelData
                    readonly property bool current: panel.model !== null && panel.model.sortKey === modelData.key
                    width: chipRow.implicitWidth + 16
                    height: 26
                    radius: 13
                    color: current ? Theme.accentSoft : chipArea.containsMouse ? Theme.hover : "transparent"
                    border.color: current ? Util.tint(Theme.accent, 0.5) : Theme.border
                    Row {
                        id: chipRow
                        anchors.centerIn: parent
                        spacing: 3
                        Text {
                            text: chip.modelData.label
                            color: chip.current ? Theme.accent : Theme.textDim
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Icon {
                            visible: chip.current
                            name: panel.model && panel.model.ascending ? "arrowUp" : "arrowDown"
                            size: 12
                            stroke: 2.4
                            color: Theme.accent
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    MouseArea {
                        id: chipArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: panel.model.sortBy(chip.modelData.key)
                    }
                }
            }
        }

        ListView {
            id: list
            visible: !panel.collapsed
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 2
            model: panel.model
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: TrafficRow {
                width: list.width - 6
            }

            Column {
                visible: list.count === 0
                anchors.centerIn: parent
                width: parent.width - 30
                spacing: 10
                Icon {
                    name: app.running ? "antenna" : "radar"
                    size: 34
                    color: Theme.muted
                    anchors.horizontalCenter: parent.horizontalCenter
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: search.text !== "" ? "No live targets match “" + search.text + "”"
                        : app.running ? "Listening… no targets decoded yet" : "Start monitoring to see live traffic"
                    color: Theme.text
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: search.text !== "" ? "Search matches ICAO, MMSI, callsign, registration and vessel name."
                        : app.running ? "Reception depends on antenna, gain and local traffic." + (app.simulationEnabled ? " Simulation mode shows sample targets." : "")
                        : "Drag the map to explore, scroll to zoom, click a marker to inspect it."
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    lineHeight: 1.2
                }
            }
        }
    }
}
