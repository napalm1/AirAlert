import QtQuick

// Range / center readout shown in radar-scope mode.
GlassPanel {
    id: readout
    property var mapItem
    width: 216
    height: column.implicitHeight + 20
    color: Qt.rgba(0.01, 0.07, 0.035, 0.9)
    borderColor: "#1d5a33"

    function coord(value, pos, neg) {
        return Math.abs(value).toFixed(3) + "°" + (value >= 0 ? pos : neg)
    }

    Column {
        id: column
        x: 13
        y: 10
        spacing: 3
        Text { text: "RADAR SCOPE"; color: "#6fcf91"; font.family: "Consolas"; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.5 }
        Row {
            spacing: 6
            Text { text: "RANGE"; color: "#4e9a69"; font.family: "Consolas"; font.pixelSize: 11; anchors.baseline: rangeValue.baseline }
            Text { id: rangeValue; text: readout.mapItem ? readout.mapItem.rangeText : ""; color: "#c2ffd8"; font.family: "Consolas"; font.pixelSize: 18; font.weight: Font.Bold }
        }
        Text {
            text: readout.mapItem ? readout.coord(readout.mapItem.centerLat, "N", "S") + "  " + readout.coord(readout.mapItem.centerLon, "E", "W") : ""
            color: "#9ff0bb"
            font.family: "Consolas"
            font.pixelSize: 11
        }
        Text {
            text: "Drag to move · scroll for range\nSweep is cosmetic only"
            color: "#4e9a69"
            font.family: "Consolas"
            font.pixelSize: 10
            lineHeight: 1.15
        }
    }
}
