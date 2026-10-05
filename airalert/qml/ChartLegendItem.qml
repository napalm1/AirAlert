import QtQuick

// Legend entry: a key mirroring the mark (bar swatch or line stroke) and a text label.
Row {
    id: item
    property color color: Theme.accent
    property string text: ""
    property bool line: false
    spacing: 6
    Rectangle {
        width: item.line ? 14 : 10
        height: item.line ? 2 : 10
        radius: item.line ? 1 : 3
        color: item.color
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        text: item.text
        color: Theme.textDim
        font.pixelSize: 12
        anchors.verticalCenter: parent.verticalCenter
    }
}
