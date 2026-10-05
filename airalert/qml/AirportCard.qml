import QtQuick
import QtQuick.Shapes

// Details for the airport symbol under the pointer: name, codes, city and radio frequencies.
GlassPanel {
    id: card
    property var mapItem
    readonly property var info: mapItem ? mapItem.airportInfo : ({})
    readonly property bool shown: mapItem !== undefined && mapItem !== null && info.code !== undefined && mapItem.hoverKey === ""
    readonly property var freqs: info.freqs || []
    visible: shown
    blockInput: false
    width: Math.min(330, Math.max(250, header.implicitWidth + 28))
    height: column.implicitHeight + 24
    x: mapItem ? Math.max(8, Math.min(mapItem.airportX + 16, parent.width - width - 8)) : 0
    y: mapItem ? (mapItem.airportY - height - 12 >= 8 ? mapItem.airportY - height - 12 : mapItem.airportY + 16) : 0

    Column {
        id: column
        x: 14
        y: 12
        width: card.width - 28
        spacing: 7

        Row {
            id: header
            spacing: 9
            // Chart-style symbol matching the map: ring with ticks, small ring, or H for heliports.
            Shape {
                width: 22
                height: 22
                anchors.verticalCenter: parent.verticalCenter
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: Theme.accent
                    strokeWidth: 1.7
                    fillColor: Theme.panel
                    capStyle: ShapePath.FlatCap
                    PathSvg {
                        path: card.info.tier === 0
                              ? "M 6.5 11 A 4.5 4.5 0 1 0 15.5 11 A 4.5 4.5 0 1 0 6.5 11 Z M 11 1.8 V 6.3 M 11 15.7 V 20.2 M 1.8 11 H 6.3 M 15.7 11 H 20.2"
                              : card.info.tier === 2
                                ? "M 4 11 A 7 7 0 1 0 18 11 A 7 7 0 1 0 4 11 Z M 8.6 7.6 V 14.4 M 13.4 7.6 V 14.4 M 8.6 11 H 13.4"
                                : "M 7 11 A 4 4 0 1 0 15 11 A 4 4 0 1 0 7 11 Z"
                    }
                }
            }
            Text {
                text: card.info.code || ""
                color: Theme.text
                font.pixelSize: 17
                font.weight: Font.Bold
                font.letterSpacing: 0.6
                anchors.verticalCenter: parent.verticalCenter
            }
            Chip {
                text: card.info.iata && card.info.iata !== card.info.code ? "IATA " + card.info.iata : ""
                tone: Theme.accent
                anchors.verticalCenter: parent.verticalCenter
            }
            Chip {
                text: card.info.icao && card.info.icao !== card.info.code ? "ICAO " + card.info.icao : ""
                tone: Theme.accent
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        Column {
            width: parent.width
            spacing: 1
            Text {
                width: parent.width
                text: card.info.name || ""
                color: Theme.text
                font.pixelSize: 13
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }
            Text {
                width: parent.width
                text: [card.info.city || "", card.info.tierName || ""].filter(function(s) { return s !== "" }).join(" · ")
                color: Theme.muted
                font.pixelSize: 12
                elide: Text.ElideRight
            }
        }

        Rectangle {
            width: parent.width
            height: 1
            color: Theme.border
            visible: card.freqs.length > 0
        }

        Grid {
            visible: card.freqs.length > 0
            columns: 2
            columnSpacing: 16
            rowSpacing: 4
            Repeater {
                model: card.freqs
                delegate: Row {
                    required property var modelData
                    spacing: 7
                    Text {
                        width: 58
                        text: modelData.kind
                        color: Theme.muted
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        font.letterSpacing: 0.6
                        elide: Text.ElideRight
                        anchors.baseline: mhz.baseline
                    }
                    Text {
                        id: mhz
                        text: modelData.mhz
                        color: Theme.text
                        font.pixelSize: 13
                        font.features: { "tnum": 1 }
                    }
                }
            }
        }

        Text {
            visible: card.freqs.length === 0
            text: "No published frequencies"
            color: Theme.muted
            font.pixelSize: 11
        }
    }
}
