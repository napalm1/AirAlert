pragma Singleton
import QtQuick

// Design tokens for AirAlert. `dark` is bound to the app appearance in Main.qml.
QtObject {
    property bool dark: true

    readonly property color bg: dark ? "#060b15" : "#edf1f7"
    readonly property color bgAlt: dark ? "#0a1221" : "#e3e9f2"
    readonly property color panel: dark ? "#0e1829" : "#ffffff"
    readonly property color glass: dark ? Qt.rgba(0.05, 0.085, 0.15, 0.93) : Qt.rgba(1, 1, 1, 0.95)
    readonly property color raised: dark ? "#132036" : "#f3f6fa"
    readonly property color hover: dark ? "#182841" : "#eaf0f8"
    readonly property color pressed: dark ? "#1d3050" : "#dfe7f2"
    readonly property color border: dark ? "#1f3050" : "#d5deea"
    readonly property color borderStrong: dark ? "#2d4369" : "#bccadd"
    readonly property color text: dark ? "#e9eff9" : "#111d31"
    readonly property color textDim: dark ? "#a9b8d0" : "#43546d"
    readonly property color muted: dark ? "#71839f" : "#687890"
    readonly property color accent: dark ? "#4ea8ff" : "#1f6fe5"
    readonly property color accentHover: dark ? "#6bb8ff" : "#195fcb"
    readonly property color accentSoft: dark ? Qt.rgba(0.31, 0.66, 1, 0.15) : Qt.rgba(0.12, 0.44, 0.9, 0.10)
    readonly property color accentInk: "#ffffff"
    readonly property color vessel: dark ? "#2dd4bf" : "#0d9488"
    readonly property color alert: dark ? "#ffb224" : "#c96a00"
    readonly property color alertSoft: dark ? Qt.rgba(1, 0.70, 0.14, 0.14) : Qt.rgba(0.85, 0.47, 0.02, 0.11)
    readonly property color danger: dark ? "#ff5f6d" : "#d42c43"
    readonly property color dangerSoft: dark ? Qt.rgba(1, 0.37, 0.43, 0.14) : Qt.rgba(0.83, 0.17, 0.26, 0.09)
    readonly property color success: dark ? "#34d399" : "#0c9467"
    readonly property color successSoft: dark ? Qt.rgba(0.2, 0.83, 0.6, 0.14) : Qt.rgba(0.05, 0.58, 0.4, 0.10)
    readonly property color sim: dark ? "#c38bff" : "#8a3fd8"
    readonly property color simSoft: dark ? Qt.rgba(0.76, 0.55, 1, 0.15) : Qt.rgba(0.54, 0.25, 0.85, 0.10)
    readonly property color stale: dark ? "#6c7b92" : "#8b98ab"
    readonly property color shadow: dark ? Qt.rgba(0, 0, 0, 0.45) : Qt.rgba(0.08, 0.15, 0.3, 0.16)
    readonly property color scrim: dark ? Qt.rgba(0.01, 0.03, 0.07, 0.62) : Qt.rgba(0.07, 0.11, 0.2, 0.34)

    readonly property string mono: "Cascadia Mono"
    readonly property int radius: 14
    readonly property int radiusSmall: 9
    readonly property int gap: 14
}
