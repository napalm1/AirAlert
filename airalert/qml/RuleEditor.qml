import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Util.js" as Util

AModal {
    id: editor
    property int index: -1
    property string ruleId: ""
    property string name: ""
    property bool ruleOn: true
    property string kind: "aircraft"
    property string field: "registration"
    property string value: ""
    property string condition: "enter"
    property string threshold: "25"
    property string thresholdUnit: "distance"   // what `threshold` currently measures
    property string zone: ""
    property string missingZone: ""
    property string missingExtraZone: ""
    property string cooldown: "60"
    property bool sound: true
    property bool desktop: true
    property bool speak: false
    property bool phone: false
    property bool overrideQuiet: false
    property string lookahead: "5"
    property var extras: []
    property int extrasRevision: 0
    property var options: ({ kinds: [], fields: [], conditions: [], extras: [], zones: [], units: "mi" })
    property string error: ""
    readonly property bool usesDistance: condition === "enter" || condition === "leave" || condition === "approach"
    readonly property bool usesZone: condition === "zone_enter" || condition === "zone_leave"
    readonly property bool usesThreshold: usesDistance || condition === "altitude_below" || condition === "speed_above" || condition === "rare_type"
    readonly property bool usesTrait: ["first_aircraft", "first_type", "first_operator", "rare_type", "military", "circling"].indexOf(condition) >= 0
    readonly property string sentence: extrasRevision >= 0 ? app.ruleSentence(form()) : ""

    function extraUnit(conditionValue) {
        var option = options.extras.find(function(e) { return e.value === conditionValue })
        return option ? option.unit : ""
    }
    function unitOf(conditionValue) {
        return conditionValue === "enter" || conditionValue === "leave" || conditionValue === "approach" ? "distance"
             : conditionValue === "altitude_below" ? "ft" : conditionValue === "speed_above" ? "kn"
             : conditionValue === "rare_type" ? "count" : ""
    }
    // A starting value when a condition measures something else (25 miles must not become 25 feet).
    function defaultFor(unit, conditionValue) {
        return unit === "distance" ? (conditionValue === "approach" ? "3" : conditionValue === "within" || conditionValue === "beyond" ? "10" : "25")
             : unit === "ft" ? "3000" : unit === "kn" ? "250" : unit === "count" ? "3" : "0"
    }
    function pickCondition(value) {
        condition = value
        var unit = unitOf(value)
        if (unit !== "" && unit !== thresholdUnit) {
            threshold = defaultFor(unit, value)
            thresholdUnit = unit
        }
    }
    function addExtra() {
        extras = extras.concat([{ condition: "altitude_below", threshold: "3000", zone: options.zones.length ? options.zones[0] : "" }])
        extrasRevision += 1
    }
    function removeExtra(i) {
        var copy = extras.slice()
        copy.splice(i, 1)
        extras = copy
        extrasRevision += 1
    }
    // Edits change the objects in place so the row being typed in is not rebuilt.
    function setExtra(i, key, v) {
        extras[i][key] = v
        extrasRevision += 1
    }

    title: index < 0 ? "New alert" : "Edit alert"
    subtitle: "Checked against every decoded update. Entry fires once while inside; leaving and returning re-arms it."
    iconName: "bell"
    iconColor: Theme.alert
    width: 640
    height: Math.min(Overlay.overlay ? Overlay.overlay.height - 60 : 760, 760)

    function openWith(i, data) {
        options = app.ruleOptions()
        index = i
        ruleId = data.id || ""
        name = data.name || ""
        ruleOn = data.enabled !== false
        kind = data.kind || "aircraft"
        field = data.field || "registration"
        value = data.value || ""
        condition = data.condition || "enter"
        threshold = String(data.threshold !== undefined ? data.threshold : 25)
        thresholdUnit = unitOf(condition)
        // A rule whose geofence was removed shows (and would save) the first existing zone.
        missingZone = data.zone && options.zones.indexOf(data.zone) < 0 ? data.zone : ""
        zone = data.zone && options.zones.indexOf(data.zone) >= 0 ? data.zone
             : (options.zones.length ? options.zones[0] : "")
        cooldown = String(data.cooldown !== undefined ? data.cooldown : 60)
        sound = data.sound !== false
        desktop = data.desktop !== false
        speak = data.speak === true
        phone = data.phone === true
        overrideQuiet = data.override_quiet === true
        lookahead = String(data.lookahead !== undefined ? data.lookahead : 5)
        // Inside/outside conditions whose geofence was removed show (and would save) the first existing zone.
        var missing = []
        extras = (data.extra || []).map(function(e) {
            var zoneName = e.zone || ""
            if (extraUnit(e.condition) === "zone" && options.zones.indexOf(zoneName) < 0) {
                if (zoneName !== "") missing.push(zoneName)
                zoneName = options.zones.length ? options.zones[0] : ""
            }
            return { condition: e.condition, threshold: String(e.threshold !== undefined ? e.threshold : 0), zone: zoneName }
        })
        missingExtraZone = missing.join(", ")
        extrasRevision += 1
        error = ""
        fieldBox.currentIndex = Math.max(0, fieldBox.indexOfValue(field))
        conditionBox.currentIndex = Math.max(0, conditionBox.indexOfValue(condition))
        zoneBox.currentIndex = Math.max(0, options.zones.indexOf(zone))
        scroller.contentY = 0
        open()
        nameField.forceActiveFocus()
    }

    function form() {
        return { id: ruleId, name: name, enabled: ruleOn, kind: kind, field: field, value: value,
                 condition: condition, threshold: threshold, zone: usesZone ? zone : "", cooldown: cooldown,
                 sound: sound, desktop: desktop, speak: speak, phone: phone, override_quiet: overrideQuiet,
                 lookahead: lookahead, extra: extras.map(function(e) {
                     return { condition: e.condition, threshold: e.threshold, zone: e.zone } }) }
    }

    function save() {
        var result = app.saveRule(index, form())
        if (result !== "") error = result
        else close()
    }

    Flickable {
        id: scroller
        anchors.fill: parent
        contentHeight: formColumn.implicitHeight + 36
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: formColumn
            x: 24
            y: 18
            width: scroller.width - 48
            spacing: 16

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: previewRow.implicitHeight + 24
                radius: 12
                color: Theme.alertSoft
                border.color: Util.tint(Theme.alert, 0.35)
                RowLayout {
                    id: previewRow
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    Icon { name: "bell"; size: 20; color: Theme.alert; Layout.alignment: Qt.AlignTop }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: "ALERT ME WHEN"; color: Theme.alert; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.1 }
                        Text {
                            Layout.fillWidth: true
                            text: editor.sentence
                            color: Theme.text
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            FormRow {
                label: "Name"
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    AField {
                        id: nameField
                        Layout.fillWidth: true
                        text: editor.name
                        placeholderText: "e.g. N123AB nearby"
                        onTextEdited: editor.name = text
                    }
                    ASwitch {
                        text: "Enabled"
                        checked: editor.ruleOn
                        onToggled: editor.ruleOn = checked
                    }
                }
            }

            FormRow {
                label: "Target type"
                Segmented {
                    options: [{ value: "aircraft", label: "Aircraft" }, { value: "vessel", label: "Vessel" }, { value: "any", label: "Any target" }]
                    value: editor.kind
                    onPicked: (v) => editor.kind = v
                }
            }

            FormRow {
                label: "Identity"
                hint: "Exact, case-insensitive match. Leave the value blank to match every target of this type. Registration matching needs an ICAO → registration lookup (Watchlist page)."
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    ACombo {
                        id: fieldBox
                        Layout.preferredWidth: 230
                        model: editor.options.fields
                        textRole: "label"
                        valueRole: "value"
                        onActivated: editor.field = currentValue
                    }
                    AField {
                        Layout.fillWidth: true
                        text: editor.value
                        placeholderText: "Blank matches all — e.g. N123AB"
                        onTextEdited: editor.value = text
                    }
                }
            }

            FormRow {
                label: "Condition"
                ACombo {
                    id: conditionBox
                    Layout.fillWidth: true
                    model: editor.options.conditions
                    textRole: "label"
                    valueRole: "value"
                    onActivated: editor.pickCondition(currentValue)
                }
            }

            FormRow {
                visible: editor.usesThreshold
                label: editor.condition === "approach" ? "Predicted closest distance and look-ahead"
                     : editor.usesDistance ? "Distance from home" : editor.condition === "altitude_below" ? "Altitude"
                     : editor.condition === "rare_type" ? "Rare means seen fewer than" : "Speed"
                hint: editor.condition === "approach"
                      ? "Uses each target's current track and speed to predict how close it will come to your station. Fires once per pass, before it arrives."
                      : editor.usesDistance ? "Great-circle distance from your station location. Stale positions (older than 60 s) never trigger." : ""
                RowLayout {
                    spacing: 10
                    AField {
                        Layout.preferredWidth: 200
                        text: editor.threshold
                        suffix: editor.usesDistance ? editor.options.units : editor.condition === "altitude_below" ? "feet"
                                : editor.condition === "rare_type" ? "times in 30 days" : "knots"
                        validator: DoubleValidator { bottom: 0 }
                        onTextEdited: editor.threshold = text
                    }
                    Text {
                        visible: editor.condition === "approach"
                        text: "within the next"
                        color: Theme.muted
                        font.pixelSize: 13
                    }
                    AField {
                        visible: editor.condition === "approach"
                        Layout.preferredWidth: 150
                        text: editor.lookahead
                        suffix: "minutes"
                        validator: IntValidator { bottom: 1; top: 60 }
                        onTextEdited: editor.lookahead = text
                    }
                }
            }

            InfoBox {
                visible: editor.usesTrait
                text: editor.condition === "military"
                      ? "Recognized from the address blocks that countries reserve for military aircraft and from the operator in the aircraft database. Not every military aircraft can be recognized."
                      : editor.condition === "circling"
                        ? "Fires when an aircraft turns a full circle in one direction while staying in a small area, as helicopters and survey flights do. Aircraft only."
                        : "Compared with your own History on this computer, so it gets more selective the longer AirAlert runs. Aircraft only. Type and airline alerts need the aircraft database or a callsign."
            }

            InfoBox {
                visible: editor.condition === "squawk_emergency"
                tone: Theme.danger
                iconName: "warning"
                text: "Fires when an aircraft sets transponder code 7500 (hijack), 7600 (radio failure) or 7700 (emergency). These are rare \u2014 consider letting this alert ignore quiet hours."
            }

            FormRow {
                visible: editor.usesZone
                label: "Geofence"
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    ACombo {
                        id: zoneBox
                        visible: editor.options.zones.length > 0
                        Layout.fillWidth: true
                        model: editor.options.zones
                        onActivated: editor.zone = currentText
                    }
                    Text {
                        visible: editor.missingZone !== ""
                        Layout.fillWidth: true
                        text: "This alert used “" + editor.missingZone + "”, which no longer exists. Choose a geofence and save to reactivate it."
                        color: Theme.danger
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        visible: editor.options.zones.length === 0
                        spacing: 10
                        Text {
                            text: "No geofences yet. Draw one on the map first."
                            color: Theme.muted
                            font.pixelSize: 13
                        }
                        AButton {
                            text: "Draw a geofence"
                            iconName: "polygon"
                            compact: true
                            onClicked: { editor.close(); app.beginZone() }
                        }
                    }
                }
            }

            FormRow {
                label: "Cooldown"
                hint: "Minimum time between notifications for the same target. A crossing suppressed by cooldown is not delayed into a later alert."
                AField {
                    Layout.preferredWidth: 220
                    text: editor.cooldown
                    suffix: "seconds"
                    validator: IntValidator { bottom: 0; top: 86400 }
                    onTextEdited: editor.cooldown = text
                }
            }

            FormRow {
                label: "Only when also\u2026"
                hint: editor.extras.length ? "Every condition must be true at the same time for the alert to fire."
                                           : "Optional: narrow the alert, e.g. only below 3,000 ft or only inside a geofence."
                Repeater {
                    model: editor.extras
                    delegate: RowLayout {
                        id: extraRow
                        required property var modelData
                        required property int index
                        property string kindNow: modelData.condition
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "and"; color: Theme.muted; font.pixelSize: 13; Layout.preferredWidth: 26 }
                        ACombo {
                            Layout.preferredWidth: 230
                            model: editor.options.extras
                            textRole: "label"
                            valueRole: "value"
                            Component.onCompleted: currentIndex = Math.max(0, indexOfValue(extraRow.modelData.condition))
                            onActivated: {
                                var before = editor.extraUnit(extraRow.kindNow)
                                var after = editor.extraUnit(currentValue)
                                editor.setExtra(extraRow.index, "condition", currentValue)
                                extraRow.kindNow = currentValue
                                if (after !== "zone" && after !== before) {
                                    var start = editor.defaultFor(after, currentValue)
                                    extraValue.text = start
                                    editor.setExtra(extraRow.index, "threshold", start)
                                }
                            }
                        }
                        AField {
                            id: extraValue
                            visible: editor.extraUnit(extraRow.kindNow) !== "zone"
                            Layout.fillWidth: true
                            text: extraRow.modelData.threshold
                            suffix: {
                                var unit = editor.extraUnit(extraRow.kindNow)
                                return unit === "distance" ? editor.options.units : unit === "ft" ? "feet" : "knots"
                            }
                            validator: DoubleValidator { bottom: 0 }
                            onTextEdited: editor.setExtra(extraRow.index, "threshold", text)
                        }
                        ACombo {
                            visible: editor.extraUnit(extraRow.kindNow) === "zone"
                            Layout.fillWidth: true
                            model: editor.options.zones
                            Component.onCompleted: currentIndex = Math.max(0, editor.options.zones.indexOf(extraRow.modelData.zone))
                            onActivated: editor.setExtra(extraRow.index, "zone", currentText)
                        }
                        IconButton {
                            iconName: "close"
                            tip: "Remove this condition"
                            size: 30
                            iconSize: 15
                            onClicked: editor.removeExtra(extraRow.index)
                        }
                    }
                }
                Text {
                    visible: editor.missingExtraZone !== ""
                    Layout.fillWidth: true
                    text: "A condition used “" + editor.missingExtraZone + "”, which no longer exists. Check the geofence chosen above and save to reactivate the alert."
                    color: Theme.danger
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
                AButton {
                    visible: editor.extras.length < 6
                    text: editor.extras.length ? "Add another condition" : "Add a condition"
                    iconName: "plus"
                    compact: true
                    variant: "ghost"
                    onClicked: editor.addExtra()
                }
            }

            FormRow {
                label: "Notify me with"
                hint: "An in-app alert and a History event are always recorded."
                GridLayout {
                    columns: 3
                    columnSpacing: 22
                    rowSpacing: 4
                    ACheck { text: "Sound"; iconName: "sound"; checked: editor.sound; onToggled: editor.sound = checked }
                    ACheck { text: "Desktop pop-up"; iconName: "monitor"; checked: editor.desktop; onToggled: editor.desktop = checked }
                    ACheck { text: "Speak aloud"; iconName: "sound"; checked: editor.speak; onToggled: editor.speak = checked }
                    ACheck { text: "Phone"; iconName: "bell"; checked: editor.phone; onToggled: editor.phone = checked }
                    ACheck {
                        Layout.columnSpan: 2
                        text: "Ignore quiet hours"
                        iconName: "moon"
                        checked: editor.overrideQuiet
                        onToggled: editor.overrideQuiet = checked
                    }
                }
                Text {
                    visible: editor.phone && !editor.options.phoneConfigured
                    Layout.fillWidth: true
                    text: "Phone alerts need ntfy or Pushover set up in Settings \u2192 Notifications."
                    color: Theme.alert
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    footer: [
        Text {
            Layout.fillWidth: true
            text: editor.error
            color: Theme.danger
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        },
        AButton { text: "Cancel"; variant: "ghost"; onClicked: editor.close() },
        AButton { text: editor.index < 0 ? "Create alert" : "Save changes"; variant: "primary"; iconName: "check"; onClicked: editor.save() }
    ]
}
