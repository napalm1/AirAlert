import QtQuick
import QtQuick.Controls.Basic

CheckBox {
    id: control
    property string iconName: ""
    implicitHeight: 30
    hoverEnabled: true
    padding: 0
    spacing: 9

    indicator: Rectangle {
        implicitWidth: 20
        implicitHeight: 20
        x: control.leftPadding
        y: (control.height - height) / 2
        radius: 6
        color: control.checked ? Theme.accent : "transparent"
        border.color: control.checked ? Theme.accent : (control.hovered ? Theme.muted : Theme.borderStrong)
        border.width: 1.5
        Icon {
            anchors.centerIn: parent
            name: "check"
            size: 14
            stroke: 2.6
            color: "#ffffff"
            visible: control.checked
        }
    }
    contentItem: Row {
        leftPadding: control.indicator.width + control.spacing
        spacing: 6
        Icon {
            visible: control.iconName !== ""
            name: control.iconName
            size: 15
            color: Theme.textDim
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: control.text
            color: Theme.text
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
