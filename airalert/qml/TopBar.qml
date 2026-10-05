import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

Rectangle {
    id: bar
    property int page: 0
    signal pageRequested(int index)
    implicitHeight: 62
    color: Theme.panel

    readonly property var tabs: [
        { label: "Live map", icon: "map" },
        { label: "Alerts", icon: "bell" },
        { label: "History", icon: "clock" },
        { label: "Watchlist", icon: "starLine" },
        { label: "Statistics", icon: "chart" }
    ]

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 14
        spacing: 12

        // Brand
        Row {
            spacing: 11
            Layout.alignment: Qt.AlignVCenter
            Rectangle {
                width: 34
                height: 34
                radius: 10
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: "#2f7df6" }
                    GradientStop { position: 1; color: "#18b9a6" }
                }
                Icon {
                    anchors.centerIn: parent
                    name: "radar"
                    size: 20
                    stroke: 2.2
                    color: "#ffffff"
                }
                Rectangle {
                    width: 9
                    height: 9
                    radius: 4.5
                    color: Theme.alert
                    border.color: Theme.panel
                    border.width: 2
                    x: parent.width - 7
                    y: -2
                }
            }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    text: "AirAlert"
                    color: Theme.text
                    font.pixelSize: 17
                    font.weight: Font.Bold
                    font.letterSpacing: -0.2
                }
                Text {
                    text: "ADS-B · AIS station"
                    color: Theme.muted
                    font.pixelSize: 11
                    font.letterSpacing: 0.3
                }
            }
        }

        Item { Layout.preferredWidth: 14 }

        // Page navigation (Left/Right/Home/End when focused)
        Row {
            id: nav
            spacing: 4
            Layout.alignment: Qt.AlignVCenter
            activeFocusOnTab: true
            Keys.onLeftPressed: bar.pageRequested((bar.page + bar.tabs.length - 1) % bar.tabs.length)
            Keys.onRightPressed: bar.pageRequested((bar.page + 1) % bar.tabs.length)
            Keys.onPressed: (event) => {
                if (event.key === Qt.Key_Home) { bar.pageRequested(0); event.accepted = true }
                else if (event.key === Qt.Key_End) { bar.pageRequested(bar.tabs.length - 1); event.accepted = true }
            }
            Repeater {
                model: bar.tabs
                delegate: Rectangle {
                    id: tab
                    required property var modelData
                    required property int index
                    readonly property bool current: bar.page === index
                    width: tabRow.implicitWidth + 26
                    height: 38
                    radius: 10
                    color: current ? Theme.accentSoft : tabArea.containsMouse ? Theme.hover : "transparent"
                    border.width: current && nav.activeFocus ? 1.5 : 0
                    border.color: Theme.accent
                    Behavior on color { ColorAnimation { duration: 120 } }
                    Row {
                        id: tabRow
                        anchors.centerIn: parent
                        spacing: 8
                        Icon {
                            name: tab.modelData.icon
                            size: 17
                            color: tab.current ? Theme.accent : Theme.textDim
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            text: tab.modelData.label
                            color: tab.current ? Theme.accent : Theme.textDim
                            font.pixelSize: 13
                            font.weight: tab.current ? Font.DemiBold : Font.Medium
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Rectangle {
                            visible: tab.index === 1 && app.unreadAlerts > 0
                            width: Math.max(20, badgeText.implicitWidth + 10)
                            height: 20
                            radius: 10
                            color: Theme.alert
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                id: badgeText
                                anchors.centerIn: parent
                                text: app.unreadAlerts > 99 ? "99+" : app.unreadAlerts
                                color: "#1d1300"
                                font.pixelSize: 11
                                font.weight: Font.Bold
                            }
                        }
                    }
                    MouseArea {
                        id: tabArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { nav.forceActiveFocus(); bar.pageRequested(tab.index) }
                    }
                }
            }
        }

        Item { Layout.fillWidth: true }

        // Monitoring state
        Rectangle {
            id: statePill
            readonly property color tone: app.running ? (app.isSimulation ? Theme.sim : Theme.success)
                                                      : app.sourceText === "Paused" ? Theme.alert : Theme.muted
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: pillRow.implicitWidth + 24
            implicitHeight: 32
            radius: 16
            color: Util.tint(tone, 0.13)
            border.color: Util.tint(tone, 0.35)
            Row {
                id: pillRow
                anchors.centerIn: parent
                spacing: 8
                Item {
                    width: 10
                    height: 10
                    anchors.verticalCenter: parent.verticalCenter
                    Rectangle {
                        anchors.centerIn: parent
                        width: 10
                        height: 10
                        radius: 5
                        color: statePill.tone
                        opacity: 0.35
                        visible: app.running
                        // A heartbeat, not a constant pulse: an endless animation makes Qt redraw the whole
                        // window at 60 frames a second (about 15% of a core) even when nothing else changes.
                        // Frames are only drawn during the 0.6 s ping, once every 4 s.
                        SequentialAnimation on scale {
                            running: app.running
                            loops: Animation.Infinite
                            PauseAnimation { duration: 3400 }
                            NumberAnimation { from: 1; to: 2.1; duration: 600; easing.type: Easing.OutCubic }
                            NumberAnimation { to: 1; duration: 0 }
                        }
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 8
                        height: 8
                        radius: 4
                        color: statePill.tone
                    }
                }
                Text {
                    text: app.sourceText.toUpperCase()
                    color: statePill.tone
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    font.letterSpacing: 0.8
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        ACombo {
            id: modeBox
            Layout.preferredWidth: 196
            Layout.alignment: Qt.AlignVCenter
            model: app.modes
            enabled: !app.running
            currentIndex: Math.max(0, app.modes.indexOf(app.mode))
            onActivated: (i) => app.setMode(app.modes[i])
            Accessible.name: "Monitoring mode"
            ATip {
                visible: modeBox.hovered && !modeBox.popup.visible
                text: app.running ? "Stop monitoring to change the mode" : "Monitoring mode"
            }
        }

        AButton {
            id: runButton
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 164
            text: app.running ? "Stop monitoring" : "Start monitoring"
            iconName: app.running ? "stop" : "play"
            variant: app.running ? "dangerSoft" : "primary"
            onClicked: app.toggleMonitoring()
        }

        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: 26
            Layout.alignment: Qt.AlignVCenter
            color: Theme.border
        }

        IconButton {
            iconName: app.theme === "Dark" ? "sun" : "moon"
            tip: app.theme === "Dark" ? "Switch to light appearance" : "Switch to dark appearance"
            Layout.alignment: Qt.AlignVCenter
            onClicked: app.toggleTheme()
        }
        IconButton {
            iconName: "sliders"
            tip: "Settings"
            Layout.alignment: Qt.AlignVCenter
            onClicked: app.requestSettings("general")
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
    }
}
