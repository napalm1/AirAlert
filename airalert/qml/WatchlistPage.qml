import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

Item {
    id: page
    onVisibleChanged: if (visible) insights.refreshAircraftDb()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        PageHeader {
            Layout.fillWidth: true
            title: "Watchlist"
            subtitle: "Targets you care about get a ★ on the map and in lists. Encounters are counted from your local history."
            AButton { text: "Add entry"; iconName: "plus"; variant: "primary"; onClicked: app.newWatch() }
        }

        DataTable {
            id: table
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: app.watchModel
            emptyIcon: "starLine"
            emptyTitle: "Your watchlist is empty"
            emptyText: "Add an ICAO address, registration or MMSI — or select a target on the live map and choose Watch."
            actionsWidth: 124
            columns: [{ title: "Name", role: "name", width: 0.21, strong: true }, { title: "Identifier", role: "identifier", width: 0.15 },
                      { title: "Type", role: "kind", width: 0.1, format: "kind" }, { title: "Notes", role: "notes", width: 0.24 },
                      { title: "Last seen", role: "lastSeenText", width: 0.18 }, { title: "Encounters", role: "sightings", width: 0.12, format: "number" }]
            onActivated: (row) => app.editWatch(app.watchModel.get(row).row)
            actions: Component {
                Row {
                    readonly property var entry: parent ? parent.rowData : ({})
                    spacing: 2
                    layoutDirection: Qt.RightToLeft
                    IconButton { iconName: "trash"; tip: "Remove from watchlist"; size: 32; iconSize: 16; tint: Theme.danger; anchors.verticalCenter: parent.verticalCenter; onClicked: app.deleteWatch(parent.entry.row) }
                    IconButton { iconName: "edit"; tip: "Edit entry"; size: 32; iconSize: 16; anchors.verticalCenter: parent.verticalCenter; onClicked: app.editWatch(parent.entry.row) }
                    IconButton { iconName: "bell"; tip: "Create an alert for this entry"; size: 32; iconSize: 16; anchors.verticalCenter: parent.verticalCenter; onClicked: app.alertForWatch(parent.entry.row) }
                }
            }
        }

        // ADS-B rarely broadcasts registrations; the local aircraft database (and CSV imports) fill them in.
        AircraftDbCard {
            objectName: "aircraftDbCard"
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
        }
    }
}
