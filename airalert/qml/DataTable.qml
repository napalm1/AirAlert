import QtQuick
import QtQuick.Controls.Basic
import "Util.js" as Util

// Sortable table over a SortProxy. columns: [{title, role, width (fraction), align, format}]
Rectangle {
    id: table
    property var model
    property var columns: []
    property int currentRow: -1
    property string currentKey: ""

    function clearSelection() {
        currentKey = ""
        currentRow = -1
    }

    // Re-sorting moves rows; keep the same record selected.
    Connections {
        target: table.model
        function onSortChanged() {
            table.currentRow = table.currentKey !== "" ? table.model.rowOfKey(table.currentKey) : -1
        }
    }
    property string emptyTitle: "Nothing here yet"
    property string emptyText: ""
    property string emptyIcon: "list"
    property Component actions: null
    property real actionsWidth: 0
    readonly property real contentWidth: width - 24 - actionsWidth
    signal activated(int row)
    signal rowClicked(int row)
    radius: Theme.radius
    color: Theme.panel
    border.color: Theme.border
    clip: true

    function cell(value, format) {
        if (value === undefined || value === null || value === "")
            return "—"
        if (format === "number")
            return Util.number(value)
        if (format === "kind")
            return value === "aircraft" ? "Aircraft" : value === "vessel" ? "Vessel" : value
        return String(value)
    }

    Rectangle {
        id: header
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 1
        height: 40
        radius: Theme.radius
        color: Theme.raised
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: parent.radius; color: parent.color }
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Theme.border }
        Row {
            x: 11
            height: parent.height
            Repeater {
                model: table.columns
                delegate: Item {
                    id: head
                    required property var modelData
                    readonly property bool sorted: table.model && table.model.sortKey === modelData.role
                    width: table.contentWidth * modelData.width
                    height: header.height
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: head.modelData.align === "right" ? parent.right : undefined
                        anchors.rightMargin: 10
                        spacing: 4
                        Text {
                            text: head.modelData.title.toUpperCase()
                            color: head.sorted ? Theme.accent : Theme.muted
                            font.pixelSize: 11
                            font.weight: Font.Bold
                            font.letterSpacing: 0.9
                        }
                        Icon {
                            visible: head.sorted
                            name: table.model && table.model.ascending ? "arrowUp" : "arrowDown"
                            size: 12
                            stroke: 2.4
                            color: Theme.accent
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (table.model) table.model.sortBy(head.modelData.role)
                    }
                }
            }
        }
    }

    ListView {
        id: list
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 1
        anchors.topMargin: 0
        clip: true
        model: table.model
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        onCountChanged: if (table.currentRow >= count) table.clearSelection()
        delegate: Rectangle {
            id: row
            required property int index
            required property var model
            width: list.width
            height: 42
            color: table.currentRow === index ? Theme.accentSoft : rowArea.containsMouse ? Theme.hover
                   : index % 2 === 1 ? Util.tint(Theme.raised, Theme.dark ? 0.45 : 0.7) : "transparent"
            MouseArea {
                id: rowArea
                anchors.fill: parent
                hoverEnabled: true
                onClicked: { table.currentKey = row.model.key; table.currentRow = row.index; table.rowClicked(row.index) }
                onDoubleClicked: { table.currentKey = row.model.key; table.currentRow = row.index; table.activated(row.index) }
            }
            Row {
                x: 11
                height: parent.height
                Repeater {
                    model: table.columns
                    delegate: Item {
                        required property var modelData
                        width: table.contentWidth * modelData.width
                        height: row.height
                        Text {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: table.cell(row.model[modelData.role], modelData.format)
                            color: modelData.strong ? Theme.text : Theme.textDim
                            font.pixelSize: 13
                            font.weight: modelData.strong ? Font.DemiBold : Font.Normal
                            font.features: { "tnum": 1 }
                            horizontalAlignment: modelData.align === "right" ? Text.AlignRight : Text.AlignLeft
                            elide: Text.ElideRight
                        }
                    }
                }
            }
            Loader {
                id: rowActions
                property var rowData: row.model
                property int rowIndex: row.index
                active: table.actions !== null
                sourceComponent: table.actions
                width: table.actionsWidth
                height: parent.height
                anchors.right: parent.right
                anchors.rightMargin: 8
            }
        }

        Column {
            visible: list.count === 0
            anchors.centerIn: parent
            width: Math.min(parent.width - 40, 420)
            spacing: 8
            Icon { name: table.emptyIcon; size: 30; color: Theme.muted; anchors.horizontalCenter: parent.horizontalCenter }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: table.emptyTitle
                color: Theme.text
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: table.emptyText
                visible: text !== ""
                color: Theme.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }
    }
}
