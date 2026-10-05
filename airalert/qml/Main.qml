import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: win
    width: 1480
    height: 920
    minimumWidth: 1120
    minimumHeight: 700
    visible: false
    title: "AirAlert"
    color: Theme.bg
    property int page: 0
    readonly property bool modalOpen: ruleEditor.opened || watchEditor.opened || settingsDialog.opened || maintenance.opened
                                      || noticeDialog.opened || receptionDialog.opened

    Binding { target: Theme; property: "dark"; value: app.theme === "Dark" }
    onPageChanged: app.setPage(page)
    // Closing keeps AirAlert running in the tray (Settings → Startup), so alerts keep working.
    onClosing: (close) => {
        if (!app.allowClose()) {
            close.accepted = false
            win.hide()
        }
    }

    function toggleFullscreen() {
        if (win.visibility === Window.FullScreen) win.showNormal()
        else win.showFullScreen()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        TopBar {
            Layout.fillWidth: true
            page: win.page
            onPageRequested: (index) => win.page = index
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.page
            LivePage {
                id: live
                onFullscreenRequested: win.toggleFullscreen()
                onShowAlerts: win.page = 1
            }
            AlertsPage {
                onShowLive: win.page = 0
            }
            HistoryPage {
                onMaintenanceRequested: maintenance.openDialog()
            }
            WatchlistPage {}
            StatsPage {}
        }
        StatusBar { Layout.fillWidth: true }
    }

    RuleEditor { id: ruleEditor; objectName: "ruleEditor" }
    WatchEditor { id: watchEditor; objectName: "watchEditor" }
    SettingsDialog { id: settingsDialog; objectName: "settingsDialog" }
    MaintenanceDialog { id: maintenance; objectName: "maintenanceDialog" }
    NoticeDialog { id: noticeDialog; objectName: "noticeDialog" }
    ReceptionDialog { id: receptionDialog; objectName: "receptionDialog" }

    ToastHost {
        id: toasts
        anchors.horizontalCenter: parent.horizontalCenter
        y: 74
        z: 100
    }

    Connections {
        target: app
        function onToast(text, kind) { toasts.show(text, kind) }
        function onAlertRaised(text, key, time) {
            // On the live map the alert ribbon flashes instead.
            if (win.page !== 0 || win.modalOpen)
                toasts.showAlert(text)
        }
        function onRequestPage(index) { win.page = index }
        function onOpenRuleEditor(index, data) { ruleEditor.openWith(index, data) }
        function onOpenWatchEditor(index, data) { watchEditor.openWith(index, data) }
        function onOpenSettings(first, section) { settingsDialog.openWith(first, section) }
        function onNotice(title, text) { noticeDialog.show(title, text) }
    }

    Shortcut { sequence: "F11"; onActivated: win.toggleFullscreen() }
    Shortcut {
        sequence: "Escape"
        enabled: !win.modalOpen
        onActivated: {
            if (win.page === 0 && live.clearSearch())
                return
            if (!app.escape() && win.visibility === Window.FullScreen)
                win.showNormal()
        }
    }
    Shortcut {
        sequences: ["Return", "Enter"]
        enabled: app.zoneMode === "draw" && !win.modalOpen
        onActivated: app.finishZone()
    }
    Shortcut { sequence: "Ctrl+F"; enabled: !win.modalOpen; onActivated: { win.page = 0; live.focusSearch() } }
    Shortcut { sequence: "Ctrl+,"; enabled: !win.modalOpen; onActivated: app.requestSettings("general") }
}
