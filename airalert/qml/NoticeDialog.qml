import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Simple acknowledgement dialog for important one-off messages.
AModal {
    id: dialog
    property string message: ""
    iconName: "warning"
    iconColor: Theme.alert
    width: 520
    height: Math.min(360, body.implicitHeight + 190)

    function show(heading, text) {
        title = heading
        message = text
        open()
    }

    Text {
        id: body
        anchors.fill: parent
        anchors.margins: 24
        text: dialog.message
        color: Theme.text
        font.pixelSize: 14
        wrapMode: Text.WordWrap
    }

    footer: [
        Item { Layout.fillWidth: true },
        AButton { text: "OK"; variant: "primary"; onClicked: dialog.close() }
    ]
}
