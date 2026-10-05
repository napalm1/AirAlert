import QtQuick
import "Util.js" as Util

// Receiver coverage rose: maximum range per 10° bearing sector around home (north up, clockwise).
// Each series is a stepped outline with a light wash; hovering a sector reads out every series.
Item {
    id: chart
    property var series: []          // [{name, color, values: [36 × number | null]}]
    property string units: "mi"
    property int hovered: -1

    readonly property real maxValue: {
        var m = 0
        for (var s = 0; s < series.length; s++)
            for (var i = 0; i < series[s].values.length; i++)
                m = Math.max(m, series[s].values[i] || 0)
        return m
    }
    readonly property var axis: tone.scale(maxValue, 4, false, true)
    readonly property real radius: Math.max(10, Math.min(width, height) / 2 - 18)
    readonly property real cx: width / 2
    readonly property real cy: height / 2

    function rad(bearing) { return (bearing - 90) * Math.PI / 180 }
    function css(c, a) { return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + a + ")" }
    function rOf(v) { return v > 0 ? v / axis.max * radius : 0 }

    ChartStyle { id: tone }

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var c = chart
            // Rings and spokes: hairline, recessive.
            ctx.lineWidth = 1
            ctx.strokeStyle = c.css(tone.grid, 1)
            for (var r = 1; r <= 4; r++) {
                ctx.beginPath()
                ctx.arc(c.cx, c.cy, c.radius * r / 4, 0, Math.PI * 2)
                ctx.stroke()
            }
            for (var b = 0; b < 360; b += 30) {
                ctx.beginPath()
                ctx.moveTo(c.cx, c.cy)
                ctx.lineTo(c.cx + Math.cos(c.rad(b)) * c.radius, c.cy + Math.sin(c.rad(b)) * c.radius)
                ctx.stroke()
            }
            ctx.strokeStyle = c.css(tone.baseline, 1)
            ctx.beginPath()
            ctx.arc(c.cx, c.cy, c.radius, 0, Math.PI * 2)
            ctx.stroke()
            // Hovered sector wash
            if (c.hovered >= 0) {
                ctx.beginPath()
                ctx.moveTo(c.cx, c.cy)
                ctx.arc(c.cx, c.cy, c.radius, c.rad(c.hovered * 10), c.rad(c.hovered * 10 + 10), false)
                ctx.closePath()
                ctx.fillStyle = c.css(Theme.text, Theme.dark ? 0.06 : 0.05)
                ctx.fill()
            }
            // Series: stepped outline (arc per sector, radial steps between sectors).
            for (var s = 0; s < c.series.length; s++) {
                var values = c.series[s].values
                var any = false
                for (var k = 0; k < values.length; k++)
                    if (values[k] > 0) any = true
                if (!any)
                    continue
                ctx.beginPath()
                for (var i = 0; i < 36; i++) {
                    var rr = c.rOf(values[i])
                    var a0 = c.rad(i * 10), a1 = c.rad(i * 10 + 10)
                    if (i === 0)
                        ctx.moveTo(c.cx + Math.cos(a0) * rr, c.cy + Math.sin(a0) * rr)
                    else
                        ctx.lineTo(c.cx + Math.cos(a0) * rr, c.cy + Math.sin(a0) * rr)
                    if (rr > 0)
                        ctx.arc(c.cx, c.cy, rr, a0, a1, false)
                    else
                        ctx.lineTo(c.cx, c.cy)
                }
                ctx.closePath()
                ctx.fillStyle = c.css(c.series[s].color, Theme.dark ? 0.16 : 0.12)
                ctx.fill()
                ctx.lineWidth = 2
                ctx.lineJoin = "round"
                ctx.strokeStyle = c.css(c.series[s].color, 1)
                ctx.stroke()
            }
            // Home
            ctx.beginPath()
            ctx.fillStyle = c.css(Theme.text, 1)
            ctx.arc(c.cx, c.cy, 3, 0, Math.PI * 2)
            ctx.fill()
        }
    }
    onSeriesChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    onHoveredChanged: canvas.requestPaint()
    Connections {
        target: Theme
        function onDarkChanged() { canvas.requestPaint() }
    }

    // Compass letters
    Repeater {
        model: [{ b: 0, t: "N" }, { b: 90, t: "E" }, { b: 180, t: "S" }, { b: 270, t: "W" }]
        delegate: Text {
            required property var modelData
            x: chart.cx + Math.cos(chart.rad(modelData.b)) * (chart.radius + 10) - width / 2
            y: chart.cy + Math.sin(chart.rad(modelData.b)) * (chart.radius + 10) - height / 2
            text: modelData.t
            color: modelData.t === "N" ? Theme.textDim : tone.label
            font.pixelSize: 11
            font.weight: Font.DemiBold
        }
    }
    // Ring distances along the 135° spoke, on a surface plate so outlines never cross the digits
    Repeater {
        model: 4
        delegate: Rectangle {
            required property int index
            readonly property real ringR: chart.radius * (index + 1) / 4
            x: chart.cx + Math.cos(chart.rad(135)) * ringR - width / 2
            y: chart.cy + Math.sin(chart.rad(135)) * ringR - height / 2
            width: ringLabel.implicitWidth + 8
            height: ringLabel.implicitHeight + 2
            radius: 4
            color: Util.tint(Theme.panel, 0.88)
            visible: chart.maxValue > 0
            Text {
                id: ringLabel
                anchors.centerIn: parent
                text: tone.compact(chart.axis.step * (parent.index + 1)) + (parent.index === 3 ? " " + chart.units : "")
                color: tone.label
                font.pixelSize: 10
                font.features: { "tnum": 1 }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: (mouse) => {
            var dx = mouse.x - chart.cx, dy = mouse.y - chart.cy
            var dist = Math.sqrt(dx * dx + dy * dy)
            if (dist > chart.radius + 4 || dist < 4) { chart.hovered = -1; return }
            var bearing = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360
            chart.hovered = Math.min(35, Math.floor(bearing / 10))
        }
        onExited: chart.hovered = -1
    }

    ChartTip {
        shown: chart.hovered >= 0
        readonly property real mid: chart.rad(chart.hovered * 10 + 5)
        anchorX: chart.cx + Math.cos(mid) * chart.radius * 0.55
        anchorY: chart.cy + Math.sin(mid) * chart.radius * 0.55
        title: chart.hovered < 0 ? "" : "Bearing " + Util.pad3(chart.hovered * 10) + "°–" + Util.pad3(chart.hovered * 10 + 10) + "°"
        rows: {
            if (chart.hovered < 0)
                return []
            var out = []
            for (var s = 0; s < chart.series.length; s++) {
                var v = chart.series[s].values[chart.hovered]
                out.push({ color: chart.series[s].color, value: v > 0 ? Util.number(v, v < 10 ? 1 : 0) + " " + chart.units : "—",
                           label: chart.series[s].name })
            }
            return out
        }
    }
}
