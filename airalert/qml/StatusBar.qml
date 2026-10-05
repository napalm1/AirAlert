import QtQuick
import QtQuick.Layouts
import "Util.js" as Util

Rectangle {
    id: status
    implicitHeight: 30
    color: Theme.panel

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: Theme.border
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        spacing: 14

        Rectangle {
            Layout.preferredWidth: 8
            Layout.preferredHeight: 8
            radius: 4
            color: app.running ? (app.isSimulation ? Theme.sim : Theme.success) : app.sourceText === "Paused" ? Theme.alert : Theme.muted
        }
        Text {
            Layout.fillWidth: true
            text: app.healthText
            color: Theme.textDim
            font.pixelSize: 12
            elide: Text.ElideRight
        }
        Row {
            objectName: "receptionLow"
            visible: typeof insights !== "undefined" && insights !== null && insights.health.status === "low"
            spacing: 5
            Icon { name: "warning"; size: 13; color: Theme.alert; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Reception low"; color: Theme.alert; font.pixelSize: 12; font.weight: Font.DemiBold }
            HoverHandler { id: healthHover }
            ATip { visible: healthHover.hovered; text: typeof insights !== "undefined" && insights !== null ? insights.health.text : "" }
        }
        Row {
            visible: app.snoozeInfo.active === true
            spacing: 5
            Icon { name: "clock"; size: 13; color: Theme.sim; anchors.verticalCenter: parent.verticalCenter }
            Text { text: app.snoozeInfo.all ? "Alerts snoozed until " + app.snoozeInfo.all : "Some alerts snoozed"; color: Theme.sim; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
        Row {
            visible: app.quietActive
            spacing: 5
            Icon { name: "moon"; size: 13; color: Theme.sim; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Quiet hours"; color: Theme.sim; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
        Row {
            spacing: 5
            Icon { name: "pulse"; size: 13; color: Theme.muted; anchors.verticalCenter: parent.verticalCenter }
            Text { text: app.rate + " msg/s"; color: Theme.textDim; font.pixelSize: 12; font.features: { "tnum": 1 } }
        }
        Row {
            spacing: 5
            Icon { name: "pin"; size: 13; color: app.homeSet ? Theme.muted : Theme.alert; anchors.verticalCenter: parent.verticalCenter }
            Text { text: app.locationText; color: app.homeSet ? Theme.textDim : Theme.alert; font.pixelSize: 12 }
        }
        Row {
            spacing: 5
            Icon { name: app.mapTheme === "Scope only" ? "radar" : "map"; size: 13; color: Theme.muted; anchors.verticalCenter: parent.verticalCenter }
            Text { text: app.mapStatus; color: Theme.textDim; font.pixelSize: 12 }
        }
        Text {
            text: "AirAlert " + app.version
            color: Theme.muted
            font.pixelSize: 11
        }
    }
}
