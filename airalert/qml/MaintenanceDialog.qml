import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

AModal {
    id: dialog
    property var info: ({})
    property bool confirming: false
    title: "Database maintenance"
    subtitle: "Recorded sightings, positions and events are stored in history.sqlite in your data folder."
    iconName: "database"
    width: 560
    height: 500

    function openDialog() {
        info = app.maintenanceInfo()
        daysField.text = String(info.retention || 90)
        confirming = false
        open()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16
        GridLayout {
            Layout.fillWidth: true
            columns: 3
            rowSpacing: 10
            columnSpacing: 10
            Repeater {
                model: [
                    { label: "Database size", value: (dialog.info.sizeMb !== undefined ? dialog.info.sizeMb : "—") + " MB" },
                    { label: "Sightings", value: Util.number(dialog.info.sightings) },
                    { label: "Positions", value: Util.number(dialog.info.positions) },
                    { label: "Events", value: Util.number(dialog.info.events) },
                    { label: "Oldest position", value: dialog.info.oldest || "—", wide: true }
                ]
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.columnSpan: modelData.wide ? 2 : 1
                    Layout.preferredHeight: 60
                    radius: 11
                    color: Theme.raised
                    border.color: Theme.border
                    Column {
                        x: 12
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3
                        Text { text: modelData.label.toUpperCase(); color: Theme.muted; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.9 }
                        Text { text: modelData.value; color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
                    }
                }
            }
        }
        FormRow {
            label: "Delete history older than"
            hint: "Automatic retention (Settings → Database) runs at startup and daily. Settings, alerts, watchlist, geofences and the aircraft lookup are never deleted."
            AField {
                id: daysField
                Layout.preferredWidth: 180
                suffix: "days"
                validator: IntValidator { bottom: 1; top: 36500 }
                onTextEdited: dialog.confirming = false
            }
        }
        Rectangle {
            visible: dialog.confirming
            Layout.fillWidth: true
            Layout.preferredHeight: warn.implicitHeight + 20
            radius: 10
            color: Theme.dangerSoft
            border.color: Util.tint(Theme.danger, 0.4)
            Text {
                id: warn
                anchors.fill: parent
                anchors.margins: 10
                text: "This permanently deletes sightings, positions and events older than " + daysField.text + " days. This cannot be undone."
                color: Theme.danger
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }
        Item { Layout.fillHeight: true }
    }

    footer: [
        AButton { text: "Open data folder"; iconName: "folder"; variant: "ghost"; onClicked: app.openDataFolder() },
        Item { Layout.fillWidth: true },
        AButton { text: "Close"; variant: "ghost"; onClicked: dialog.close() },
        AButton {
            text: dialog.confirming ? "Delete permanently" : "Delete old history…"
            iconName: "trash"
            variant: dialog.confirming ? "danger" : "dangerSoft"
            enabled: parseInt(daysField.text) >= 1
            onClicked: {
                if (!dialog.confirming) { dialog.confirming = true; return }
                app.deleteOlderThan(parseInt(daysField.text))
                dialog.info = app.maintenanceInfo()
                dialog.confirming = false
            }
        }
    ]
}
