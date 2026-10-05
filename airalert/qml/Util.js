.pragma library

function _rgb(hex) {
    return [parseInt(hex.substr(1, 2), 16), parseInt(hex.substr(3, 2), 16), parseInt(hex.substr(5, 2), 16)]
}

function _hex(v) {
    var s = Math.round(Math.max(0, Math.min(255, v))).toString(16)
    return s.length === 1 ? "0" + s : s
}

// Interpolated altitude color matching the map glyphs. stops: [{altitude, color}]
function altitudeColor(altitude, stops, unknown) {
    if (altitude === undefined || altitude === null || !stops || stops.length === 0)
        return unknown
    if (altitude <= stops[0].altitude)
        return stops[0].color
    for (var i = 1; i < stops.length; i++) {
        if (altitude <= stops[i].altitude) {
            var f = (altitude - stops[i - 1].altitude) / (stops[i].altitude - stops[i - 1].altitude)
            var a = _rgb(stops[i - 1].color), b = _rgb(stops[i].color)
            return "#" + _hex(a[0] + (b[0] - a[0]) * f) + _hex(a[1] + (b[1] - a[1]) * f) + _hex(a[2] + (b[2] - a[2]) * f)
        }
    }
    return stops[stops.length - 1].color
}

function number(value, digits) {
    if (value === undefined || value === null || value === "")
        return "—"
    return Number(value).toLocaleString(Qt.locale(), "f", digits || 0)
}

function pad3(value) {
    if (value === undefined || value === null)
        return "—"
    var s = String(Math.round(value))
    while (s.length < 3) s = "0" + s
    return s
}

function tint(color, alpha) {
    return Qt.rgba(color.r, color.g, color.b, alpha)
}
