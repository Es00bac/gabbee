pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import org.kde.plasma.plasmoid

Item {
    id: root

    required property PlasmoidItem plasmoidItem

    implicitWidth: mainRow.implicitWidth + 8
    implicitHeight: 28
    clip: true

    Layout.fillHeight: true
    Layout.minimumWidth: implicitWidth
    Layout.preferredWidth: implicitWidth
    Layout.maximumWidth: implicitWidth
    Layout.minimumHeight: 1
    Layout.preferredHeight: implicitHeight

    Rectangle {
        id: card
        anchors.fill: parent
        anchors.margins: 0
        color: "#101820"
        border.color: "#62a196"
        border.width: 1
        radius: 0

        RowLayout {
            id: mainRow
            anchors.fill: parent
            anchors.margins: 0
            anchors.leftMargin: 4
            anchors.rightMargin: 4
            spacing: 4

            Text {
                text: "Gabbee"
                color: "#ecf4f1"
                font.pixelSize: 11
                font.bold: true
                Layout.alignment: Qt.AlignVCenter
            }

            Rectangle {
                id: statusChip
                color: {
                    var c = {
                        "IDLE": "#2fbf9f",
                        "RECORDING": "#d95d39",
                        "TRANSCRIBING": "#d1a208",
                        "DELIVERING": "#4b9cd3",
                        "ERROR": "#b43e5a"
                    };
                    return root.plasmoidItem.backendOnline ? (c[root.plasmoidItem.currentState] || "#51636a") : "#51636a";
                }
                radius: Math.min(8, height / 2)
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.preferredWidth: statusText.implicitWidth + 12
                Layout.alignment: Qt.AlignVCenter

                Text {
                    id: statusText
                    anchors.centerIn: parent
                    text: {
                        var l = {
                            "IDLE": "Idle",
                            "RECORDING": "Recording",
                            "TRANSCRIBING": "Transcribing",
                            "DELIVERING": "Delivering",
                            "ERROR": "Error"
                        };
                        if (!root.plasmoidItem.backendOnline) {
                            return "Offline";
                        }
                        return l[root.plasmoidItem.currentState] || root.plasmoidItem.currentState;
                    }
                    color: "white"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                }
            }

            Button {
                id: pinBtn
                text: checked ? "Pinned" : "Pin"
                checkable: true
                checked: true
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.alignment: Qt.AlignVCenter

                contentItem: Text {
                    text: pinBtn.text
                    color: "white"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    color: pinBtn.checked ? "#1d7f73" : "#51636a"
                    radius: Math.min(6, height / 3)
                }
            }

            Button {
                id: settingsBtn
                text: "\u2699"
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.preferredWidth: Math.max(22, root.height)
                Layout.alignment: Qt.AlignVCenter

                contentItem: Text {
                    text: settingsBtn.text
                    color: "white"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    color: settingsBtn.hovered ? "#333333" : "#222222"
                    radius: Math.min(6, height / 3)
                }
            }

            Rectangle {
                id: previewLabel
                visible: root.plasmoidItem.lastText !== "" || root.plasmoidItem.errorMessage !== ""
                color: "#1a1a1a"
                radius: Math.min(5, height / 3)
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.minimumWidth: 60
                Layout.preferredWidth: previewText.implicitWidth + 10
                Layout.maximumWidth: 180
                Layout.alignment: Qt.AlignVCenter

                Text {
                    id: previewText
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 4
                    anchors.right: parent.right
                    anchors.rightMargin: 4
                    text: root.plasmoidItem.errorMessage !== "" ? root.plasmoidItem.errorMessage : root.plasmoidItem.lastText
                    color: root.plasmoidItem.errorMessage !== "" ? "#ff8888" : "#ecf4f1"
                    font.pixelSize: 9
                    elide: Text.ElideRight
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                id: startBtn
                text: "Start"
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.alignment: Qt.AlignVCenter
                enabled: root.plasmoidItem.backendOnline
                    && (root.plasmoidItem.currentState === "IDLE" || root.plasmoidItem.currentState === "ERROR")
                onClicked: root.plasmoidItem.postAction("start")

                contentItem: Text {
                    text: startBtn.text
                    color: "white"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    color: startBtn.enabled ? "#1d7f73" : "#51636a"
                    radius: Math.min(6, height / 3)
                }
            }

            Button {
                id: stopBtn
                text: "Stop"
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.alignment: Qt.AlignVCenter
                enabled: root.plasmoidItem.backendOnline && root.plasmoidItem.currentState === "RECORDING"
                onClicked: root.plasmoidItem.postAction("stop")

                contentItem: Text {
                    text: stopBtn.text
                    color: "white"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    color: stopBtn.enabled ? "#1d7f73" : "#51636a"
                    radius: Math.min(6, height / 3)
                }
            }

            Button {
                id: cancelBtn
                text: "Cancel"
                Layout.fillHeight: true
                Layout.minimumHeight: 18
                Layout.alignment: Qt.AlignVCenter
                enabled: root.plasmoidItem.backendOnline && root.plasmoidItem.currentState === "RECORDING"
                onClicked: root.plasmoidItem.postAction("cancel")

                contentItem: Text {
                    text: cancelBtn.text
                    color: "white"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    color: cancelBtn.enabled ? "#1d7f73" : "#51636a"
                    radius: Math.min(6, height / 3)
                }
            }
        }
    }
}
