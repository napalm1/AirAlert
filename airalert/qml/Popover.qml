import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts

Popup {
    id: pop
    property string title: ""
    default property alias content: column.data
    padding: 14
    width: 290
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 }
            NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 150; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 90 } }

    background: Item {
        RectangularShadow {
            anchors.fill: frame
            offset.y: 10
            radius: frame.radius
            blur: 30
            spread: -4
            color: Theme.shadow
        }
        Rectangle {
            id: frame
            anchors.fill: parent
            radius: 13
            color: Theme.panel
            border.color: Theme.borderStrong
        }
    }
    contentItem: ColumnLayout {
        id: column
        spacing: 8
        Text {
            text: pop.title.toUpperCase()
            visible: pop.title !== ""
            color: Theme.muted
            font.pixelSize: 11
            font.weight: Font.Bold
            font.letterSpacing: 1.2
            Layout.bottomMargin: 2
        }
    }
}
