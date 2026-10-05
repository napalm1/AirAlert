import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

// Inline geofence composer: draw, name, rename or remove a zone.
GlassPanel {
    id: bar
    property var mapItem
    readonly property string mode: app.zoneMode
    readonly property color tone: Theme.dark ? "#f472b6" : "#db2777"
    visible: mode !== ""
    height: layout.implicitHeight + 24
    borderColor: Util.tint(tone, 0.55)

    onModeChanged: {
        if (mode === "create" || mode === "rename") {
            nameField.text = app.zoneName
            nameField.forceActiveFocus()
            nameField.selectAll()
        }
    }

    RowLayout {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 14
        anchors.rightMargin: 12
        spacing: 12

        Rectangle {
            Layout.preferredWidth: 38
            Layout.preferredHeight: 38
            radius: 11
            color: Util.tint(bar.tone, 0.15)
            Icon { anchors.centerIn: parent; name: bar.mode === "remove" ? "trash" : "polygon"; size: 19; color: bar.mode === "remove" ? Theme.danger : bar.tone }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                text: app.zoneTitle.toUpperCase()
                color: bar.mode === "remove" ? Theme.danger : bar.tone
                font.pixelSize: 11
                font.weight: Font.Bold
                font.letterSpacing: 1.2
            }
            Text {
                Layout.fillWidth: true
                text: app.zoneHint
                color: Theme.text
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }
        AField {
            id: nameField
            visible: bar.mode === "create" || bar.mode === "rename"
            Layout.preferredWidth: 210
            placeholderText: "Geofence name"
            maximumLength: 80
            onAccepted: app.commitZone(text)
        }
        AButton {
            visible: bar.mode === "draw"
            text: "Undo"
            iconName: "undo"
            compact: true
            enabled: bar.mapItem && bar.mapItem.vertexCount > 0
            onClicked: bar.mapItem.undoVertex()
        }
        AButton {
            visible: bar.mode === "draw"
            text: "Finish shape"
            iconName: "check"
            variant: "primary"
            compact: true
            enabled: bar.mapItem && bar.mapItem.vertexCount >= 3
            onClicked: app.finishZone()
        }
        AButton {
            visible: bar.mode === "create" || bar.mode === "rename"
            text: bar.mode === "rename" ? "Save name" : "Save geofence"
            variant: "primary"
            compact: true
            onClicked: app.commitZone(nameField.text)
        }
        AButton {
            visible: bar.mode === "remove"
            text: "Remove geofence"
            iconName: "trash"
            variant: "danger"
            compact: true
            onClicked: app.commitZone("")
        }
        AButton {
            text: "Cancel"
            variant: "ghost"
            compact: true
            onClicked: app.cancelZone()
        }
    }
}
