import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

// History playback controls. Takes the latest-alert ribbon's place while replaying.
GlassPanel {
    id: bar
    readonly property bool shown: playback.active || playback.loading
    readonly property color tone: Theme.alert
    readonly property color ink: Theme.dark ? "#1d1300" : "#ffffff"
    readonly property bool roomy: width >= 780
    visible: shown
    height: 106
    borderColor: Util.tint(tone, 0.6)

    function typing() {
        var item = bar.Window.activeFocusItem
        return item !== null && item !== undefined && item.cursorPosition !== undefined
    }
    function modal() {
        var root = bar.Window.window
        return root !== null && root !== undefined && root.modalOpen === true
    }

    // Space plays / pauses unless a text field has focus or a dialog is open.
    Shortcut {
        sequence: "Space"
        enabled: playback.active && bar.visible && !bar.typing() && !bar.modal()
        onActivated: playback.togglePlay()
    }

    // Amber edge so replayed traffic is never mistaken for live traffic.
    Rectangle {
        x: 1
        y: 10
        width: 3
        height: parent.height - 20
        radius: 1.5
        color: bar.tone
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 10
        anchors.topMargin: 10
        anchors.bottomMargin: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Rectangle {
                Layout.preferredHeight: 22
                Layout.preferredWidth: badge.implicitWidth + 18
                radius: 11
                color: bar.tone
                Row {
                    id: badge
                    anchors.centerIn: parent
                    spacing: 5
                    Icon { name: "clock"; size: 12; stroke: 2.4; color: bar.ink; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: "HISTORY PLAYBACK"
                        color: bar.ink
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        font.letterSpacing: 1.1
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: playback.loading ? "Loading recorded positions…" : playback.rangeText
                color: Theme.textDim
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Chip {
                text: playback.limitText
                tone: Theme.alert
                iconName: "warning"
            }
            Text {
                visible: playback.active
                text: playback.targetCount + (playback.targetCount === 1 ? " target on map" : " targets on map")
                color: Theme.muted
                font.pixelSize: 12
                font.features: { "tnum": 1 }
            }
            AButton {
                text: "Exit playback"
                iconName: "close"
                compact: true
                variant: "ghost"
                onClicked: playback.close()
            }
        }

        // Loading: an indeterminate progress sweep.
        Item {
            visible: playback.loading
            Layout.fillWidth: true
            Layout.fillHeight: true
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width
                height: 4
                radius: 2
                color: Theme.border
                clip: true
                Rectangle {
                    id: sweep
                    width: parent.width * 0.3
                    height: parent.height
                    radius: 2
                    color: bar.tone
                    NumberAnimation on x {
                        running: playback.loading
                        loops: Animation.Infinite
                        from: -sweep.width
                        to: sweep.parent.width
                        duration: 1100
                        easing.type: Easing.InOutQuad
                    }
                }
            }
        }

        RowLayout {
            visible: playback.active
            Layout.fillWidth: true
            spacing: 10

            Button {
                id: playButton
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                hoverEnabled: true
                Accessible.name: playback.playing ? "Pause" : "Play"
                onClicked: playback.togglePlay()
                background: Rectangle {
                    radius: 21
                    color: playButton.down ? Qt.darker(bar.tone, 1.15) : playButton.hovered ? Qt.lighter(bar.tone, 1.08) : bar.tone
                    border.width: playButton.visualFocus ? 2 : 0
                    border.color: Theme.text
                }
                contentItem: Item {
                    Row {
                        visible: playback.playing
                        anchors.centerIn: parent
                        spacing: 4
                        Rectangle { width: 4; height: 14; radius: 1; color: bar.ink }
                        Rectangle { width: 4; height: 14; radius: 1; color: bar.ink }
                    }
                    Icon {
                        visible: !playback.playing
                        anchors.centerIn: parent
                        anchors.horizontalCenterOffset: 1.5
                        name: "play"
                        size: 20
                        color: bar.ink
                    }
                }
                ATip { visible: playButton.hovered; text: (playback.playing ? "Pause" : "Play") + " (Space)" }
            }
            IconButton {
                visible: bar.roomy
                iconName: "chevronLeft"
                tip: "Back 1 minute"
                size: 32
                iconSize: 16
                onClicked: playback.step(-60)
            }
            IconButton {
                visible: bar.roomy
                iconName: "chevronRight"
                tip: "Forward 1 minute"
                size: 32
                iconSize: 16
                onClicked: playback.step(60)
            }
            Column {
                Layout.preferredWidth: 112
                spacing: 0
                Text {
                    text: playback.timeText
                    color: Theme.text
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }
                Text {
                    text: playback.dateText
                    color: Theme.muted
                    font.pixelSize: 11
                }
            }
            Slider {
                id: seek
                Layout.fillWidth: true
                Layout.minimumWidth: 90
                from: playback.start
                to: Math.max(playback.start + 1, playback.end)
                stepSize: 0
                hoverEnabled: true
                Accessible.name: "Playback position"
                onMoved: playback.seek(value)
                Binding {
                    target: seek
                    property: "value"
                    value: playback.time
                    when: !seek.pressed
                    restoreMode: Binding.RestoreNone
                }
                background: Rectangle {
                    x: seek.leftPadding
                    y: seek.topPadding + seek.availableHeight / 2 - height / 2
                    width: seek.availableWidth
                    height: 5
                    radius: 2.5
                    color: Theme.border
                    Rectangle {
                        width: seek.visualPosition * parent.width
                        height: parent.height
                        radius: 2.5
                        color: bar.tone
                    }
                }
                handle: Rectangle {
                    x: seek.leftPadding + seek.visualPosition * (seek.availableWidth - width)
                    y: seek.topPadding + seek.availableHeight / 2 - height / 2
                    width: 18
                    height: 18
                    radius: 9
                    color: "#ffffff"
                    border.color: bar.tone
                    border.width: seek.pressed || seek.hovered ? 3 : 2
                }
            }
            Segmented {
                options: playback.speeds.map(function(s) { return { value: String(s), label: s + "×" } })
                value: String(playback.speed)
                onPicked: (v) => playback.setSpeed(parseInt(v))
            }
        }
    }
}
