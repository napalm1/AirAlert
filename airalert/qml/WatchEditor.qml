import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AModal {
    id: editor
    property int index: -1
    property string kind: "aircraft"
    property string error: ""
    title: index < 0 ? "Add to watchlist" : "Edit watchlist entry"
    subtitle: "Matching targets are starred on the map. Use an ICAO address, registration or MMSI."
    iconName: "star"
    iconColor: Theme.alert
    width: 540
    height: 540

    function openWith(i, data) {
        index = i
        kind = data.kind || "aircraft"
        nameField.text = data.name || ""
        idField.text = data.identifier || ""
        notesField.text = data.notes || ""
        error = ""
        open();
        if (idField.text === "") idField.forceActiveFocus()
        else nameField.forceActiveFocus()
    }

    function save() {
        var result = app.saveWatch(index, { kind: kind, name: nameField.text, identifier: idField.text, notes: notesField.text })
        if (result !== "") error = result
        else close()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16
        FormRow {
            label: "Type"
            Segmented {
                options: [{ value: "aircraft", label: "Aircraft" }, { value: "vessel", label: "Vessel" }]
                value: editor.kind
                onPicked: (v) => editor.kind = v
            }
        }
        FormRow {
            label: "Name"
            AField { id: nameField; Layout.fillWidth: true; placeholderText: "Friendly name (optional)" }
        }
        FormRow {
            label: editor.kind === "vessel" ? "MMSI" : "ICAO address or registration"
            AField {
                id: idField
                Layout.fillWidth: true
                placeholderText: editor.kind === "vessel" ? "e.g. 366999712" : "e.g. A1B2C3 or N123AB"
                onAccepted: editor.save()
            }
        }
        FormRow {
            label: "Notes"
            Layout.fillHeight: true
            TextArea {
                id: notesField
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 80
                wrapMode: TextEdit.Wrap
                color: Theme.text
                placeholderText: "Anything worth remembering"
                placeholderTextColor: Theme.muted
                selectionColor: Theme.accent
                font.pixelSize: 13
                padding: 10
                background: Rectangle {
                    radius: Theme.radiusSmall
                    color: Theme.raised
                    border.color: notesField.activeFocus ? Theme.accent : Theme.border
                    border.width: notesField.activeFocus ? 1.5 : 1
                }
            }
        }
    }

    footer: [
        Text { Layout.fillWidth: true; text: editor.error; color: Theme.danger; font.pixelSize: 13; wrapMode: Text.WordWrap },
        AButton { text: "Cancel"; variant: "ghost"; onClicked: editor.close() },
        AButton { text: "Save"; variant: "primary"; iconName: "check"; onClicked: editor.save() }
    ]
}
