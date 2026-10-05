import QtQuick
import QtQuick.Controls.Basic

Switch {
    id: control
    property string subtitle: ""
    implicitHeight: subtitle !== "" ? 42 : 30
    hoverEnabled: true
    padding: 0
    opacity: enabled ? 1 : 0.45

    indicator: Rectangle {
        implicitWidth: 38
        implicitHeight: 22
        x: control.leftPadding
        y: (control.height - height) / 2
        radius: 11
        color: control.checked ? Theme.accent : (control.hovered ? Theme.borderStrong : Theme.border)
        Behavior on color { ColorAnimation { duration: 120 } }
        Rectangle {
            x: control.checked ? parent.width - width - 3 : 3
            y: 3
            width: 16
            height: 16
            radius: 8
            color: "#ffffff"
            Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Item {
        implicitWidth: labels.implicitWidth + labels.x
        implicitHeight: labels.implicitHeight
        Column {
            id: labels
            x: control.indicator.width + (control.text !== "" ? 11 : 0)
            width: parent.width - x
            anchors.verticalCenter: parent.verticalCenter
            spacing: 1
            Text {
                text: control.text
                visible: text !== ""
                color: Theme.text
                font.pixelSize: 13
                width: parent.width
                elide: Text.ElideRight
            }
            Text {
                text: control.subtitle
                visible: text !== ""
                color: Theme.muted
                font.pixelSize: 12
                width: parent.width
                wrapMode: Text.WordWrap
            }
        }
    }
}
