import QtQuick
import "Util.js" as Util

// Chart tokens and axis helpers shared by the Chart* components.
// Series hues are the app's aircraft blue and vessel teal, stepped per theme into the chart
// lightness band and checked for colour-vision separation and contrast on Theme.panel.
QtObject {
    readonly property color aircraft: Theme.dark ? "#3f93f2" : "#1f6fe5"
    readonly property color vessel: Theme.dark ? "#14a394" : "#0d9488"
    readonly property color grid: Theme.dark ? "#18263d" : "#e8edf4"
    readonly property color baseline: Theme.dark ? "#2d4369" : "#bccadd"
    readonly property color label: Theme.muted
    readonly property color surface: Theme.panel
    readonly property color wash: Theme.dark ? Qt.rgba(1, 1, 1, 0.045) : Qt.rgba(0.07, 0.12, 0.22, 0.045)

    // Round axis {max, step, count}: `count` intervals, or one either side of it when that wastes less
    // space (exact: always `count`). integer: steps of at least 1.
    function scale(maxValue, count, integer, exact) {
        var n = count || 4
        if (!(maxValue > 0))
            return { max: n, step: 1, count: n }
        function fit(k) {
            var raw = maxValue / k
            var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10))
            var steps = [1, 2, 2.5, 5, 10]
            var step = 10 * mag
            for (var i = 0; i < steps.length; i++) {
                if (steps[i] * mag >= raw - 1e-9) {
                    step = steps[i] * mag
                    break
                }
            }
            if (integer)
                step = Math.max(1, Math.ceil(step))
            return { max: step * k, step: step, count: k }
        }
        var best = fit(n)
        if (!exact) {
            var options = [n + 1, n - 1]
            for (var j = 0; j < options.length; j++) {
                if (options[j] < 2)
                    continue
                var other = fit(options[j])
                if (other.max < best.max - 1e-9)
                    best = other
            }
        }
        return best
    }

    function compact(v) {
        if (v === undefined || v === null)
            return "—"
        var a = Math.abs(v)
        if (a >= 1e6)
            return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M"
        if (a >= 1e4)
            return Math.round(v / 1e3) + "K"
        if (a >= 1e3)
            return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "K"
        return Util.number(v, a > 0 && a < 10 && v % 1 !== 0 ? 1 : 0)
    }
}
