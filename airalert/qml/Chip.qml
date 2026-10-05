import QtQuick
import "Util.js" as Util

Rectangle {
    id: chip
    property string text: ""
    property string iconName: ""
    property color tone: Theme.accent
    visible: text !== ""
    implicitWidth: row.implicitWidth + 16
    implicitHeight: 24
    radius: 12
    color: Util.tint(tone, 0.13)
    border.color: Util.tint(tone, 0.32)
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 5
        Icon {
            visible: chip.iconName !== ""
            name: chip.iconName
            size: 12
            stroke: 2.3
            color: chip.tone
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: chip.text
            color: chip.tone
            font.pixelSize: 12
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
