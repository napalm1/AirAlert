import QtQuick
import "Util.js" as Util

// Time series: a 2 px line over a light area wash, broken where there is no data, with a crosshair readout.
// compact: sparkline without axes (the caller labels it).
Item {
    id: chart
    property var points: []          // [{t: unix seconds (bucket start), v: number | null}], evenly spaced
    property real start: 0           // x domain in unix seconds
    property real end: 1
    property real bucket: 60         // seconds covered by one point
    property color color: tone.aircraft
    property bool compact: false
    property string unitLabel: "msg/s"
    property string gapLabel: "not monitoring"
    property string tickMode: "hours"   // hours | days | weeks
    property int hovered: -1

    readonly property real axisWidth: compact ? 0 : 40
    readonly property real plotTop: compact ? 5 : 8
    readonly property real plotW: Math.max(10, width - axisWidth)
    readonly property real plotH: Math.max(10, height - plotTop - (compact ? 5 : 22))
    readonly property real maxValue: {
        var m = 0
        for (var i = 0; i < points.length; i++)
            if (points[i].v !== null && points[i].v !== undefined)
                m = Math.max(m, points[i].v)
        return m
    }
    readonly property var axis: compact ? { max: Math.max(1, maxValue * 1.08), step: Math.max(1, maxValue * 1.08), count: 1 }
                                        : tone.scale(maxValue, 4, false)
    readonly property int lastValid: {
        for (var i = points.length - 1; i >= 0; i--)
            if (points[i].v !== null && points[i].v !== undefined)
                return i
        return -1
    }

    function xOf(t) { return axisWidth + (t + bucket / 2 - start) / Math.max(1, end - start) * plotW }
    function yOf(v) { return plotTop + plotH - Math.max(0, v) / axis.max * plotH }
    function css(c, a) { return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + a + ")" }

    function xTicks() {
        if (compact || !(end > start))
            return []
        var out = []
        var d = new Date(start * 1000)
        var guard = 0
        if (tickMode === "hours") {
            d.setMinutes(0, 0, 0)
            d.setHours(d.getHours() + 1)
            while (d.getHours() % 4 !== 0 && guard++ < 24)
                d.setHours(d.getHours() + 1)
            while (d.getTime() / 1000 <= end && guard++ < 200) {
                out.push({ t: d.getTime() / 1000, label: d.getHours() === 0 ? Qt.formatDateTime(d, "ddd") : Qt.formatDateTime(d, "HH:mm") })
                d.setHours(d.getHours() + 4)
            }
        } else {
            var stepDays = tickMode === "days" ? 1 : 5
            d.setHours(24, 0, 0, 0)
            while (d.getTime() / 1000 <= end && guard++ < 200) {
                out.push({ t: d.getTime() / 1000, label: Qt.formatDateTime(d, tickMode === "days" ? "ddd d" : "MMM d") })
                d.setDate(d.getDate() + stepDays)
            }
        }
        return out
    }

    function bucketTitle(t) {
        var a = new Date(t * 1000), b = new Date((t + bucket) * 1000)
        if (bucket < 60)
            return Qt.formatDateTime(a, "HH:mm:ss")
        if (bucket >= 4 * 3600)
            return Qt.formatDateTime(a, "ddd MMM d, HH:mm") + "–" + Qt.formatDateTime(b, "HH:mm")
        return Qt.formatDateTime(a, "ddd HH:mm") + "–" + Qt.formatDateTime(b, "HH:mm")
    }

    ChartStyle { id: tone }

    // Gridlines and y labels
    Repeater {
        model: chart.compact ? 0 : chart.axis.count + 1
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
        visible: chart.compact
        y: chart.plotTop + chart.plotH
        width: chart.width
        height: 1
        color: tone.grid
    }

    // X labels
    Repeater {
        model: chart.xTicks()
        delegate: Text {
            required property var modelData
            readonly property real cx: chart.axisWidth + (modelData.t - chart.start) / Math.max(1, chart.end - chart.start) * chart.plotW
            x: Math.max(chart.axisWidth, Math.min(chart.width - width, cx - width / 2))
            y: chart.plotTop + chart.plotH + 5
            text: modelData.label
            color: tone.label
            font.pixelSize: 11
        }
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var pts = chart.points
            var base = chart.plotTop + chart.plotH
            var segment = []
            function flush() {
                if (segment.length === 1) {
                    ctx.beginPath()
                    ctx.fillStyle = chart.css(chart.color, 1)
                    ctx.arc(segment[0][0], segment[0][1], 2.5, 0, Math.PI * 2)
                    ctx.fill()
                } else if (segment.length > 1) {
                    ctx.beginPath()
                    ctx.moveTo(segment[0][0], base)
                    for (var k = 0; k < segment.length; k++)
                        ctx.lineTo(segment[k][0], segment[k][1])
                    ctx.lineTo(segment[segment.length - 1][0], base)
                    ctx.closePath()
                    ctx.fillStyle = chart.css(chart.color, Theme.dark ? 0.14 : 0.10)
                    ctx.fill()
                    ctx.beginPath()
                    ctx.moveTo(segment[0][0], segment[0][1])
                    for (var j = 1; j < segment.length; j++)
                        ctx.lineTo(segment[j][0], segment[j][1])
                    ctx.lineWidth = 2
                    ctx.lineJoin = "round"
                    ctx.lineCap = "round"
                    ctx.strokeStyle = chart.css(chart.color, 1)
                    ctx.stroke()
                }
                segment = []
            }
            for (var i = 0; i < pts.length; i++) {
                if (pts[i].v === null || pts[i].v === undefined)
                    flush()
                else
                    segment.push([chart.xOf(pts[i].t), chart.yOf(pts[i].v)])
            }
            flush()
        }
    }
    onPointsChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    onColorChanged: canvas.requestPaint()
    onAxisChanged: canvas.requestPaint()
    Connections {
        target: Theme
        function onDarkChanged() { canvas.requestPaint() }
    }

    // Latest value marker: 8 px dot with a surface ring.
    Rectangle {
        readonly property var point: chart.lastValid >= 0 ? chart.points[chart.lastValid] : null
        visible: point !== null && chart.hovered < 0
        width: 12
        height: 12
        radius: 6
        color: chart.color
        border.width: 2
        border.color: tone.surface
        x: point ? chart.xOf(point.t) - 6 : 0
        y: point ? chart.yOf(point.v) - 6 : 0
    }

    // Crosshair
    Rectangle {
        visible: chart.hovered >= 0
        x: chart.hovered >= 0 ? Math.round(chart.xOf(chart.points[chart.hovered].t)) : 0
        y: chart.plotTop
        width: 1
        height: chart.plotH
        color: Theme.borderStrong
    }
    Rectangle {
        readonly property var point: chart.hovered >= 0 ? chart.points[chart.hovered] : null
        visible: point !== null && point.v !== null && point.v !== undefined
        width: 12
        height: 12
        radius: 6
        color: chart.color
        border.width: 2
        border.color: tone.surface
        x: point ? chart.xOf(point.t) - 6 : 0
        y: point && point.v !== null ? chart.yOf(point.v) - 6 : 0
    }

    MouseArea {
        x: chart.axisWidth
        width: chart.plotW
        height: chart.height
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: (mouse) => {
            var n = chart.points.length
            if (n === 0) { chart.hovered = -1; return }
            var t = chart.start + mouse.x / chart.plotW * (chart.end - chart.start) - chart.bucket / 2
            var i = Math.round((t - chart.points[0].t) / chart.bucket)
            chart.hovered = Math.max(0, Math.min(n - 1, i))
        }
        onExited: chart.hovered = -1
    }

    ChartTip {
        readonly property var point: chart.hovered >= 0 ? chart.points[chart.hovered] : null
        shown: point !== null
        anchorX: point ? chart.xOf(point.t) : 0
        anchorY: point && point.v !== null ? chart.yOf(point.v) : chart.plotTop + chart.plotH / 2
        title: point ? chart.bucketTitle(point.t) : ""
        rows: !point ? [] : (point.v === null || point.v === undefined)
              ? [{ value: "—", label: chart.gapLabel }]
              : [{ color: chart.color, value: Util.number(point.v, point.v < 10 ? 1 : 0), label: chart.unitLabel }]
    }
}
