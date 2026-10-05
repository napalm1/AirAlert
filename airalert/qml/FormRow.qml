import QtQuick
import QtQuick.Layouts

// Label + control(s) + optional hint, stacked.
ColumnLayout {
    id: row
    property string label: ""
    property string hint: ""
    default property alias content: holder.data
    Layout.fillWidth: true
    spacing: 7
    Text {
        text: row.label
        visible: text !== ""
        color: Theme.textDim
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    ColumnLayout {
        id: holder
        Layout.fillWidth: true
        spacing: 6
    }
    Text {
        text: row.hint
        visible: text !== ""
        color: Theme.muted
        font.pixelSize: 12
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
