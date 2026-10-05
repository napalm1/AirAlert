import QtQuick

// Segmented picker. options: strings or {value, label} objects.
Rectangle {
    id: seg
    property var options: []
    property string value: ""
    property bool stretch: false
    signal picked(string value)
    implicitHeight: 34
    implicitWidth: row.implicitWidth + 6
    radius: Theme.radiusSmall
    color: Theme.raised
    border.color: Theme.border

    Row {
        id: row
        x: 3
        y: 3
        height: parent.height - 6
        spacing: 2
        Repeater {
            model: seg.options
            delegate: Rectangle {
                id: option
                required property var modelData
                readonly property string optionValue: typeof modelData === "string" ? modelData : modelData.value
                readonly property string optionLabel: typeof modelData === "string" ? modelData : modelData.label
                readonly property bool current: seg.value === optionValue
                width: seg.stretch ? (seg.width - 6 - (seg.options.length - 1) * 2) / seg.options.length
                                   : Math.max(52, label.implicitWidth + 24)
                height: row.height
                radius: 7
                color: current ? Theme.accent : area.containsMouse ? Theme.hover : "transparent"
                Behavior on color { ColorAnimation { duration: 110 } }
                Text {
                    id: label
                    anchors.centerIn: parent
                    text: option.optionLabel
                    color: option.current ? Theme.accentInk : Theme.textDim
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                MouseArea {
                    id: area
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: seg.picked(option.optionValue)
                }
            }
        }
    }
}
