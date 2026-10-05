import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: field
    property string iconName: ""
    property bool clearable: false
    property string suffix: ""
    implicitHeight: 36
    leftPadding: iconName !== "" ? 34 : 12
    rightPadding: (clearable && text !== "" ? 30 : 12) + (suffix !== "" ? suffixText.implicitWidth + 6 : 0)
    color: Theme.text
    placeholderTextColor: Theme.muted
    selectionColor: Theme.accent
    selectedTextColor: "#ffffff"
    font.pixelSize: 13
    hoverEnabled: true
    selectByMouse: true

    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.raised
        border.color: field.activeFocus ? Theme.accent : field.hovered ? Theme.borderStrong : Theme.border
        border.width: field.activeFocus ? 1.5 : 1
        Icon {
            visible: field.iconName !== ""
            name: field.iconName
            size: 16
            color: field.activeFocus ? Theme.accent : Theme.muted
            anchors.left: parent.left
            anchors.leftMargin: 11
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            id: suffixText
            visible: field.suffix !== ""
            text: field.suffix
            color: Theme.muted
            font.pixelSize: 12
            anchors.right: parent.right
            anchors.rightMargin: field.clearable && field.text !== "" ? 34 : 12
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    IconButton {
        visible: field.clearable && field.text !== ""
        iconName: "close"
        size: 26
        iconSize: 14
        tip: "Clear"
        anchors.right: parent.right
        anchors.rightMargin: 5
        anchors.verticalCenter: parent.verticalCenter
        focusPolicy: Qt.NoFocus
        onClicked: { field.clear(); field.forceActiveFocus() }
    }
}
