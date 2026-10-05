import QtQuick
import AirAlert.Map 1.0
import "Util.js" as Util

// Full-bleed map with floating traffic list, inspector, tools and alert ribbon.
Item {
    id: live
    property alias map: map
    signal fullscreenRequested()
    signal showAlerts()
    readonly property real margin: 14

    function focusSearch() {
        traffic.collapsed = false
        traffic.searchField.forceActiveFocus()
    }

    // Esc in the search box clears it first. Returns true when it did.
    function clearSearch() {
        if (!traffic.searchField.activeFocus || traffic.searchField.text === "")
            return false
        traffic.searchField.clear()
        return true
    }

    MapCanvas {
        id: map
        anchors.fill: parent
        leftInset: traffic.collapsed ? 0 : traffic.x + traffic.width
        rightInset: Math.max(0, live.width - toolbar.x + 4)
        bottomInset: playbackBar.visible ? live.height - playbackBar.y : ribbon.visible ? live.height - ribbon.y : 0
        Component.onCompleted: Qt.callLater(function() { app.attachMap(map) })
    }

    // Replayed history is framed in amber so it is never mistaken for live traffic.
    Rectangle {
        anchors.fill: map
        visible: playback.active
        color: "transparent"
        border.color: Util.tint(Theme.alert, 0.75)
        border.width: 2
        z: 1
    }

    SelectionPulse {
        mapItem: map
        tone: map.styleName === "Scope" ? "#9ff0bb" : Theme.accent
    }

    // Aircraft squawking 7500 / 7600 / 7700 pulse red (a fixed pool, so animations never restart).
    Repeater {
        model: 4
        delegate: Item {
            id: alarm
            objectName: "emergencyPulse"
            required property int index
            readonly property var point: index < map.emergencyPoints.length ? map.emergencyPoints[index] : null
            visible: point !== null
            x: point ? point.x : 0
            y: point ? point.y : 0
            z: 1
            Repeater {
                model: 2
                delegate: Rectangle {
                    id: ring
                    required property int index
                    width: 40
                    height: 40
                    radius: 20
                    x: -20
                    y: -20
                    color: "transparent"
                    border.color: Theme.danger
                    border.width: 2.5
                    opacity: 0
                    SequentialAnimation {
                        running: alarm.visible
                        loops: Animation.Infinite
                        PauseAnimation { duration: ring.index * 600 }
                        ParallelAnimation {
                            NumberAnimation { target: ring; property: "scale"; from: 0.75; to: 2.2; duration: 1200; easing.type: Easing.OutCubic }
                            NumberAnimation { target: ring; property: "opacity"; from: 0.95; to: 0; duration: 1200; easing.type: Easing.OutQuad }
                        }
                        PauseAnimation { duration: (1 - ring.index) * 600 }
                    }
                }
            }
        }
    }

    HoverCard {
        mapItem: map
        z: 5
    }

    AirportCard {
        mapItem: map
        z: 5
    }

    TrafficPanel {
        id: traffic
        x: live.margin
        y: live.margin
        height: collapsed ? 58 : live.height - live.margin * 2
        z: 2
    }

    InspectorPanel {
        id: inspector
        y: live.margin
        height: live.height - live.margin * 2
        x: active ? live.width - width - live.margin : live.width + 24
        visible: x < live.width
        z: 2
        Behavior on x { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }

    MapToolbar {
        id: toolbar
        mapItem: map
        y: live.margin
        x: (inspector.active ? inspector.x : live.width) - width - 12
        z: 3
        Behavior on x { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        onFullscreenRequested: live.fullscreenRequested()
    }

    ScopeReadout {
        mapItem: map
        visible: map.styleName === "Scope"
        x: map.leftInset + 12
        y: live.margin
        z: 2
    }

    // Map view readout: center coordinates, zoom and tile status.
    GlassPanel {
        visible: map.styleName !== "Scope"
        blockInput: false
        shadow: false
        width: readoutText.implicitWidth + 20
        height: 24
        radius: 8
        x: live.width - map.rightInset - width - 12
        y: live.height - map.bottomInset - height - (app.tilesEnabled ? 30 : 10)
        z: 2
        Text {
            id: readoutText
            anchors.centerIn: parent
            text: Math.abs(map.centerLat).toFixed(3) + "°" + (map.centerLat >= 0 ? "N" : "S") + "  "
                  + Math.abs(map.centerLon).toFixed(3) + "°" + (map.centerLon >= 0 ? "E" : "W")
                  + "  ·  zoom " + map.zoom.toFixed(1) + "  ·  " + app.mapStatus
            color: Theme.textDim
            font.pixelSize: 11
            font.features: { "tnum": 1 }
        }
    }

    MapLegend {
        objectName: "mapLegend"
        visible: map.styleName !== "Scope" && (showAircraft || showVessels)
        x: map.leftInset + 12
        y: live.height - map.bottomInset - height - 46
        z: 2
    }

    GeofenceBar {
        id: zoneBar
        mapItem: map
        width: Math.min(760, live.width - map.leftInset - map.rightInset - 24)
        x: map.leftInset + (live.width - map.leftInset - map.rightInset - width) / 2
        y: live.margin
        z: 4
    }

    AlertRibbon {
        id: ribbon
        // History playback takes this slot; the ribbon returns when playback ends.
        visible: !playbackBar.visible && !ribbon.dismissed && ribbon.a.text !== undefined && ribbon.a.text !== ""
        width: Math.min(720, live.width - (traffic.collapsed ? 0 : traffic.x + traffic.width) - (live.width - toolbar.x) - 28)
        x: (traffic.collapsed ? 0 : traffic.x + traffic.width) + (toolbar.x - (traffic.collapsed ? 0 : traffic.x + traffic.width) - width) / 2
        y: live.height - height - live.margin
        z: 3
        onShowAll: live.showAlerts()
    }

    PlaybackBar {
        id: playbackBar
        readonly property real leftEdge: traffic.collapsed ? 0 : traffic.x + traffic.width
        width: Math.min(940, toolbar.x - leftEdge - 28)
        x: leftEdge + (toolbar.x - leftEdge - width) / 2
        y: live.height - height - live.margin
        z: 3
    }
}
