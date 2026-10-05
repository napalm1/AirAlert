import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts

// Centered modal dialog shell with header, body and footer slots.
Popup {
    id: modal
    property string title: ""
    property string subtitle: ""
    property string iconName: ""
    property color iconColor: Theme.accent
    default property alias content: body.data
    property alias footer: footerRow.data
    property bool showFooter: true
    modal: true
    focus: true
    dim: true
    padding: 0
    anchors.centerIn: Overlay.overlay
    closePolicy: Popup.CloseOnEscape

    Overlay.modal: Rectangle { color: Theme.scrim }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 }
            NumberAnimation { property: "scale"; from: 0.965; to: 1; duration: 180; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; to: 0; duration: 100 }
    }

    background: Item {
        RectangularShadow {
            anchors.fill: frame
            offset.y: 18
            radius: frame.radius
            blur: 48
            spread: -6
            color: Theme.shadow
        }
        Rectangle {
            id: frame
            anchors.fill: parent
            radius: 18
            color: Theme.panel
            border.color: Theme.border
        }
    }

    contentItem: ColumnLayout {
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 16
            Layout.topMargin: 20
            Layout.bottomMargin: 14
            spacing: 14
            Rectangle {
                visible: modal.iconName !== ""
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40
                radius: 12
                color: Qt.rgba(modal.iconColor.r, modal.iconColor.g, modal.iconColor.b, 0.14)
                Icon {
                    anchors.centerIn: parent
                    name: modal.iconName
                    size: 20
                    color: modal.iconColor
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: modal.title
                    color: Theme.text
                    font.pixelSize: 19
                    font.weight: Font.DemiBold
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                Text {
                    text: modal.subtitle
                    visible: text !== ""
                    color: Theme.muted
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
            IconButton {
                iconName: "close"
                tip: "Close (Esc)"
                Layout.alignment: Qt.AlignTop
                onClicked: modal.close()
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: Theme.border
        }
        Item {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
        }
        Rectangle {
            visible: modal.showFooter
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: Theme.border
        }
        RowLayout {
            id: footerRow
            visible: modal.showFooter
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.topMargin: 14
            Layout.bottomMargin: 16
            spacing: 10
        }
    }
}
