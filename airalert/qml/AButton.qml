import QtQuick
import QtQuick.Controls.Basic

// Text button. variant: primary | secondary | ghost | danger | dangerSoft | alert
Button {
    id: control
    property string iconName: ""
    property string variant: "secondary"
    property bool compact: false
    readonly property color fg: {
        switch (variant) {
        case "primary": return Theme.accentInk
        case "danger": return "#ffffff"
        case "dangerSoft": return Theme.danger
        case "alert": return "#1d1300"
        case "ghost": return control.hovered ? Theme.text : Theme.textDim
        default: return Theme.text
        }
    }
    readonly property color fill: {
        switch (variant) {
        case "primary": return control.down ? Qt.darker(Theme.accent, 1.15) : control.hovered ? Theme.accentHover : Theme.accent
        case "danger": return control.down ? Qt.darker(Theme.danger, 1.15) : control.hovered ? Qt.lighter(Theme.danger, 1.08) : Theme.danger
        case "dangerSoft": return control.hovered ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.22) : Theme.dangerSoft
        case "alert": return control.hovered ? Qt.lighter(Theme.alert, 1.08) : Theme.alert
        case "ghost": return control.down ? Theme.pressed : control.hovered ? Theme.hover : "transparent"
        default: return control.down ? Theme.pressed : control.hovered ? Theme.hover : Theme.raised
        }
    }
    implicitHeight: compact ? 30 : 36
    leftPadding: text !== "" ? (compact ? 11 : 15) : 9
    rightPadding: text !== "" ? (compact ? 12 : 16) : 9
    hoverEnabled: true
    font.pixelSize: compact ? 12 : 13
    font.weight: Font.DemiBold
    opacity: enabled ? 1 : 0.42

    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 7
            Icon {
                name: control.iconName
                visible: control.iconName !== ""
                size: control.compact ? 14 : 16
                color: control.fg
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: control.text
                visible: control.text !== ""
                color: control.fg
                font: control.font
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.fill
        border.width: control.variant === "secondary" ? 1 : (control.visualFocus ? 2 : 0)
        border.color: control.visualFocus ? Theme.accent : Theme.border
        Behavior on color { ColorAnimation { duration: 110 } }
    }
}
