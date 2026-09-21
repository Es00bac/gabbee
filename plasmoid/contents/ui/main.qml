import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    switchWidth: 1
    switchHeight: 1
    preferredRepresentation: fullRepresentation
    Plasmoid.icon: "gabbee"
    Plasmoid.status: backendOnline ? PlasmaCore.Types.ActiveStatus : PlasmaCore.Types.PassiveStatus
    toolTipMainText: "Gabbee"
    toolTipSubText: backendOnline ? providerText : "Backend offline"

    property string currentState: "IDLE"
    property string providerText: ""
    property string lastText: ""
    property string errorMessage: ""
    property bool backendOnline: false

    function postAction(action) {
        var xhr = new XMLHttpRequest();
        xhr.open("POST", "http://127.0.0.1:28765/" + action);
        xhr.onreadystatechange = function() {
            if (xhr.readyState === XMLHttpRequest.DONE) {
                root.refreshState();
            }
        };
        xhr.onerror = function() {
            root.backendOnline = false;
            root.errorMessage = "Backend offline";
        };
        xhr.send();
    }

    function refreshState() {
        var xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE) {
                return;
            }
            if (xhr.status === 200) {
                try {
                    var data = JSON.parse(xhr.responseText);
                    root.backendOnline = true;
                    root.currentState = data.state || "IDLE";
                    root.providerText = data.provider || "";
                    root.lastText = data.last_text || "";
                    root.errorMessage = data.error_message || "";
                } catch (e) {
                    root.backendOnline = false;
                    root.errorMessage = "Bad backend response";
                }
            } else {
                root.backendOnline = false;
                root.errorMessage = "Backend offline";
            }
        };
        xhr.onerror = function() {
            root.backendOnline = false;
            root.errorMessage = "Backend offline";
        };
        xhr.open("GET", "http://127.0.0.1:28765/state");
        xhr.send();
    }

    Timer {
        interval: 200
        running: true
        repeat: true
        onTriggered: root.refreshState()
    }

    Component.onCompleted: root.refreshState()

    compactRepresentation: CompactRepresentation {
        plasmoidItem: root
    }

    fullRepresentation: CompactRepresentation {
        plasmoidItem: root
    }
}
