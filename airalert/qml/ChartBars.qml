import QtQuick
import "Util.js" as Util

// Column chart: one or two series over categories. Thin columns with a rounded data end, square at
// the baseline and a 2 px gap between neighbours; hairline grid; the whole category slot is the hover target.
Item {
    id: chart
    property var categories: []      // x-axis labels
    property var tipTitles: []       // optional longer titles per category for the readout
    property var series: []          // [{name, color, values: [number]}]
    property int labelEvery: 1
    property bool labelFromEnd: false  // count label spacing back from the last category
    property int emphasis: -1        // category whose label is emphasised (e.g. today)
    property string unitLabel: ""    // readout label for a single series, e.g. "aircraft"
    property bool integer: true
    property int ticks: 4                // gridline intervals
    property bool showCategories: true   // x labels (off for the upper panel of small multiples)
    property int hovered: -1

    readonly property int count: categories.length
    readonly property int seriesCount: series.length
    readonly property real maxValue: {
        var m = 0
        for (var s = 0; s < series.length; s++)
            for (var i = 0; i < series[s].values.length; i++)
                m = Math.max(m, series[s].values[i] || 0)
        return m
    }
    readonly property var axis: tone.scale(maxValue, ticks, integer)
    readonly property real axisWidth: 36
    readonly property real plotTop: 14   // room for the peak label
    readonly property real plotW: Math.max(10, width - axisWidth)
    readonly property real plotH: Math.max(10, height - plotTop - (showCategories ? 22 : 4))
    readonly property real slot: count > 0 ? plotW / count : plotW
    readonly property real barW: Math.max(2, Math.min(24, (slot * 0.74 - (seriesCount - 1) * 2) / Math.max(1, seriesCount)))
    readonly property real groupW: seriesCount * barW + (seriesCount - 1) * 2
    // The single tallest mark gets a direct value label.
    readonly property var peak: {
        var best = { series: -1, index: -1, value: 0 }
        for (var s = 0; s < series.length; s++)
            for (var i = 0; i < series[s].values.length; i++)
                if ((series[s].values[i] || 0) > best.value)
                    best = { series: s, index: i, value: series[s].values[i] }
        return best
    }

    function yOf(v) { return plotTop + plotH - Math.max(0, v) / axis.max * plotH }

    ChartStyle { id: tone }

    // Gridlines and y-axis labels
    Repeater {
        model: chart.axis.count + 1
        delegate: Item {
            required property int index
            readonly property real value: chart.axis.step * index
            y: chart.yOf(value)
            width: chart.width
            Rectangle {
                x: chart.axisWidth
                width: chart.plotW
                height: 1
                color: index === 0 ? tone.baseline : tone.grid
            }
            Text {
                width: chart.axisWidth - 8
                y: -7
                horizontalAlignment: Text.AlignRight
                text: tone.compact(value)
                color: tone.label
                font.pixelSize: 11
                font.features: { "tnum": 1 }
            }
        }
    }

    Rectangle {
        visible: chart.hovered >= 0
        x: chart.axisWidth + chart.hovered * chart.slot + 1
        y: chart.plotTop
        width: chart.slot - 2
        height: chart.plotH
        radius: 4
        color: tone.wash
    }

    Repeater {
        model: chart.count
        delegate: Item {
            id: slotItem
            required property int index
            readonly property int category: index
            x: chart.axisWidth + index * chart.slot
            width: chart.slot
            height: chart.height

            Repeater {
                model: chart.series
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    readonly property real value: modelData.values[slotItem.category] || 0
                    visible: value > 0
                    x: (chart.slot - chart.groupW) / 2 + index * (chart.barW + 2)
                    width: chart.barW
                    y: chart.yOf(value)
                    height: chart.plotTop + chart.plotH - y
                    radius: Math.min(4, width / 2)
                    bottomLeftRadius: 0
                    bottomRightRadius: 0
                    color: modelData.color
                    opacity: chart.hovered < 0 || chart.hovered === slotItem.category ? 1 : 0.55
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                }
            }

            Text {
                visible: chart.peak.index === slotItem.category && chart.peak.series >= 0 && chart.hovered < 0
                x: (chart.slot - chart.groupW) / 2 + chart.peak.series * (chart.barW + 2) + chart.barW / 2 - width / 2
                y: chart.yOf(chart.peak.value) - height - 2
                text: Util.number(chart.peak.value)
                color: Theme.textDim
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }

            Text {
                visible: chart.showCategories && ((chart.labelFromEnd ? chart.count - 1 - slotItem.category : slotItem.category) % chart.labelEvery === 0
                         || slotItem.category === chart.emphasis)
                anchors.horizontalCenter: parent.horizontalCenter
                y: chart.plotTop + chart.plotH + 5
                text: chart.categories[slotItem.category]
                color: slotItem.category === chart.emphasis ? Theme.text : tone.label
                font.pixelSize: 11
                font.weight: slotItem.category === chart.emphasis ? Font.DemiBold : Font.Normal
            }
        }
    }

    MouseArea {
        x: chart.axisWidth
        y: chart.plotTop
        width: chart.plotW
        height: chart.plotH
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: (mouse) => chart.hovered = Math.max(0, Math.min(chart.count - 1, Math.floor(mouse.x / chart.slot)))
        onExited: chart.hovered = -1
    }

    ChartTip {
        shown: chart.hovered >= 0
        anchorX: chart.axisWidth + (chart.hovered + 0.5) * chart.slot + chart.groupW / 2
        anchorY: chart.plotTop + chart.plotH / 2
        title: chart.hovered < 0 ? "" : (chart.tipTitles[chart.hovered] || chart.categories[chart.hovered] || "")
        rows: {
            if (chart.hovered < 0)
                return []
            var out = []
            for (var s = 0; s < chart.series.length; s++)
                out.push({ color: chart.series[s].color, value: Util.number(chart.series[s].values[chart.hovered] || 0),
                           label: chart.series.length === 1 ? chart.unitLabel : chart.series[s].name })
            return out
        }
    }
}
