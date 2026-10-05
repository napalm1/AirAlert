import QtQuick

// Map key. Aircraft are colored by altitude (the ramp); the row below shows the other symbols and states.
// Colors come from the map's own palette (app.altitudeLegend, app.mapKey), so they match what is drawn.
GlassPanel {
    id: legend
    readonly property var stops: app.altitudeLegend
    readonly property var key: app.mapKey
    readonly property real maxAltitude: stops.length ? stops[stops.length - 1].altitude : 40000
    readonly property bool showAircraft: app.layers.aircraft !== false
    readonly property bool showVessels: app.layers.vessels !== false
    width: 296
    height: column.implicitHeight + 22
    blockInput: true

    // Stop i of the ramp; a ramp with fewer than six stops repeats its last one.
    function stopAt(i) {
        return stops.length ? stops[Math.min(i, stops.length - 1)] : { altitude: 0, color: "transparent" }
    }
    function stopPosition(i) {
        return maxAltitude > 0 ? stopAt(i).altitude / maxAltitude : 0
    }

    component KeyItem: Row {
        id: item
        property string icon: ""
        property color tint: Theme.text
        property string label: ""
        property string tip: ""
        spacing: 4
        Icon { name: item.icon; size: 13; color: item.tint; anchors.verticalCenter: parent.verticalCenter }
        Text { text: item.label; color: Theme.textDim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
        HoverHandler { id: hover }
        ATip { visible: hover.hovered && item.tip !== ""; text: item.tip }
    }

    Column {
        id: column
        x: 11
        y: 11
        width: parent.width - 22
        spacing: 7

        Column {
            visible: legend.showAircraft
            width: parent.width
            spacing: 5
            Row {
                spacing: 5
                Icon { name: "plane"; size: 13; color: Theme.textDim; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Aircraft are colored by altitude"
                    color: Theme.textDim
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            // One rounded bar with a stop per altitude step (separate rounded segments left notches).
            Rectangle {
                objectName: "altitudeRamp"
                width: parent.width
                height: 8
                radius: 4
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: legend.stopPosition(0); color: legend.stopAt(0).color }
                    GradientStop { position: legend.stopPosition(1); color: legend.stopAt(1).color }
                    GradientStop { position: legend.stopPosition(2); color: legend.stopAt(2).color }
                    GradientStop { position: legend.stopPosition(3); color: legend.stopAt(3).color }
                    GradientStop { position: legend.stopPosition(4); color: legend.stopAt(4).color }
                    GradientStop { position: legend.stopPosition(5); color: legend.stopAt(5).color }
                }
            }
            Item {
                width: parent.width
                height: 12
                Repeater {
                    model: [0, 10000, 20000, 30000, 40000]
                    delegate: Text {
                        required property var modelData
                        x: Math.min(parent.width - implicitWidth, Math.max(0, parent.width * modelData / legend.maxAltitude - implicitWidth / 2))
                        text: modelData === 0 ? "0 ft" : modelData === 40000 ? "40k+ ft" : (modelData / 1000) + "k"
                        color: Theme.muted
                        font.pixelSize: 10
                    }
                }
            }
        }

        Rectangle {
            visible: legend.showAircraft
            width: parent.width
            height: 1
            color: Theme.border
        }

        Row {
            spacing: 12
            KeyItem {
                visible: legend.showAircraft
                icon: "plane"
                tint: legend.key.unknown
                label: "No altitude"
                tip: "Aircraft that have not reported an altitude"
            }
            KeyItem {
                visible: legend.showVessels
                icon: "hull"
                tint: legend.key.vessel
                label: "Vessel"
            }
            KeyItem {
                icon: legend.showAircraft ? "plane" : "hull"
                tint: legend.key.stale
                label: "Stale"
                tip: "No position update for 60 seconds"
            }
            KeyItem {
                icon: "star"
                tint: legend.key.star
                label: "Watched"
                tip: "On your watchlist"
            }
        }
    }
}
