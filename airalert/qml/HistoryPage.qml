import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

Item {
    id: page
    signal maintenanceRequested()
    property string category: "sightings"
    property bool searched: false
    readonly property bool sightings: category === "sightings"
    readonly property var selectedRow: table.currentRow >= 0 ? app.historyModel.get(table.currentRow) : ({})

    function stamp(date) { return Qt.formatDateTime(date, "yyyy-MM-dd HH:mm") }
    function preset(hours) {
        var now = new Date()
        toField.text = stamp(new Date(now.getTime() + 60 * 60 * 1000))
        fromField.text = hours > 0 ? stamp(new Date(now.getTime() - hours * 3600 * 1000)) : ""
        search()
    }
    function search() {
        table.clearSelection()
        app.searchHistory({ category: category, query: queryField.text, kind: kindBox.currentValue,
                            start: fromField.text, end: toField.text, maxDistance: sightings ? distanceField.text : "" })
        searched = true
    }

    Component.onCompleted: {
        var now = new Date()
        fromField.text = stamp(new Date(now.getTime() - 7 * 86400 * 1000))
        toField.text = stamp(new Date(now.getTime() + 86400 * 1000))
    }
    // Re-run the current search whenever the page is shown so new records appear.
    onVisibleChanged: if (visible) search()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        PageHeader {
            Layout.fillWidth: true
            title: "History"
            subtitle: "Search recorded sightings and the event timeline stored on this computer. Double-click a sighting to replay its track on the map."
            AButton { text: "Database maintenance"; iconName: "database"; onClicked: page.maintenanceRequested() }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: filters.implicitHeight + 32
            radius: Theme.radius
            color: Theme.panel
            border.color: Theme.border
            ColumnLayout {
                id: filters
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Segmented {
                        options: [{ value: "sightings", label: "Sightings" }, { value: "alert", label: "Alerts" },
                                  { value: "detection", label: "Detections" }, { value: "receiver", label: "Receiver" },
                                  { value: "application", label: "App events" }]
                        value: page.category
                        onPicked: (v) => { page.category = v; page.search() }
                    }
                    Item { Layout.fillWidth: true }
                    Text { text: "Quick range"; color: Theme.muted; font.pixelSize: 12 }
                    Repeater {
                        model: [{ label: "1 h", hours: 1 }, { label: "24 h", hours: 24 }, { label: "7 d", hours: 168 },
                                { label: "30 d", hours: 720 }, { label: "All", hours: 0 }]
                        delegate: AButton {
                            required property var modelData
                            text: modelData.label
                            compact: true
                            variant: "ghost"
                            onClicked: page.preset(modelData.hours)
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    AField {
                        id: queryField
                        Layout.fillWidth: true
                        iconName: "search"
                        clearable: true
                        placeholderText: page.sightings ? "ICAO, MMSI, registration, callsign or name" : "Event text or target key"
                        onAccepted: page.search()
                    }
                    ACombo {
                        id: kindBox
                        visible: page.sightings
                        Layout.preferredWidth: 130
                        model: [{ value: "any", label: "All targets" }, { value: "aircraft", label: "Aircraft" }, { value: "vessel", label: "Vessels" }]
                        textRole: "label"
                        valueRole: "value"
                        onActivated: page.search()
                    }
                    AField {
                        id: fromField
                        Layout.preferredWidth: 158
                        iconName: "clock"
                        placeholderText: "From (any time)"
                        onAccepted: page.search()
                    }
                    Text { text: "to"; color: Theme.muted; font.pixelSize: 13 }
                    AField {
                        id: toField
                        Layout.preferredWidth: 150
                        placeholderText: "To (now)"
                        onAccepted: page.search()
                    }
                    AField {
                        id: distanceField
                        visible: page.sightings
                        Layout.preferredWidth: 140
                        placeholderText: "Max distance"
                        suffix: app.units
                        validator: DoubleValidator { bottom: 0 }
                        onAccepted: page.search()
                    }
                    AButton { text: "Search"; iconName: "search"; variant: "primary"; onClicked: page.search() }
                }
            }
        }

        DataTable {
            id: table
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: app.historyModel
            emptyIcon: "clock"
            emptyTitle: page.searched ? "No matching records" : "Search your archive"
            emptyText: page.searched ? "Try a wider date range or a different category. Times are local." : ""
            columns: page.sightings
                     ? [{ title: "Identity", role: "title", width: 0.36, strong: true }, { title: "Type", role: "kind", width: 0.12, format: "kind" },
                        { title: "First seen", role: "firstText", width: 0.19 }, { title: "Last seen", role: "lastText", width: 0.19 },
                        { title: "Source", role: "source", width: 0.14 }]
                     : [{ title: "Event", role: "title", width: 0.54, strong: true }, { title: "Category", role: "category", width: 0.13 },
                        { title: "Time", role: "firstText", width: 0.19 }, { title: "Source", role: "source", width: 0.14 }]
            onActivated: (row) => app.openTrack(app.historyModel.get(row).row)
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            AButton {
                text: "Open track on map"
                iconName: "route"
                variant: "primary"
                enabled: page.selectedRow.isSighting === true
                onClicked: app.openTrack(page.selectedRow.row)
            }
            AButton {
                text: "Export results"
                iconName: "download"
                onClicked: app.exportResults()
            }
            AButton {
                text: "Export track (CSV, KML)…"
                iconName: "download"
                enabled: page.selectedRow.isSighting === true
                onClicked: app.exportTrack(page.selectedRow.row)
            }
            AButton {
                id: replayButton
                text: playback.loading ? "Loading…" : "Replay period on map"
                iconName: "play"
                enabled: !playback.loading
                onClicked: playback.openRange(fromField.text, toField.text, true)
                ATip {
                    visible: replayButton.hovered
                    text: "Replay all traffic recorded between the From and To times on the live map"
                }
            }
            Item { Layout.fillWidth: true }
            Text {
                text: app.historyInfo
                color: Theme.muted
                font.pixelSize: 12
                elide: Text.ElideRight
                Layout.maximumWidth: 520
            }
        }
    }
}
