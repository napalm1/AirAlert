import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

// Local aircraft database: what is installed, CSV imports and the optional OpenSky update.
Rectangle {
    id: card
    readonly property var db: insights.aircraftDb
    readonly property bool busy: insights.updateBusy
    readonly property string state_: insights.updateState
    implicitHeight: body.implicitHeight + 32
    radius: Theme.radius
    color: Theme.panel
    border.color: Theme.border

    ColumnLayout {
        id: body
        x: 16
        y: 16
        width: parent.width - 32
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            Rectangle {
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                Layout.alignment: Qt.AlignTop
                radius: 12
                color: Theme.accentSoft
                Icon { anchors.centerIn: parent; name: "database"; size: 20; color: Theme.accent }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                RowLayout {
                    spacing: 10
                    Text {
                        text: "Aircraft database"
                        color: Theme.text
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }
                    Chip {
                        text: card.db.ok ? card.db.sourceShort : "Not installed"
                        tone: card.db.ok ? (card.db.sourceShort === "OpenSky Network" ? Theme.success : Theme.accent) : Theme.alert
                    }
                }
                Text {
                    objectName: "aircraftDbSummary"
                    Layout.fillWidth: true
                    text: card.db.ok
                          ? Util.number(card.db.count) + " aircraft" + (card.db.dataDate ? " · data from " + card.db.dataDate : "")
                            + (card.db.built ? " · built " + card.db.built : "") + " · " + Util.number(card.db.sizeMb, 1) + " MB"
                          : "No aircraft database is installed. Update from OpenSky or import a CSV."
                    color: Theme.textDim
                    font.pixelSize: 13
                    font.features: { "tnum": 1 }
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    text: "Registration, type, model, manufacturer and operator are looked up on this computer for aircraft you receive."
                          + (card.db.userCount > 0 ? " Your " + Util.number(card.db.userCount) + " imported CSV mapping" + (card.db.userCount === 1 ? "" : "s") + " take precedence." : "")
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
            AButton {
                objectName: "importCsvButton"
                Layout.alignment: Qt.AlignTop
                text: "Import CSV…"
                iconName: "upload"
                enabled: !card.busy
                onClicked: {
                    app.importAircraftCsv()
                    insights.refreshAircraftDb()
                }
            }
            AButton {
                objectName: "openSkyButton"
                Layout.alignment: Qt.AlignTop
                text: "Update from OpenSky"
                iconName: "download"
                enabled: !card.busy
                onClicked: insights.updateAircraftDb()
            }
        }

        // Progress while downloading or building
        RowLayout {
            Layout.fillWidth: true
            visible: card.busy
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: insights.updateText
                    color: Theme.textDim
                    font.pixelSize: 12
                    font.features: { "tnum": 1 }
                }
                Rectangle {
                    objectName: "aircraftDbProgress"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 6
                    radius: 3
                    color: Theme.accentSoft
                    Rectangle {
                        width: parent.width * insights.updateProgress
                        height: parent.height
                        radius: 3
                        color: Theme.accent
                        Behavior on width { NumberAnimation { duration: 180 } }
                    }
                }
            }
            AButton {
                objectName: "cancelUpdateButton"
                text: "Cancel"
                variant: "ghost"
                compact: true
                onClicked: insights.cancelAircraftUpdate()
            }
        }

        // Result of the last update
        RowLayout {
            Layout.fillWidth: true
            visible: !card.busy && insights.updateText !== ""
            spacing: 8
            Icon {
                name: card.state_ === "error" ? "warning" : card.state_ === "done" ? "check" : "info"
                size: 15
                color: card.state_ === "error" ? Theme.danger : card.state_ === "done" ? Theme.success : Theme.muted
            }
            Text {
                Layout.fillWidth: true
                text: insights.updateText
                color: card.state_ === "error" ? Theme.danger : Theme.textDim
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Icon { name: "info"; size: 15; color: Theme.muted; Layout.alignment: Qt.AlignTop; Layout.topMargin: 1 }
            Text {
                Layout.fillWidth: true
                text: "<b>Update from OpenSky</b> downloads the OpenSky Network's public aircraft database (a zip file of about 25 MB) from s3.opensky-network.org, only when you click it. Nothing about your station, location or traffic is sent. "
                      + "<b>Import CSV</b> adds mappings you are permitted to use, with columns <b>icao</b> (or icao24), <b>registration</b> and <b>type</b> (or typecode)."
                textFormat: Text.StyledText
                color: Theme.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }
    }
}
