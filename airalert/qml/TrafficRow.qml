import QtQuick
import "Util.js" as Util

// One live target in the traffic list. Click selects (view unchanged); double-click centers.
Rectangle {
    id: row
    required property string key
    required property string label
    required property string kind
    required property string secondary
    required property string detail
    required property string lastSeenText
    required property var altitude
    required property var speed
    required property var distance
    required property var bearing
    required property var heading
    required property bool stale
    required property bool watched
    required property bool simulated
    required property bool hasPosition
    required property string squawk
    required property string emergency
    required property string phase
    readonly property bool selected: app.selectedKey === key
    readonly property bool urgent: emergency !== ""
    readonly property color tone: urgent ? Theme.danger
                                         : stale ? Theme.stale
                                         : kind === "vessel" ? Theme.vessel
                                         : Util.altitudeColor(altitude, app.panelAltitudeStops, Theme.accent)
    height: 56
    radius: 10
    color: urgent ? Theme.dangerSoft : selected ? Theme.accentSoft : area.containsMouse ? Theme.hover : "transparent"
    border.color: urgent ? Util.tint(Theme.danger, 0.7) : selected ? Util.tint(Theme.accent, 0.55) : "transparent"
    border.width: urgent ? 1.5 : 1

    Rectangle {
        visible: row.urgent
        z: 2
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.top: parent.top
        anchors.topMargin: -7
        width: urgentText.implicitWidth + 12
        height: 16
        radius: 5
        color: Theme.danger
        Text {
            id: urgentText
            anchors.centerIn: parent
            text: "SQUAWK " + row.squawk + " · " + row.emergency.toUpperCase()
            color: "#ffffff"
            font.pixelSize: 10
            font.weight: Font.Bold
            font.letterSpacing: 0.5
        }
    }

    Rectangle {
        id: badge
        width: 36
        height: 36
        radius: 10
        x: 8
        anchors.verticalCenter: parent.verticalCenter
        color: Util.tint(row.tone, 0.16)
        Icon {
            anchors.centerIn: parent
            name: row.kind === "vessel" ? "ship" : "plane"
            size: 20
            color: row.tone
            rotation: row.kind === "aircraft" && row.heading !== null && row.heading !== undefined ? row.heading : 0
        }
    }

    Column {
        anchors.left: badge.right
        anchors.leftMargin: 11
        anchors.right: metrics.left
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Row {
            spacing: 6
            width: parent.width
            Text {
                text: row.label
                color: Theme.text
                font.pixelSize: 14
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                width: Math.min(implicitWidth, parent.width - (row.watched ? 20 : 0) - (row.simulated ? 38 : 0))
            }
            Icon {
                visible: row.watched
                name: "star"
                size: 13
                color: Theme.alert
                anchors.verticalCenter: parent.verticalCenter
            }
            Rectangle {
                visible: row.simulated
                width: 32
                height: 16
                radius: 5
                color: Theme.simSoft
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    anchors.centerIn: parent
                    text: "SIM"
                    color: Theme.sim
                    font.pixelSize: 10
                    font.weight: Font.Bold
                    font.letterSpacing: 0.6
                }
            }
        }
        Text {
            text: row.detail + (row.phase !== "" ? " · " + row.phase : "") + (row.stale ? (row.hasPosition ? " · stale position" : " · no position") : "")
            color: Theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
            width: parent.width
        }
    }

    Column {
        id: metrics
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text {
            anchors.right: parent.right
            text: row.kind === "aircraft"
                  ? (row.altitude !== null && row.altitude !== undefined ? Util.number(row.altitude) + " ft" : "— ft")
                  : (row.speed !== null && row.speed !== undefined ? Util.number(row.speed, 1) + " kn" : "— kn")
            color: row.kind === "aircraft" ? row.tone : Theme.text
            font.pixelSize: 13
            font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
        }
        Text {
            anchors.right: parent.right
            text: (row.kind === "aircraft" && row.speed !== null && row.speed !== undefined
                   ? Util.number(row.speed) + " kn · " : "")
                  + (row.distance !== null && row.distance !== undefined
                     ? Util.number(row.distance, 1) + " " + app.units + " · " + Util.pad3(row.bearing) + "°"
                     : "no range")
            color: Theme.muted
            font.pixelSize: 12
            font.features: { "tnum": 1 }
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: app.selectTarget(row.key)
        onDoubleClicked: app.focusTarget(row.key)
    }
    ATip {
        visible: area.containsMouse
        delay: 900
        text: "Last heard " + row.lastSeenText + " · double-click to center"
    }
}
