import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

// Latest alert, pinned to the bottom of the map. Flashes when a new alert fires.
GlassPanel {
    id: ribbon
    property bool dismissed: false
    readonly property var a: app.latestAlert
    signal showAll()
    visible: !dismissed && a.text !== undefined && a.text !== ""
    height: 60
    borderColor: Util.tint(Theme.alert, 0.5)

    Connections {
        target: app
        function onAlertRaised(text, key, time) {
            ribbon.dismissed = false
            flash.restart()
        }
    }

    Rectangle {
        id: glow
        anchors.fill: parent
        radius: ribbon.radius
        color: "transparent"
        border.color: Theme.alert
        border.width: 2
        opacity: 0
        SequentialAnimation {
            id: flash
            loops: 3
            NumberAnimation { target: glow; property: "opacity"; to: 1; duration: 240 }
            NumberAnimation { target: glow; property: "opacity"; to: 0.15; duration: 420 }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 10
        spacing: 12
        Rectangle {
            Layout.preferredWidth: 38
            Layout.preferredHeight: 38
            radius: 19
            color: Theme.alert
            Icon { anchors.centerIn: parent; name: "bell"; size: 19; color: "#1d1300"; stroke: 2.2 }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1
            Text {
                text: "LATEST ALERT · " + (ribbon.a.time || "")
                color: Theme.alert
                font.pixelSize: 11
                font.weight: Font.Bold
                font.letterSpacing: 1.1
            }
            Text {
                Layout.fillWidth: true
                text: ribbon.a.text || ""
                color: Theme.text
                font.pixelSize: 14
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
        }
        AButton {
            text: "Show"
            iconName: "locate"
            compact: true
            onClicked: app.focusTarget(ribbon.a.key)
        }
        AButton {
            id: snoozeButton
            text: "Snooze"
            iconName: "clock"
            variant: "ghost"
            compact: true
            onClicked: snoozePop.opened ? snoozePop.close() : snoozePop.open()
            Popover {
                id: snoozePop
                title: "Snooze"
                width: 290
                x: snoozeButton.width - width
                y: -height - 10
                Text {
                    Layout.fillWidth: true
                    text: "Snoozed alerts make no sound, pop-up or phone message, but are still recorded."
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
                AButton {
                    Layout.fillWidth: true
                    visible: (ribbon.a.key || "") !== ""
                    text: "This target · 1 hour"
                    compact: true
                    onClicked: { app.snooze("target", ribbon.a.key, 60); snoozePop.close() }
                }
                AButton {
                    Layout.fillWidth: true
                    visible: (ribbon.a.key || "") !== ""
                    text: "This target · rest of today"
                    compact: true
                    onClicked: { app.snooze("target", ribbon.a.key, -1); snoozePop.close() }
                }
                AButton {
                    Layout.fillWidth: true
                    visible: (ribbon.a.ruleId || "") !== ""
                    text: "This alert rule · 1 hour"
                    compact: true
                    onClicked: { app.snooze("rule", ribbon.a.ruleId, 60); snoozePop.close() }
                }
                AButton {
                    Layout.fillWidth: true
                    text: "All alerts · 1 hour"
                    compact: true
                    onClicked: { app.snooze("all", "", 60); snoozePop.close() }
                }
            }
        }
        AButton {
            text: "All alerts"
            variant: "ghost"
            compact: true
            onClicked: ribbon.showAll()
        }
        IconButton {
            iconName: "close"
            tip: "Dismiss"
            size: 30
            iconSize: 15
            onClicked: ribbon.dismissed = true
        }
    }
}
