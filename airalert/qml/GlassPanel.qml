import QtQuick
import QtQuick.Effects

// Floating translucent panel. Swallows clicks, hover and wheel so the map
// underneath does not react to interaction with the panel.
Item {
    id: root
    default property alias content: body.data
    property color color: Theme.glass
    property real radius: Theme.radius
    property bool shadow: true
    property bool blockInput: true
    property color borderColor: Theme.border

    RectangularShadow {
        anchors.fill: bg
        visible: root.shadow
        offset.y: 10
        radius: bg.radius
        blur: 30
        spread: -4
        color: Theme.shadow
    }
    Rectangle {
        id: bg
        anchors.fill: parent
        radius: root.radius
        color: root.color
        border.color: root.borderColor
        border.width: 1
    }
    MouseArea {
        anchors.fill: parent
        enabled: root.blockInput
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        onWheel: (wheel) => wheel.accepted = true
    }
    Item {
        id: body
        anchors.fill: parent
    }
}
