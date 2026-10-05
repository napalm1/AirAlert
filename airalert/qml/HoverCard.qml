import QtQuick

// Quick look at the target under the pointer.
GlassPanel {
    id: card
    property var mapItem
    readonly property string key: mapItem ? mapItem.hoverKey : ""
    // Re-read every refresh (countsChanged fires once a second) so a moving or departing target stays current.
    readonly property var info: key !== "" && app.visibleCount >= 0 ? app.hoverInfo(key) : ({})
    visible: key !== "" && key !== app.selectedKey && info.label !== undefined
    blockInput: false
    width: Math.max(150, column.implicitWidth + 24)
    height: column.implicitHeight + 18
    x: mapItem ? Math.min(mapItem.hoverX + 18, parent.width - width - 8) : 0
    y: mapItem ? Math.max(8, mapItem.hoverY - height - 12) : 0

    Column {
        id: column
        x: 12
        y: 9
        spacing: 2
        Row {
            spacing: 6
            Icon { name: card.info.kind === "vessel" ? "ship" : "plane"; size: 14; color: card.info.kind === "vessel" ? Theme.vessel : Theme.accent; anchors.verticalCenter: parent.verticalCenter }
            Text { text: card.info.label || ""; color: Theme.text; font.pixelSize: 13; font.weight: Font.Bold }
            Icon { visible: card.info.watched === true; name: "star"; size: 12; color: Theme.alert; anchors.verticalCenter: parent.verticalCenter }
        }
        Text { text: card.info.secondary || ""; color: Theme.muted; font.pixelSize: 12 }
        Text { text: card.info.line || ""; color: card.info.stale ? Theme.stale : Theme.textDim; font.pixelSize: 12 }
        Text { text: "Click to inspect"; color: Theme.accent; font.pixelSize: 11 }
    }
}
