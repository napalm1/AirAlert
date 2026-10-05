import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: combo
    implicitHeight: 36
    font.pixelSize: 13
    hoverEnabled: true
    opacity: enabled ? 1 : 0.45

    contentItem: Text {
        leftPadding: 12
        rightPadding: 32
        text: combo.displayText
        color: Theme.text
        font: combo.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Icon {
        name: "chevronDown"
        size: 14
        color: Theme.muted
        x: combo.width - width - 12
        y: (combo.height - height) / 2
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: combo.pressed ? Theme.pressed : combo.hovered ? Theme.hover : Theme.raised
        border.color: combo.activeFocus || combo.popup.visible ? Theme.accent : Theme.border
        border.width: combo.activeFocus || combo.popup.visible ? 1.5 : 1
    }
    delegate: ItemDelegate {
        id: item
        required property var model
        required property int index
        width: ListView.view ? ListView.view.width : combo.width
        height: 34
        highlighted: combo.highlightedIndex === index
        hoverEnabled: true
        contentItem: Text {
            text: {
                const data = item.model.modelData
                if (combo.textRole === "")
                    return data
                if (data !== undefined && data !== null && typeof data === "object")
                    return data[combo.textRole]
                return item.model[combo.textRole]
            }
            color: combo.currentIndex === item.index ? Theme.accent : Theme.text
            font.pixelSize: 13
            font.weight: combo.currentIndex === item.index ? Font.DemiBold : Font.Normal
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            leftPadding: 4
        }
        background: Rectangle {
            radius: 7
            color: item.highlighted || item.hovered ? Theme.hover : "transparent"
        }
    }
    popup: Popup {
        y: combo.height + 4
        width: Math.max(combo.width, 180)
        padding: 4
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 340)
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: combo.popup.visible ? combo.delegateModel : null
            currentIndex: combo.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            radius: 11
            color: Theme.panel
            border.color: Theme.borderStrong
        }
    }
}
