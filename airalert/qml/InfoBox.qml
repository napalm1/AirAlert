import QtQuick
import QtQuick.Layouts
import "Util.js" as Util

Rectangle {
    id: box
    property string text: ""
    property string iconName: "info"
    property color tone: Theme.accent
    Layout.fillWidth: true
    implicitHeight: label.implicitHeight + 24
    radius: 11
    color: Util.tint(tone, 0.08)
    border.color: Util.tint(tone, 0.25)
    Icon {
        id: icon
        name: box.iconName
        size: 16
        color: box.tone
        x: 12
        y: 13
    }
    Text {
        id: label
        anchors.left: icon.right
        anchors.leftMargin: 10
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: box.text
        color: Theme.textDim
        font.pixelSize: 12
        wrapMode: Text.WordWrap
        lineHeight: 1.15
    }
}
