import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Frame for one chart: title, subtitle, legend slot, empty state and an accessible table view.
Rectangle {
    id: card
    property string title: ""
    property string subtitle: ""
    property string footnote: ""
    property bool empty: false
    property string emptyIcon: "chart"
    property string emptyTitle: "No data yet"
    property string emptyText: ""
    property var tableHeader: []
    property var tableRows: []      // [[cell, cell, ...]] as display strings
    property int tableTextColumns: 1  // leading text columns (left-aligned); the rest are numbers
    property bool showTable: false
    property bool loading: false    // refetch: keep the previous render, dimmed
    default property alias content: plot.data
    property alias legend: legendRow.data
    radius: Theme.radius
    color: Theme.panel
    border.color: Theme.border

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        anchors.topMargin: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    Layout.fillWidth: true
                    text: card.title
                    color: Theme.text
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: card.subtitle
                    color: Theme.muted
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }
            RowLayout {
                id: legendRow
                visible: !card.empty
                spacing: 14
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 3
            }
            IconButton {
                visible: card.tableRows.length > 0 && !card.empty
                iconName: card.showTable ? "chart" : "list"
                tip: card.showTable ? "Show chart" : "Show as table"
                size: 28
                iconSize: 15
                active: card.showTable
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: -3
                onClicked: card.showTable = !card.showTable
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Item {
                id: plot
                anchors.fill: parent
                visible: !card.empty && !card.showTable
                opacity: card.loading ? 0.55 : 1
                Behavior on opacity { NumberAnimation { duration: 150 } }
            }

            // Table view. The rows scroll in a Flickable (not a ListView over the array), so the periodic
            // statistics refresh replaces the rows without scrolling back to the top.
            Item {
                id: table
                objectName: "chartTable"
                anchors.fill: parent
                visible: card.showTable && !card.empty
                clip: true
                Rectangle {
                    id: tableHead
                    width: table.width
                    height: 28
                    color: Theme.panel
                    z: 2
                    Row {
                        anchors.fill: parent
                        Repeater {
                            model: card.tableHeader
                            delegate: Text {
                                required property var modelData
                                required property int index
                                width: table.width / Math.max(1, card.tableHeader.length) - (index === card.tableHeader.length - 1 ? 12 : 0)
                                height: 28
                                leftPadding: index === 0 ? 6 : 0
                                verticalAlignment: Text.AlignVCenter
                                horizontalAlignment: index < card.tableTextColumns ? Text.AlignLeft : Text.AlignRight
                                text: String(modelData).toUpperCase()
                                color: Theme.muted
                                font.pixelSize: 11
                                font.weight: Font.Bold
                                font.letterSpacing: 0.8
                            }
                        }
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
                }
                Flickable {
                    id: tableFlick
                    objectName: "chartTableFlick"
                    anchors.top: tableHead.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    clip: true
                    contentWidth: width
                    contentHeight: tableBody.height
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    Column {
                        id: tableBody
                        width: tableFlick.width
                        Repeater {
                            model: table.visible ? card.tableRows : []
                            delegate: Rectangle {
                                id: tableRow
                                required property var modelData
                                required property int index
                                width: tableBody.width
                                height: 26
                                color: index % 2 === 1 ? Theme.raised : "transparent"
                                radius: 5
                                Row {
                                    anchors.fill: parent
                                    Repeater {
                                        model: tableRow.modelData
                                        delegate: Text {
                                            required property var modelData
                                            required property int index
                                            width: table.width / Math.max(1, tableRow.modelData.length) - (index === tableRow.modelData.length - 1 ? 12 : 0)
                                            height: 26
                                            leftPadding: index === 0 ? 6 : 0
                                            verticalAlignment: Text.AlignVCenter
                                            horizontalAlignment: index < card.tableTextColumns ? Text.AlignLeft : Text.AlignRight
                                            text: String(modelData)
                                            color: index === 0 ? Theme.textDim : Theme.text
                                            font.pixelSize: 12
                                            font.features: { "tnum": 1 }
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Column {
                visible: card.empty
                anchors.centerIn: parent
                width: Math.min(parent.width - 24, 340)
                spacing: 7
                Rectangle {
                    width: 42
                    height: 42
                    radius: 12
                    color: Theme.raised
                    anchors.horizontalCenter: parent.horizontalCenter
                    Icon { anchors.centerIn: parent; name: card.emptyIcon; size: 20; color: Theme.muted }
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: card.emptyTitle
                    color: Theme.text
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    horizontalAlignment: Text.AlignHCenter
                    text: card.emptyText
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    lineHeight: 1.1
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: card.footnote
            color: Theme.muted
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }
}
