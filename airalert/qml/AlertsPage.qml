import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

Item {
    id: page
    signal showLive()

    readonly property var conditionLabels: ({
        "enter": "Within range", "leave": "Out of range", "first": "First detected", "signal": "Heard",
        "altitude_below": "Below altitude", "speed_above": "Above speed", "zone_enter": "Enters geofence",
        "zone_leave": "Leaves geofence", "approach": "Predicted pass", "squawk_emergency": "Emergency squawk",
        "first_aircraft": "New aircraft", "first_type": "New type", "first_operator": "New airline",
        "rare_type": "Rare type", "military": "Military", "circling": "Circling"
    })

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 18

        PageHeader {
            Layout.fillWidth: true
            title: "Alerts"
            subtitle: "Match an identity, distance or predicted pass, altitude, speed, geofence or emergency squawk. Deliver by sound, pop-up, voice or phone."
            AButton {
                visible: app.snoozeInfo.active !== true
                text: "Snooze all · 1 h"
                iconName: "clock"
                variant: "ghost"
                onClicked: app.snooze("all", "", 60)
                ATip { visible: parent.hovered; text: "Silence every alert for an hour. Alerts are still recorded." }
            }
            AButton {
                visible: !app.hasEmergencyRule
                text: "Add emergency alert"
                iconName: "warning"
                onClicked: app.newEmergencyRule()
                ATip { visible: parent.hovered; text: "Alert on squawk 7500 / 7600 / 7700, even during quiet hours" }
            }
            AButton {
                text: "New alert"
                iconName: "plus"
                variant: "primary"
                onClicked: app.newRule()
            }
        }

        InfoBox {
            visible: app.quietActive
            iconName: "moon"
            tone: Theme.sim
            text: "Quiet hours are on right now: sound, voice, pop-ups and phone pushes are held back unless an alert ignores quiet hours. Alerts are still recorded below."
        }

        RowLayout {
            objectName: "snoozeBanner"
            visible: app.snoozeInfo.active === true
            Layout.fillWidth: true
            spacing: 12
            InfoBox {
                Layout.fillWidth: true
                iconName: "clock"
                tone: Theme.sim
                text: {
                    var s = app.snoozeInfo, parts = []
                    if (s.all) parts.push("All alerts are snoozed until " + s.all)
                    if (s.rules > 0) parts.push(s.rules + (s.rules === 1 ? " alert rule is" : " alert rules are") + " snoozed")
                    var names = (s.targets || []).map(function(t) { return t.label + " until " + t.until })
                    if (names.length) parts.push("Snoozed targets: " + names.join(", "))
                    return parts.join(". ") + ". Snoozed alerts are still recorded below and in History."
                }
            }
            AButton { text: "Resume alerts"; iconName: "bell"; onClicked: app.wakeAll() }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 18

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10
                Text {
                    text: "RULES · " + rules.count
                    color: Theme.muted
                    font.pixelSize: 11
                    font.weight: Font.Bold
                    font.letterSpacing: 1.2
                }
                ListView {
                    id: rules
                    objectName: "rulesList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 10
                    model: app.rulesModel
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: Rectangle {
                        id: card
                        required property int index
                        required property var model
                        readonly property int row: model.row
                        readonly property string name: model.name
                        readonly property bool ruleOn: model.enabled
                        readonly property string kind: model.kind
                        readonly property string match: model.match
                        readonly property string condition: model.condition
                        readonly property string threshold: model.threshold
                        readonly property var cooldown: model.cooldown
                        readonly property bool sound: model.sound
                        readonly property bool desktop: model.desktop
                        readonly property string sentence: model.sentence
                        readonly property bool inactive: model.inactive
                        readonly property bool speak: model.speak
                        readonly property bool phone: model.phone
                        readonly property bool overrideQuiet: model.overrideQuiet
                        readonly property int extras: model.extras
                        readonly property string ruleKey: model.key
                        readonly property int firedTotal: model.firedTotal
                        readonly property int firedDay: model.firedDay
                        readonly property int firedWeek: model.firedWeek
                        readonly property string lastFired: model.lastFired
                        readonly property string snoozedUntil: model.snoozedUntil
                        readonly property var daily: model.daily || []
                        readonly property int dailyMax: Math.max(1, Math.max.apply(null, daily.length ? daily : [0]))
                        property bool confirming: false
                        width: rules.width - 8
                        height: body.implicitHeight + 32
                        radius: Theme.radius
                        color: Theme.panel
                        border.color: cardArea.containsMouse ? Theme.borderStrong : Theme.border
                        MouseArea { id: cardArea; anchors.fill: parent; hoverEnabled: true; onDoubleClicked: app.editRule(card.row) }
                        Rectangle {
                            width: 4
                            radius: 2
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.margins: 12
                            color: card.ruleOn && !card.inactive ? Theme.alert : Theme.border
                        }
                        RowLayout {
                            id: body
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 30
                            anchors.rightMargin: 14
                            spacing: 14
                            Rectangle {
                                Layout.preferredWidth: 42
                                Layout.preferredHeight: 42
                                Layout.alignment: Qt.AlignTop
                                radius: 12
                                color: card.ruleOn ? Theme.alertSoft : Theme.raised
                                Icon {
                                    anchors.centerIn: parent
                                    name: card.kind === "vessel" ? "ship" : card.kind === "aircraft" ? "plane" : "bell"
                                    size: 20
                                    color: card.ruleOn ? Theme.alert : Theme.muted
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                RowLayout {
                                    spacing: 8
                                    Text {
                                        text: card.name
                                        color: card.ruleOn ? Theme.text : Theme.muted
                                        font.pixelSize: 15
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                        Layout.maximumWidth: 360
                                    }
                                    Chip { visible: card.inactive; text: "Geofence missing · inactive"; tone: Theme.danger; iconName: "warning" }
                                    Chip { visible: !card.ruleOn; text: "Paused"; tone: Theme.muted }
                                    Chip { visible: card.snoozedUntil !== ""; text: "Snoozed until " + card.snoozedUntil; tone: Theme.sim; iconName: "clock" }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: card.sentence
                                    color: Theme.textDim
                                    font.pixelSize: 13
                                    wrapMode: Text.WordWrap
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Chip { text: page.conditionLabels[card.condition] || card.condition; tone: Theme.accent; iconName: "pulse" }
                                    Chip { text: card.threshold !== "—" ? card.threshold : ""; tone: Theme.accent; iconName: card.condition.indexOf("zone") === 0 ? "polygon" : "" }
                                    Chip { text: card.match; tone: Theme.muted }
                                    Chip { text: "Cooldown " + card.cooldown + " s"; tone: Theme.muted; iconName: "clock" }
                                    Chip { visible: card.sound; text: "Sound"; tone: Theme.muted; iconName: "sound" }
                                    Chip { visible: card.desktop; text: "Desktop"; tone: Theme.muted; iconName: "monitor" }
                                    Chip { visible: card.speak; text: "Voice"; tone: Theme.muted; iconName: "sound" }
                                    Chip { visible: card.phone; text: "Phone"; tone: Theme.muted; iconName: "bell" }
                                    Chip { visible: card.overrideQuiet; text: "Ignores quiet hours"; tone: Theme.sim; iconName: "moon" }
                                    Chip { visible: card.extras > 0; text: "+" + card.extras + (card.extras === 1 ? " condition" : " conditions"); tone: Theme.accent; iconName: "sliders" }
                                }
                                // How often this rule fires: one bar per day for two weeks, so noisy rules stand out.
                                RowLayout {
                                    spacing: 10
                                    Row {
                                        visible: card.firedTotal > 0
                                        spacing: 2
                                        Repeater {
                                            model: card.daily
                                            delegate: Item {
                                                required property var modelData
                                                width: 5
                                                height: 16
                                                Rectangle {
                                                    anchors.bottom: parent.bottom
                                                    width: parent.width
                                                    height: modelData > 0 ? Math.max(3, 16 * modelData / card.dailyMax) : 2
                                                    radius: 1
                                                    color: modelData > 0 ? Theme.alert : Theme.border
                                                }
                                            }
                                        }
                                    }
                                    Text {
                                        objectName: "ruleActivity"
                                        text: card.firedTotal > 0
                                              ? "Fired " + card.firedDay + "× in 24 h · " + card.firedWeek + "× in 7 days · " + card.firedTotal + "× in all · last " + card.lastFired
                                              : "Has not fired yet"
                                        color: Theme.muted
                                        font.pixelSize: 12
                                    }
                                }
                            }
                            ASwitch {
                                checked: card.ruleOn
                                Layout.alignment: Qt.AlignTop
                                onToggled: app.setRuleEnabled(card.row, checked)
                                ATip { visible: parent.hovered; text: card.ruleOn ? "Pause this alert" : "Enable this alert" }
                            }
                            IconButton {
                                iconName: "clock"
                                tip: card.snoozedUntil !== "" ? "End snooze" : "Snooze for 1 hour"
                                active: card.snoozedUntil !== ""
                                Layout.alignment: Qt.AlignTop
                                onClicked: app.snooze("rule", card.ruleKey, card.snoozedUntil !== "" ? 0 : 60)
                            }
                            IconButton {
                                iconName: "edit"
                                tip: "Edit alert"
                                Layout.alignment: Qt.AlignTop
                                onClicked: app.editRule(card.row)
                            }
                            IconButton {
                                visible: !card.confirming
                                iconName: "trash"
                                tip: "Delete alert"
                                tint: Theme.danger
                                Layout.alignment: Qt.AlignTop
                                onClicked: { card.confirming = true; confirmTimer.restart() }
                            }
                            AButton {
                                visible: card.confirming
                                text: "Delete?"
                                variant: "danger"
                                compact: true
                                Layout.alignment: Qt.AlignTop
                                onClicked: app.deleteRule(card.row)
                            }
                            Timer { id: confirmTimer; interval: 3500; onTriggered: card.confirming = false }
                        }
                    }

                    Rectangle {
                        visible: rules.count === 0
                        anchors.top: parent.top
                        width: parent.width - 8
                        height: 200
                        radius: Theme.radius
                        color: Theme.panel
                        border.color: Theme.border
                        Column {
                            anchors.centerIn: parent
                            width: parent.width - 60
                            spacing: 10
                            Icon { name: "bell"; size: 34; color: Theme.alert; anchors.horizontalCenter: parent.horizontalCenter }
                            Text {
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                text: "No alerts yet"
                                color: Theme.text
                                font.pixelSize: 16
                                font.weight: Font.DemiBold
                            }
                            Text {
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                text: "Example: aircraft with registration N123AB comes within 25 miles of home. For tail-number alerts, import an ICAO → registration lookup on the Watchlist page."
                                color: Theme.muted
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                            AButton { text: "Create your first alert"; variant: "primary"; iconName: "plus"; anchors.horizontalCenter: parent.horizontalCenter; onClicked: app.newRule() }
                        }
                    }
                }
            }

            Rectangle {
                Layout.preferredWidth: 400
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.panel
                border.color: Theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "RECENT ALERTS"
                            color: Theme.muted
                            font.pixelSize: 11
                            font.weight: Font.Bold
                            font.letterSpacing: 1.2
                            Layout.fillWidth: true
                        }
                        Text { text: feed.count + " shown"; color: Theme.muted; font.pixelSize: 12 }
                    }
                    ListView {
                        id: feed
                        objectName: "alertFeedList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: app.alertFeed
                        spacing: 4
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        section.property: "dayText"
                        section.delegate: Text {
                            required property string section
                            text: section
                            color: Theme.muted
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            topPadding: 8
                            bottomPadding: 4
                        }
                        delegate: Rectangle {
                            id: event
                            required property string text
                            required property string timeText
                            required property string target
                            required property bool simulated
                            width: feed.width - 8
                            height: eventText.implicitHeight + 22
                            radius: 10
                            color: eventArea.containsMouse ? Theme.hover : Theme.raised
                            MouseArea {
                                id: eventArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { app.focusTarget(event.target); page.showLive() }
                            }
                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                x: 12
                                y: 16
                                color: event.simulated ? Theme.sim : Theme.alert
                            }
                            Text {
                                id: eventText
                                anchors.left: parent.left
                                anchors.leftMargin: 30
                                anchors.right: eventTime.left
                                anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: event.text
                                color: Theme.text
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                id: eventTime
                                anchors.right: parent.right
                                anchors.rightMargin: 12
                                anchors.verticalCenter: parent.verticalCenter
                                text: event.timeText
                                color: Theme.muted
                                font.pixelSize: 12
                                font.features: { "tnum": 1 }
                            }
                        }
                        Text {
                            visible: feed.count === 0
                            anchors.centerIn: parent
                            width: parent.width - 40
                            horizontalAlignment: Text.AlignHCenter
                            text: "Alerts that fire appear here, newest first. Every alert is also recorded in History."
                            color: Theme.muted
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }
}
