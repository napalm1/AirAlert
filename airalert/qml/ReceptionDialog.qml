import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

// Reception check: listen at a ladder of gains and recommend the best one.
// Open from anywhere with reception.open(); closing the dialog cancels a running check.
AModal {
    id: dlg
    readonly property bool busy: reception.running
    readonly property bool aircraft: reception.mode === "adsb"
    readonly property var rec: reception.recommendation
    readonly property bool hasRec: rec !== undefined && rec !== null && rec.gain !== undefined
    readonly property bool ran: busy || hasRec || reception.progress > 0 || reception.errorText !== ""
    // While monitoring runs, the banner at the top explains why the check can't start.
    readonly property bool showProblem: reception.errorText !== "" && !busy && !reception.monitoring
    readonly property color tone: busy ? Theme.accent
                                : hasRec ? Theme.success
                                : showProblem ? (reception.noSignal ? Theme.alert : Theme.danger)
                                : Theme.muted
    readonly property int gainColumn: 160
    readonly property int numberColumn: 58
    readonly property var tips: [
        { icon: "antenna", title: "Height beats gain",
          body: "Put the antenna as high as you can, outdoors or at a window, away from metal, walls and electronics. Keep the cable short." },
        { icon: dlg.aircraft ? "plane" : "ship", title: "Match the antenna to the band",
          body: "Aircraft use 1090 MHz: a short (about 6.9 cm) whip or collinear. AIS needs a marine VHF antenna for 162 MHz; a 1090 MHz antenna hears little AIS." },
        { icon: "monitor", title: "Close other SDR apps",
          body: "Only one program can use the receiver. Close SDR# and similar tools, and stop monitoring here first." },
        { icon: "moon", title: "Traffic comes and goes",
          body: "Few aircraft fly at night and harbors can be quiet. Run the check at a busy time for the fairest comparison." },
        { icon: "sliders", title: "More gain isn't always better",
          body: "Too much gain lets strong nearby signals overload the receiver and corrupt messages. The check finds the balance." }
    ]

    title: "Reception check"
    subtitle: "Listen at several receiver gains and keep the one that decodes the most traffic. A quiet result points to antenna problems."
    iconName: "signal"
    width: Math.min(1040, Overlay.overlay ? Overlay.overlay.width - 40 : 1040)
    height: Math.min(Overlay.overlay ? Overlay.overlay.height - 40 : 820, 820)

    onClosed: if (reception.running) reception.cancel()

    Connections {
        target: reception
        function onOpenRequested() { dlg.open() }
    }

    component Caption: Text {
        color: Theme.muted
        font.pixelSize: 10
        font.weight: Font.Bold
        font.letterSpacing: 0.9
    }
    component Figure: Text {
        Layout.preferredWidth: dlg.numberColumn
        horizontalAlignment: Text.AlignRight
        color: Theme.text
        font.pixelSize: 13
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Flickable {
            id: mainArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentHeight: column.implicitHeight + 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            ColumnLayout {
                id: column
                x: 24
                y: 18
                width: mainArea.width - 48
                spacing: 14

                // Monitoring holds the receiver: explain and offer to stop it.
                Rectangle {
                    visible: reception.monitoring
                    Layout.fillWidth: true
                    implicitHeight: 52
                    radius: 11
                    color: Util.tint(Theme.alert, 0.10)
                    border.color: Util.tint(Theme.alert, 0.38)
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 8
                        spacing: 12
                        Icon { name: "warning"; size: 18; color: Theme.alert }
                        Text {
                            Layout.fillWidth: true
                            text: "Monitoring is running. Stop it to test the receiver: only one program can use it at a time."
                            color: Theme.text
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                        }
                        AButton { text: "Stop monitoring"; iconName: "stop"; compact: true; onClicked: app.stop() }
                    }
                }

                // Setup
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 18
                    FormRow {
                        Layout.fillWidth: false
                        label: "Band"
                        Segmented {
                            options: [{ value: "adsb", label: "Aircraft · 1090 MHz" }, { value: "ais", label: "Marine AIS · 162 MHz" }]
                            value: reception.mode
                            enabled: !dlg.busy
                            opacity: enabled ? 1 : 0.55
                            onPicked: (v) => reception.mode = v
                        }
                    }
                    FormRow {
                        Layout.fillWidth: false
                        label: "Listen at each gain"
                        Segmented {
                            options: reception.secondsChoices.map((s) => ({ value: String(s), label: s + " s" }))
                            value: String(reception.secondsPerGain)
                            enabled: !dlg.busy
                            opacity: enabled ? 1 : 0.55
                            onPicked: (v) => reception.secondsPerGain = parseInt(v)
                        }
                    }
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        Layout.alignment: Qt.AlignBottom
                        implicitWidth: Math.max(112, gainReadout.implicitWidth + 28)
                        implicitHeight: 53
                        radius: 11
                        color: Theme.raised
                        border.color: Theme.border
                        Column {
                            id: gainReadout
                            anchors.centerIn: parent
                            spacing: 2
                            Caption { text: "CURRENT GAIN" }
                            Text { text: reception.currentGainLabel; color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
                        }
                    }
                }

                // Status and progress
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: progressColumn.implicitHeight + 26
                    radius: 12
                    color: Theme.raised
                    border.color: Theme.border
                    ColumnLayout {
                        id: progressColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 13
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        spacing: 9
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Item {
                                Layout.preferredWidth: 12
                                Layout.preferredHeight: 12
                                Rectangle {
                                    anchors.centerIn: parent
                                    visible: dlg.busy
                                    width: 12
                                    height: width
                                    radius: width / 2
                                    color: Theme.accent
                                    NumberAnimation on width { running: dlg.busy; loops: Animation.Infinite; from: 10; to: 26; duration: 1300; easing.type: Easing.OutCubic }
                                    NumberAnimation on opacity { running: dlg.busy; loops: Animation.Infinite; from: 0.55; to: 0; duration: 1300; easing.type: Easing.OutCubic }
                                }
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 10
                                    height: 10
                                    radius: 5
                                    color: dlg.tone
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: reception.statusText
                                color: Theme.text
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            Text {
                                visible: dlg.busy || reception.progress > 0
                                text: Math.round(reception.progress * 100) + "%"
                                color: Theme.textDim
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 6
                            radius: 3
                            color: Util.tint(Theme.text, 0.08)
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, reception.progress))
                                height: parent.height
                                radius: 3
                                color: dlg.hasRec || dlg.showProblem ? dlg.tone : Theme.accent
                                Behavior on width { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: reception.stepText !== "" ? reception.stepText
                                  : "Each gain is tested with the same decoder the app uses. Monitoring must be stopped while the check runs."
                            color: Theme.muted
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // Recommendation
                Rectangle {
                    visible: dlg.hasRec && !dlg.busy
                    Layout.fillWidth: true
                    implicitHeight: recLayout.implicitHeight + 26
                    radius: 14
                    color: Util.tint(Theme.success, Theme.dark ? 0.10 : 0.08)
                    border.color: Util.tint(Theme.success, 0.5)
                    RowLayout {
                        id: recLayout
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        spacing: 16
                        Rectangle {
                            Layout.preferredWidth: 46
                            Layout.preferredHeight: 46
                            Layout.alignment: Qt.AlignTop
                            radius: 13
                            color: Util.tint(Theme.success, 0.18)
                            Icon { anchors.centerIn: parent; name: "signal"; size: 22; stroke: 2.4; color: Theme.success }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Caption { text: "RECOMMENDED GAIN"; color: Theme.success }
                            Row {
                                spacing: 10
                                Text {
                                    text: dlg.hasRec ? dlg.rec.label : ""
                                    color: Theme.text
                                    font.pixelSize: 24
                                    font.weight: Font.DemiBold
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Chip {
                                    visible: dlg.hasRec && dlg.rec.weak === true
                                    text: "Few messages"
                                    iconName: "warning"
                                    tone: Theme.alert
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: dlg.hasRec ? dlg.rec.text : ""
                                color: Theme.textDim
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                                lineHeight: 1.15
                            }
                        }
                        ColumnLayout {
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 6
                            AButton {
                                Layout.alignment: Qt.AlignRight
                                visible: dlg.hasRec && dlg.rec.current !== true
                                text: "Use this gain"
                                iconName: "check"
                                variant: "primary"
                                enabled: reception.canApply
                                onClicked: reception.apply()
                            }
                            Chip {
                                Layout.alignment: Qt.AlignRight
                                visible: dlg.hasRec && dlg.rec.current === true
                                text: "In use"
                                iconName: "check"
                                tone: Theme.success
                            }
                            Text {
                                Layout.alignment: Qt.AlignRight
                                visible: dlg.hasRec && dlg.rec.current !== true
                                text: "Applies to aircraft and marine"
                                color: Theme.muted
                                font.pixelSize: 11
                            }
                        }
                    }
                }

                // Problems: receiver errors, or nothing heard at any gain
                Rectangle {
                    id: problem
                    visible: dlg.showProblem
                    readonly property color tone: reception.noSignal ? Theme.alert : Theme.danger
                    Layout.fillWidth: true
                    implicitHeight: problemLayout.implicitHeight + 28
                    radius: 12
                    color: Util.tint(tone, 0.09)
                    border.color: Util.tint(tone, 0.4)
                    RowLayout {
                        id: problemLayout
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        spacing: 14
                        Rectangle {
                            Layout.preferredWidth: 36
                            Layout.preferredHeight: 36
                            Layout.alignment: Qt.AlignTop
                            radius: 10
                            color: Util.tint(problem.tone, 0.16)
                            Icon { anchors.centerIn: parent; name: reception.noSignal ? "antenna" : "warning"; size: 19; color: problem.tone }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Text {
                                text: reception.noSignal ? "No signals heard" : "The check needs attention"
                                color: problem.tone
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                            Text {
                                Layout.fillWidth: true
                                text: reception.errorText
                                color: Theme.text
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                                lineHeight: 1.15
                            }
                        }
                    }
                }

                // Results table
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: table.implicitHeight + 12
                    radius: 12
                    color: Theme.panel
                    border.color: Theme.border
                    ColumnLayout {
                        id: table
                        x: 6
                        y: 6
                        width: parent.width - 12
                        spacing: 1
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 28
                            Layout.leftMargin: 12
                            Layout.rightMargin: 12
                            spacing: 14
                            Caption { text: "GAIN"; Layout.preferredWidth: dlg.gainColumn }
                            Caption { text: "VALID MESSAGES"; Layout.fillWidth: true }
                            Caption { text: "FRAMES"; Layout.preferredWidth: dlg.numberColumn; horizontalAlignment: Text.AlignRight }
                            Caption { text: dlg.aircraft ? "AIRCRAFT" : "VESSELS"; Layout.preferredWidth: dlg.numberColumn; horizontalAlignment: Text.AlignRight }
                            Caption { text: "POSITIONS"; Layout.preferredWidth: dlg.numberColumn; horizontalAlignment: Text.AlignRight }
                            Caption { text: "SCORE"; Layout.preferredWidth: dlg.numberColumn; horizontalAlignment: Text.AlignRight }
                        }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.bottomMargin: 2; color: Theme.border }
                        Repeater {
                            model: reception.resultsModel
                            delegate: Rectangle {
                                id: resultRow
                                required property string gain
                                required property string label
                                required property int frames
                                required property int valid
                                required property int targets
                                required property int positions
                                required property int score
                                required property bool best
                                required property bool current
                                required property string phase
                                required property real share
                                readonly property bool active: phase === "active"
                                readonly property bool measured: phase === "done" || (active && frames > 0)
                                readonly property string noteText: phase === "skipped" ? "not tested"
                                                                  : phase === "pending" ? (dlg.busy ? "waiting" : "")
                                                                  : active && valid === 0 ? "listening…"
                                                                  : phase === "done" && valid === 0 ? "nothing decoded" : ""
                                Layout.fillWidth: true
                                Layout.preferredHeight: 36
                                radius: 9
                                color: best ? Util.tint(Theme.success, Theme.dark ? 0.13 : 0.10)
                                            : active ? Theme.accentSoft : "transparent"
                                border.color: best ? Util.tint(Theme.success, 0.42) : "transparent"
                                Behavior on color { ColorAnimation { duration: 200 } }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 12
                                    anchors.rightMargin: 12
                                    spacing: 14
                                    Row {
                                        Layout.preferredWidth: dlg.gainColumn
                                        spacing: 8
                                        Text {
                                            text: resultRow.label
                                            color: resultRow.phase === "pending" || resultRow.phase === "skipped" ? Theme.textDim : Theme.text
                                            font.pixelSize: 14
                                            font.weight: Font.DemiBold
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        Chip { visible: resultRow.best; text: "Best"; tone: Theme.success; anchors.verticalCenter: parent.verticalCenter }
                                        Chip { visible: resultRow.current; text: "In use"; tone: Theme.textDim; anchors.verticalCenter: parent.verticalCenter }
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        Rectangle {
                                            id: track
                                            visible: resultRow.noteText === ""
                                            anchors.left: parent.left
                                            anchors.right: validFigure.left
                                            anchors.rightMargin: 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            height: 8
                                            radius: 4
                                            color: Util.tint(Theme.text, 0.07)
                                            Rectangle {
                                                width: resultRow.valid > 0 ? Math.max(6, track.width * resultRow.share) : 0
                                                height: parent.height
                                                radius: 4
                                                color: resultRow.best ? Theme.success : resultRow.active ? Theme.accent : Util.tint(Theme.accent, 0.62)
                                                Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                                            }
                                        }
                                        Text {
                                            visible: resultRow.noteText !== ""
                                            anchors.left: parent.left
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: resultRow.noteText
                                            color: resultRow.active ? Theme.accent : Theme.muted
                                            font.pixelSize: 12
                                            font.italic: true
                                        }
                                        Text {
                                            id: validFigure
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            width: 56
                                            horizontalAlignment: Text.AlignRight
                                            text: resultRow.measured ? Util.number(resultRow.valid) : "—"
                                            color: resultRow.measured ? Theme.text : Theme.muted
                                            font.pixelSize: 13
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                    Figure { text: resultRow.measured ? Util.number(resultRow.frames) : "—"; color: resultRow.measured ? Theme.textDim : Theme.muted }
                                    Figure { text: resultRow.measured ? Util.number(resultRow.targets) : "—"; color: resultRow.measured ? Theme.text : Theme.muted }
                                    Figure { text: resultRow.measured ? Util.number(resultRow.positions) : "—"; color: resultRow.measured ? Theme.textDim : Theme.muted }
                                    Figure {
                                        text: resultRow.phase === "done" ? Util.number(resultRow.score) : "—"
                                        color: resultRow.best ? Theme.success : resultRow.phase === "done" ? Theme.text : Theme.muted
                                        font.weight: Font.DemiBold
                                    }
                                }
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: "Score = valid messages + positions + 10 per " + (dlg.aircraft ? "aircraft" : "vessel")
                          + ". Ties go to the lower gain. Frames also count corrupted messages the decoder rejected."
                    color: Theme.muted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
            }
        }

        Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.border }

        // Tips
        Rectangle {
            Layout.preferredWidth: 270
            Layout.fillHeight: true
            color: Theme.raised
            Flickable {
                id: tipsArea
                anchors.fill: parent
                contentHeight: tipsColumn.implicitHeight + 40
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ColumnLayout {
                    id: tipsColumn
                    x: 20
                    y: 20
                    width: tipsArea.width - 40
                    spacing: 16
                    Caption { text: "TIPS FOR BETTER RECEPTION" }
                    Repeater {
                        model: dlg.tips
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 12
                            Rectangle {
                                Layout.preferredWidth: 30
                                Layout.preferredHeight: 30
                                Layout.alignment: Qt.AlignTop
                                radius: 9
                                color: Theme.accentSoft
                                Icon { anchors.centerIn: parent; name: modelData.icon; size: 16; color: Theme.accent }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title
                                    color: Theme.text
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.body
                                    color: Theme.textDim
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                    lineHeight: 1.15
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    footer: [
        Text {
            Layout.fillWidth: true
            text: dlg.busy ? "The receiver is busy with the check. Closing this window cancels it."
                           : "Takes " + reception.estimateText + ". The receiver is used exclusively while the check runs."
            color: Theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
        },
        AButton { text: "Close"; variant: "ghost"; onClicked: dlg.close() },
        AButton {
            visible: dlg.busy
            text: "Cancel check"
            iconName: "stop"
            variant: "dangerSoft"
            onClicked: reception.cancel()
        },
        AButton {
            visible: !dlg.busy
            text: dlg.ran ? "Run again" : "Start check"
            iconName: "play"
            variant: "primary"
            enabled: !reception.monitoring
            onClicked: reception.start(reception.mode, reception.secondsPerGain)
        }
    ]
}
