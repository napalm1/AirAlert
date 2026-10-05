import QtQuick
import QtQuick.Controls.Basic

// Square icon-only button with tooltip. `active` shows a selected state.
Button {
    id: control
    property string iconName: ""
    property string tip: ""
    property real size: 36
    property real iconSize: 18
    property bool active: false
    property color tint: Theme.textDim
    property color activeTint: Theme.accent
    implicitWidth: size
    implicitHeight: size
    padding: 0
    hoverEnabled: true
    opacity: enabled ? 1 : 0.4
    Accessible.name: tip

    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: control.iconName
            size: control.iconSize
            color: control.active ? control.activeTint : control.hovered ? Theme.text : control.tint
        }
    }
    background: Rectangle {
        radius: 10
        color: control.active ? Theme.accentSoft : control.down ? Theme.pressed : control.hovered ? Theme.hover : "transparent"
        border.width: control.visualFocus ? 2 : 0
        border.color: Theme.accent
        Behavior on color { ColorAnimation { duration: 110 } }
    }
    ATip {
        visible: control.hovered && control.tip !== ""
        text: control.tip
    }
}
