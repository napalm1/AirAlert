import QtQuick

// Ripple drawn over the selected target on the map. It plays a few times when the selection changes and then
// rests (the map's own selection ring stays): an endless animation redraws the whole window at 60 frames a second.
Item {
    id: pulse
    objectName: "selectionPulse"
    property var mapItem
    property color tone: Theme.accent
    readonly property string selection: app.selectedKey
    readonly property bool rippling: ringA.animation.running || ringB.animation.running
    x: mapItem ? mapItem.selectedX : 0
    y: mapItem ? mapItem.selectedY : 0
    visible: mapItem && mapItem.selectedVisible && app.selectedKey !== ""
    width: 0
    height: 0

    onSelectionChanged: if (selection !== "") replay()
    function replay() {
        ringA.animation.restart()
        ringB.animation.restart()
    }

    component Ring: Rectangle {
        id: ring
        property int order: 0
        property alias animation: ripple
        width: 36
        height: 36
        radius: 18
        x: -18
        y: -18
        color: "transparent"
        border.color: pulse.tone
        border.width: 2
        opacity: 0
        SequentialAnimation {
            id: ripple
            running: pulse.visible
            loops: 2
            PauseAnimation { duration: ring.order * 900 }
            ParallelAnimation {
                NumberAnimation { target: ring; property: "scale"; from: 0.8; to: 2.1; duration: 1800; easing.type: Easing.OutCubic }
                NumberAnimation { target: ring; property: "opacity"; from: 0.85; to: 0; duration: 1800; easing.type: Easing.OutQuad }
            }
            PauseAnimation { duration: (1 - ring.order) * 900 }
        }
    }
    Ring { id: ringA; order: 0 }
    Ring { id: ringB; order: 1 }
}
