import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Scrollable settings page body.
Flickable {
    id: section
    default property alias content: column.data
    contentHeight: column.implicitHeight + 48
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
    ColumnLayout {
        id: column
        x: 28
        y: 24
        width: section.width - 56
        spacing: 18
    }
}
