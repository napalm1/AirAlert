import QtQuick
import QtQuick.Layouts

RowLayout {
    id: header
    property string title: ""
    property string subtitle: ""
    default property alias actions: actionRow.data
    spacing: 16
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 3
        Text {
            text: header.title
            color: Theme.text
            font.pixelSize: 26
            font.weight: Font.Bold
            font.letterSpacing: -0.3
        }
        Text {
            text: header.subtitle
            visible: text !== ""
            color: Theme.muted
            font.pixelSize: 13
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
    }
    RowLayout {
        id: actionRow
        spacing: 10
        Layout.alignment: Qt.AlignBottom
    }
}
