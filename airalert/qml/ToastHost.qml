import QtQuick
import QtQuick.Effects

// Stack of transient notifications (info, success, warning, error, alert).
Column {
    id: host
    spacing: 8
    width: 440

    property var lastAlert: null

    function show(text, kind) {
        if (host.children.length > 4)
            host.children[0].destroy()
        return toastComponent.createObject(host, { message: text, kind: kind || "info" })
    }

    // Bursts of alerts collapse into one toast that counts them.
    function showAlert(text) {
        if (lastAlert && lastAlert.live) {
            lastAlert.count += 1
            lastAlert.message = lastAlert.count + " new alerts · latest: " + text
            lastAlert.restart()
            return
        }
        lastAlert = show(text, "alert")
    }

    Component {
        id: toastComponent
        Item {
            id: toast
            property string message: ""
            property string kind: "info"
            property int count: 1
            property bool live: true
            function restart() { life.restart() }
            readonly property color tone: kind === "success" ? Theme.success : kind === "warning" ? Theme.alert
                                        : kind === "error" ? Theme.danger : kind === "alert" ? Theme.alert : Theme.accent
            width: host.width
            height: box.height
            opacity: 0
            Component.onCompleted: appear.start()

            NumberAnimation on opacity { id: appear; running: false; to: 1; duration: 160 }
            SequentialAnimation {
                id: vanish
                ScriptAction { script: toast.live = false }
                NumberAnimation { target: toast; property: "opacity"; to: 0; duration: 220 }
                ScriptAction { script: toast.destroy() }
            }
            Timer {
                id: life
                interval: toast.kind === "alert" ? 8000 : toast.kind === "error" ? 9000 : 4600
                running: !hover.containsMouse
                onTriggered: vanish.start()
            }
            RectangularShadow {
                anchors.fill: box
                offset.y: 8
                radius: box.radius
                blur: 24
                spread: -4
                color: Theme.shadow
            }
            Rectangle {
                id: box
                width: parent.width
                height: Math.max(48, content.implicitHeight + 22)
                radius: 12
                color: Theme.panel
                border.color: Qt.rgba(toast.tone.r, toast.tone.g, toast.tone.b, 0.55)
                Rectangle {
                    width: 4
                    radius: 2
                    color: toast.tone
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.margins: 8
                }
                Row {
                    id: content
                    anchors.left: parent.left
                    anchors.leftMargin: 22
                    anchors.right: parent.right
                    anchors.rightMargin: 40
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 11
                    Icon {
                        name: toast.kind === "success" ? "check" : toast.kind === "error" || toast.kind === "warning" ? "warning"
                              : toast.kind === "alert" ? "bell" : "info"
                        color: toast.tone
                        size: 18
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        width: content.width - 30
                        text: toast.message
                        color: Theme.text
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                MouseArea {
                    id: hover
                    anchors.fill: parent
                    hoverEnabled: true
                }
                IconButton {
                    iconName: "close"
                    size: 26
                    iconSize: 13
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: vanish.start()
                }
            }
        }
    }
}
