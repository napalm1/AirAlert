import QtQuick
import "Util.js" as Util

// Ranked horizontal bars (top N). One series, one colour; value at the bar tip; row is the hover target.
Item {
    id: chart
    property var rows: []            // [{label, detail, value}]
    property color color: tone.aircraft
    property string unitLabel: "aircraft"
    property real total: 0           // denominator for the share shown on hover
    property string shareLabel: ""
    property int hovered: -1

    readonly property real maxValue: {
        var m = 0
        for (var i = 0; i < rows.length; i++)
            m = Math.max(m, rows[i].value)
        return m
    }
    readonly property real labelW: Math.min(200, Math.max(120, width * 0.4))
    readonly property real valueW: 46
    readonly property real trackW: Math.max(10, width - labelW - valueW)
    readonly property real rowH: rows.length > 0 ? Math.min(38, height / rows.length) : 38

    ChartStyle { id: tone }

    Rectangle {
        x: chart.labelW - 1
        width: 1
        height: chart.rowH * chart.rows.length
        color: tone.baseline
    }

    Column {
        width: chart.width
        Repeater {
            model: chart.rows
            delegate: Item {
                id: rowItem
                required property var modelData
                required property int index
                width: chart.width
                height: chart.rowH

                Rectangle {
                    anchors.fill: parent
                    anchors.topMargin: 1
                    anchors.bottomMargin: 1
                    radius: 6
                    color: tone.wash
                    visible: chart.hovered === rowItem.index
                }
                Column {
                    x: 4
                    width: chart.labelW - 14
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 0
                    Text {
                        width: parent.width
                        text: rowItem.modelData.label
                        color: Theme.text
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        visible: text !== "" && chart.rowH >= 30
                        text: rowItem.modelData.detail || ""
                        color: Theme.muted
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                }
                Rectangle {
                    id: bar
                    x: chart.labelW
                    width: Math.max(3, rowItem.modelData.value / Math.max(1, chart.maxValue) * chart.trackW)
                    height: Math.min(14, chart.rowH - 10)
                    anchors.verticalCenter: parent.verticalCenter
                    radius: Math.min(4, height / 2)
                    topLeftRadius: 0
                    bottomLeftRadius: 0
                    color: chart.color
                    opacity: chart.hovered < 0 || chart.hovered === rowItem.index ? 1 : 0.55
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                }
                Text {
                    x: bar.x + bar.width + 6
                    anchors.verticalCenter: parent.verticalCenter
                    text: Util.number(rowItem.modelData.value)
                    color: Theme.textDim
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.NoButton
                    onEntered: chart.hovered = rowItem.index
                    onExited: if (chart.hovered === rowItem.index) chart.hovered = -1
                }
            }
        }
    }

    ChartTip {
        shown: chart.hovered >= 0 && chart.hovered < chart.rows.length
        readonly property var row: shown ? chart.rows[chart.hovered] : null
        anchorX: row ? chart.labelW + row.value / Math.max(1, chart.maxValue) * chart.trackW + 40 : 0
        anchorY: (chart.hovered + 0.5) * chart.rowH
        title: row ? row.label + (row.detail ? " · " + row.detail : "") : ""
        rows: row ? [{ color: chart.color, value: Util.number(row.value), label: chart.unitLabel },
                     { value: chart.total > 0 ? Math.round(row.value / chart.total * 100) + "%" : "—", label: chart.shareLabel }]
                  : []
    }
}
