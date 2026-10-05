import QtQuick
import QtQuick.Controls.Basic

ToolTip {
    id: tip
    delay: 450
    timeout: 6000
    padding: 8
    leftPadding: 10
    rightPadding: 10
    contentItem: Text {
        text: tip.text
        color: Theme.dark ? "#0b1424" : "#ffffff"
        font.pixelSize: 12
        font.weight: Font.Medium
    }
    background: Rectangle {
        radius: 7
        color: Theme.dark ? "#e3ebf8" : "#15233a"
    }
}
