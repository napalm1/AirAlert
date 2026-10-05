import QtQuick

// Hover readout for charts: a title line, then one row per series (line key, value, label).
// Values lead in strong ink; labels follow in muted ink. Place it as the last child of a chart.
Rectangle {
    id: tip
    property string title: ""
    property var rows: []          // [{color, value, label}]
    property real anchorX: 0
    property real anchorY: 0
    property bool shown: false
    visible: shown && (title !== "" || rows.length > 0)
    z: 50
    width: Math.max(96, column.implicitWidth + 22)
    height: column.implicitHeight + 16
    radius: 9
    color: Theme.dark ? "#16243b" : "#ffffff"
    border.color: Theme.borderStrong
    x: {
        var p = parent ? parent.width : 0
        var right = anchorX + 14
        return right + width > p - 2 ? Math.max(2, anchorX - width - 14) : right
    }
    y: Math.max(2, Math.min((parent ? parent.height : 0) - height - 2, anchorY - height / 2))

    Column {
        id: column
        x: 11
        y: 8
        spacing: 4
        Text {
            visible: tip.title !== ""
            text: tip.title
            color: Theme.textDim
            font.pixelSize: 11
            font.weight: Font.DemiBold
        }
        Repeater {
            model: tip.rows
            delegate: Row {
                required property var modelData
                spacing: 7
                Rectangle {
                    visible: modelData.color !== undefined
                    width: 12
                    height: 2
                    radius: 1
                    color: modelData.color || "transparent"
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: modelData.value
                    color: Theme.text
                    font.pixelSize: 13
                    font.weight: Font.Bold
                    font.features: { "tnum": 1 }
                }
                Text {
                    text: modelData.label || ""
                    visible: text !== ""
                    color: Theme.muted
                    font.pixelSize: 12
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
}
