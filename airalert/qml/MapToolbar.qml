import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

GlassPanel {
    id: toolbar
    property var mapItem
    signal fullscreenRequested()
    width: 52
    height: column.implicitHeight + 14

    function feet(value) {
        // The slider's top position means "and above".
        return Number(Math.round(value)).toLocaleString(Qt.locale(), "f", 0) + (value >= bandSlider.to ? "+ ft" : " ft")
    }
    // Altitude filter changes are saved when a handle is released, not on every drag step.
    function commitBand() {
        app.setAltitudeFilter(bandSwitch.checked, bandSlider.first.value, bandSlider.second.value, unknownCheck.checked)
    }

    component SectionLabel: Text {
        Layout.topMargin: 4
        Layout.bottomMargin: 1
        color: Theme.accent
        font.pixelSize: 11
        font.weight: Font.Bold
        font.letterSpacing: 0.8
        font.capitalization: Font.AllUppercase
    }
    component Divider: Rectangle {
        Layout.fillWidth: true
        Layout.topMargin: 6
        Layout.preferredHeight: 1
        color: Theme.border
    }
    component LayerSwitch: ASwitch {
        required property var modelData
        Layout.fillWidth: true
        text: modelData.label
        subtitle: modelData.note
        checked: app.layers[modelData.key] === true
        onToggled: app.setLayer(modelData.key, checked)
    }
    component SliderTrack: Rectangle {
        property var control
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 4
        radius: 2
        color: Theme.border
        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: 2
            color: Theme.accent
        }
    }
    component SliderHandle: Rectangle {
        property var control
        property real position: 0
        property bool down: control.pressed === true
        x: control.leftPadding + position * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 18
        height: 18
        radius: 9
        color: "#ffffff"
        border.color: Theme.accent
        border.width: down ? 3 : 2
    }

    readonly property var styles: [
        { value: "Follow app", label: "Match app", note: "Follows the Dark/Light appearance", swatch: Theme.dark ? "#09111f" : "#e6edf6", ink: Theme.dark ? "#4b6a96" : "#6d86aa" },
        { value: "Dark", label: "Night map", note: "Deep navy map, always", swatch: "#09111f", ink: "#4b6a96" },
        { value: "Light", label: "Day map", note: "Bright map, always", swatch: "#e6edf6", ink: "#6d86aa" },
        { value: "Scope only", label: "Radar scope", note: "Green PPI with animated sweep", swatch: "#03130a", ink: "#3fbf6a" }
    ]

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        y: 7
        spacing: 3

        IconButton {
            iconName: "plus"
            tip: "Zoom in"
            onClicked: toolbar.mapItem.zoomIn()
        }
        IconButton {
            iconName: "minus"
            tip: "Zoom out"
            onClicked: toolbar.mapItem.zoomOut()
        }
        Rectangle { width: 28; height: 1; color: Theme.border; anchors.horizontalCenter: parent.horizontalCenter }
        IconButton {
            iconName: "locate"
            tip: "Center on your station"
            onClicked: app.centerHome()
        }
        IconButton {
            id: layersButton
            iconName: "layers"
            tip: "Map layers"
            active: layersPop.opened
            onClicked: layersPop.opened ? layersPop.close() : layersPop.open()
            Popover {
                id: layersPop
                title: "Map layers"
                x: -width - 14
                y: -8
                width: 334
                // Scrolls when the window is too short for every control.
                readonly property real available: toolbar.parent
                    ? toolbar.parent.height - (toolbar.y + column.y + layersButton.y - 8) - 14 - 62 : 600
                Flickable {
                    id: layersFlick
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(layersColumn.implicitHeight, Math.max(160, layersPop.available))
                    contentWidth: width
                    contentHeight: layersColumn.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    readonly property bool scrolls: contentHeight > height + 1
                    ScrollBar.vertical: ScrollBar {
                        policy: layersFlick.scrolls ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                        width: 6
                        contentItem: Rectangle { implicitWidth: 4; radius: 2; color: Theme.borderStrong }
                        background: Item {}
                    }

                    ColumnLayout {
                        id: layersColumn
                        width: layersFlick.width - (layersFlick.scrolls ? 12 : 0)
                        spacing: 4

                        SectionLabel { text: "Traffic" }
                        Repeater {
                            model: [
                                { key: "aircraft", label: "Aircraft", note: "Colored by altitude" },
                                { key: "vessels", label: "Vessels", note: "AIS targets" },
                                { key: "trails", label: "Track trails", note: "Older segments fade" },
                                { key: "labels", label: "Target labels", note: "Selected and emergencies always labeled" }
                            ]
                            delegate: LayerSwitch {}
                        }

                        Divider {}
                        SectionLabel { text: "Overlays" }
                        Repeater {
                            model: [
                                { key: "rings", label: "Range rings", note: "Around your station" },
                                { key: "airports", label: "Airports", note: "Hover a symbol for codes and frequencies" },
                                { key: "coverage", label: "Coverage", note: "Farthest reception in each 10° sector" },
                                { key: "headings", label: "Heading lines", note: "Where each target will be at its current speed" }
                            ]
                            delegate: LayerSwitch {}
                        }
                        RowLayout {
                            id: headingRow
                            Layout.fillWidth: true
                            Layout.leftMargin: 49
                            spacing: 10
                            enabled: app.layers.headings === true
                            opacity: enabled ? 1 : 0.45
                            Text { text: "Length"; color: Theme.textDim; font.pixelSize: 12 }
                            Slider {
                                id: headingSlider
                                Layout.fillWidth: true
                                from: 1
                                to: 30
                                stepSize: 1
                                snapMode: Slider.SnapAlways
                                Accessible.name: "Heading line length in minutes"
                                function commit() {
                                    if (Math.round(value) !== app.headingMinutes)
                                        app.setHeadingMinutes(Math.round(value))
                                }
                                onMoved: if (!pressed) commit()
                                onPressedChanged: if (!pressed) commit()
                                Binding {
                                    target: headingSlider
                                    property: "value"
                                    value: app.headingMinutes
                                    when: !headingSlider.pressed
                                    restoreMode: Binding.RestoreNone
                                }
                                background: SliderTrack { control: headingSlider }
                                handle: SliderHandle { control: headingSlider; position: headingSlider.visualPosition }
                            }
                            Text {
                                Layout.preferredWidth: 44
                                horizontalAlignment: Text.AlignRight
                                text: Math.round(headingSlider.value) + " min"
                                color: Theme.text
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                font.features: { "tnum": 1 }
                            }
                        }

                        Divider {}
                        SectionLabel { text: "Altitude filter" }
                        ASwitch {
                            id: bandSwitch
                            Layout.fillWidth: true
                            text: "Only aircraft in a band"
                            subtitle: checked ? toolbar.feet(bandSlider.first.value) + " to " + toolbar.feet(bandSlider.second.value)
                                                + (unknownCheck.checked ? ", plus unknown" : "")
                                              : "Applies to the list and the map"
                            checked: app.altitudeFilter.enabled === true
                            onToggled: toolbar.commitBand()
                        }
                        RangeSlider {
                            id: bandSlider
                            Layout.fillWidth: true
                            Layout.leftMargin: 4
                            Layout.rightMargin: 4
                            from: 0
                            to: 50000
                            stepSize: 500
                            snapMode: RangeSlider.SnapAlways
                            enabled: bandSwitch.checked
                            opacity: enabled ? 1 : 0.45
                            Accessible.name: "Altitude band"
                            first.onMoved: if (!first.pressed) toolbar.commitBand()
                            second.onMoved: if (!second.pressed) toolbar.commitBand()
                            first.onPressedChanged: if (!first.pressed) toolbar.commitBand()
                            second.onPressedChanged: if (!second.pressed) toolbar.commitBand()
                            Binding {
                                target: bandSlider.first
                                property: "value"
                                value: Math.min(50000, app.altitudeFilter.min || 0)
                                when: !bandSlider.first.pressed
                                restoreMode: Binding.RestoreNone
                            }
                            Binding {
                                target: bandSlider.second
                                property: "value"
                                value: Math.min(50000, app.altitudeFilter.max === undefined ? 50000 : app.altitudeFilter.max)
                                when: !bandSlider.second.pressed
                                restoreMode: Binding.RestoreNone
                            }
                            background: Rectangle {
                                x: bandSlider.leftPadding
                                y: bandSlider.topPadding + bandSlider.availableHeight / 2 - height / 2
                                width: bandSlider.availableWidth
                                height: 4
                                radius: 2
                                color: Theme.border
                                Rectangle {
                                    x: bandSlider.first.visualPosition * parent.width
                                    width: (bandSlider.second.visualPosition - bandSlider.first.visualPosition) * parent.width
                                    height: parent.height
                                    radius: 2
                                    color: Theme.accent
                                }
                            }
                            first.handle: SliderHandle { objectName: "bandLowerHandle"; control: bandSlider; position: bandSlider.first.visualPosition; down: bandSlider.first.pressed }
                            second.handle: SliderHandle { objectName: "bandUpperHandle"; control: bandSlider; position: bandSlider.second.visualPosition; down: bandSlider.second.pressed }
                        }
                        Item {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 14
                            opacity: bandSwitch.checked ? 1 : 0.45
                            Repeater {
                                model: [0, 10000, 20000, 30000, 40000, 50000]
                                delegate: Text {
                                    required property var modelData
                                    x: Math.min(parent.width - implicitWidth, 4 + (parent.width - 8 - 9) * modelData / 50000 + 4.5 - implicitWidth / 2)
                                    text: modelData === 0 ? "0" : (modelData / 1000) + (modelData === 50000 ? "k+" : "k")
                                    color: Theme.muted
                                    font.pixelSize: 10
                                }
                            }
                        }
                        ACheck {
                            id: unknownCheck
                            Layout.fillWidth: true
                            text: "Include aircraft without altitude"
                            checked: app.altitudeFilter.include_unknown !== false
                            enabled: bandSwitch.checked
                            opacity: enabled ? 1 : 0.45
                            onToggled: toolbar.commitBand()
                        }
                    }
                }
            }
        }
        IconButton {
            id: zonesButton
            iconName: "polygon"
            tip: "Geofences"
            active: zonesPop.opened || app.zoneMode !== ""
            onClicked: zonesPop.opened ? zonesPop.close() : zonesPop.open()
            Popover {
                id: zonesPop
                title: "Geofences"
                x: -width - 14
                y: -8
                width: 310
                AButton {
                    Layout.fillWidth: true
                    text: "Draw new geofence"
                    iconName: "plus"
                    variant: "primary"
                    onClicked: { zonesPop.close(); app.beginZone() }
                }
                Text {
                    visible: app.zones.length === 0
                    Layout.fillWidth: true
                    text: "No saved geofences yet. Draw a polygon, name it, then use it in an Enters/Leaves geofence alert."
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.topMargin: 4
                }
                Repeater {
                    model: app.zones
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 50
                        radius: 10
                        color: zoneArea.containsMouse ? Theme.hover : Theme.raised
                        border.color: Theme.border
                        MouseArea { id: zoneArea; anchors.fill: parent; hoverEnabled: true }
                        Column {
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            anchors.right: zoneActions.left
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 1
                            Text {
                                text: modelData.name
                                color: Theme.text
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                width: parent.width
                            }
                            Text {
                                text: modelData.points + " points · " + modelData.rules + (modelData.rules === 1 ? " alert" : " alerts")
                                color: Theme.muted
                                font.pixelSize: 11
                            }
                        }
                        Row {
                            id: zoneActions
                            anchors.right: parent.right
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            IconButton { iconName: "locate"; tip: "Show on map"; size: 30; iconSize: 16; onClicked: { zonesPop.close(); app.showZone(modelData.name) } }
                            IconButton { iconName: "edit"; tip: "Rename"; size: 30; iconSize: 16; onClicked: { zonesPop.close(); app.editZone(modelData.name, "rename") } }
                            IconButton { iconName: "trash"; tip: "Remove…"; size: 30; iconSize: 16; tint: Theme.danger; onClicked: { zonesPop.close(); app.editZone(modelData.name, "remove") } }
                        }
                    }
                }
            }
        }
        IconButton {
            id: styleButton
            iconName: app.mapTheme === "Scope only" ? "radar" : "map"
            tip: "Map style"
            active: stylePop.opened
            onClicked: stylePop.opened ? stylePop.close() : stylePop.open()
            Popover {
                id: stylePop
                title: "Map style"
                x: -width - 14
                y: -8
                width: 300
                Repeater {
                    model: toolbar.styles
                    delegate: Rectangle {
                        id: styleCard
                        required property var modelData
                        readonly property bool current: app.mapTheme === modelData.value
                        Layout.fillWidth: true
                        Layout.preferredHeight: 52
                        radius: 10
                        color: current ? Theme.accentSoft : styleArea.containsMouse ? Theme.hover : "transparent"
                        border.color: current ? Util.tint(Theme.accent, 0.6) : Theme.border
                        Rectangle {
                            id: swatch
                            width: 40
                            height: 34
                            radius: 8
                            x: 9
                            anchors.verticalCenter: parent.verticalCenter
                            color: styleCard.modelData.swatch
                            border.color: Theme.border
                            clip: true
                            Rectangle { width: 26; height: 26; radius: 13; anchors.centerIn: parent; color: "transparent"; border.color: styleCard.modelData.ink; border.width: 1.2 }
                            Rectangle { width: 12; height: 12; radius: 6; anchors.centerIn: parent; color: "transparent"; border.color: styleCard.modelData.ink; border.width: 1.2 }
                        }
                        Column {
                            anchors.left: swatch.right
                            anchors.leftMargin: 11
                            anchors.verticalCenter: parent.verticalCenter
                            Text { text: styleCard.modelData.label; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Text { text: styleCard.modelData.note; color: Theme.muted; font.pixelSize: 11 }
                        }
                        Icon {
                            visible: styleCard.current
                            name: "check"
                            size: 16
                            color: Theme.accent
                            anchors.right: parent.right
                            anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        MouseArea {
                            id: styleArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: app.setMapTheme(styleCard.modelData.value)
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    Layout.topMargin: 2
                    text: app.tilesEnabled ? "OpenStreetMap street tiles are on. Turn them off in Settings → Map."
                                           : "Offline grid. Enable OpenStreetMap street tiles in Settings → Map."
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
        }
        Rectangle { width: 28; height: 1; color: Theme.border; anchors.horizontalCenter: parent.horizontalCenter }
        IconButton {
            iconName: "fullscreen"
            tip: "Full screen (F11)"
            onClicked: toolbar.fullscreenRequested()
        }
    }
}
