import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1440
    height: 900
    minimumWidth: 1040
    minimumHeight: 680
    visible: true
    title: studio.projectName + " — Wan2Lab"
    color: "#11151c"

    palette {
        window: "#11151c"
        windowText: "#e8edf5"
        base: "#191f29"
        text: "#e8edf5"
        button: "#273142"
        buttonText: "#f4f7fb"
        highlight: "#6957d9"
        highlightedText: "#ffffff"
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            Label { text: "WAN2LAB"; font.bold: true; font.pixelSize: 18 }
            Label { text: studio.projectName; color: "#aeb9cb" }
            Item { Layout.fillWidth: true }
            Button { text: "New"; onClicked: studio.newProject(18) }
            Button { text: "Plan"; onClicked: studio.planMockTimeline() }
            Button { text: "Generate next"; onClicked: studio.generateNextMockSegment() }
            Button { text: "Approve"; onClicked: studio.approveCurrentSegment() }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        Frame {
            Layout.preferredWidth: 250
            Layout.fillHeight: true
            background: Rectangle { color: "#191f29"; radius: 8 }
            ColumnLayout {
                anchors.fill: parent
                Label { text: "Project & Assets"; font.bold: true }
                Label { text: "Character sheets"; color: "#aeb9cb" }
                Label { text: "Keyframes"; color: "#aeb9cb" }
                Label { text: "Mannequin scenes"; color: "#aeb9cb" }
                Label { text: "Generated media"; color: "#aeb9cb" }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#344052" }
                Label { text: "Runtime"; font.bold: true }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: studio.runtimeVersions
                    color: "#8dd7c4"
                }
                Item { Layout.fillHeight: true }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            Frame {
                Layout.fillWidth: true
                Layout.fillHeight: true
                background: Rectangle { color: "#090c11"; radius: 8 }
                ColumnLayout {
                    anchors.centerIn: parent
                    Label { text: "Video / Keyframe Preview"; font.pixelSize: 22 }
                    Label {
                        text: "Review player, frame strip, Krea edits, and mannequin viewport"
                        color: "#7f8ca0"
                    }
                }
            }

            Frame {
                Layout.fillWidth: true
                Layout.preferredHeight: 190
                background: Rectangle { color: "#191f29"; radius: 8 }
                ColumnLayout {
                    anchors.fill: parent
                    RowLayout {
                        Label { text: "Timeline"; font.bold: true }
                        Label { text: studio.durationSeconds.toFixed(1) + " s"; color: "#aeb9cb" }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: studio.approvedSegmentCount + " / " + studio.segmentCount + " approved"
                            color: "#8dd7c4"
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 5
                        color: "#10151d"
                        border.color: "#344052"
                        Label {
                            anchors.centerIn: parent
                            text: studio.segmentCount === 0
                                ? "Plan the exact-time timeline"
                                : studio.segmentCount + " bounded segment block(s)"
                            color: "#aeb9cb"
                        }
                    }
                }
            }
        }

        Frame {
            Layout.preferredWidth: 310
            Layout.fillHeight: true
            background: Rectangle { color: "#191f29"; radius: 8 }
            ColumnLayout {
                anchors.fill: parent
                Label { text: "Context Inspector"; font.bold: true }
                Label { text: "Mode and backend parameters"; color: "#aeb9cb" }
                Label { text: "Prompt and action controls"; color: "#aeb9cb" }
                Label { text: "Character assignments"; color: "#aeb9cb" }
                Label { text: "Review and provenance"; color: "#aeb9cb" }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#344052" }
                Label { text: "Activity"; font.bold: true }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: studio.eventLog
                    delegate: Label {
                        required property string modelData
                        width: ListView.view.width
                        wrapMode: Text.Wrap
                        text: modelData
                        color: "#aeb9cb"
                        topPadding: 3
                        bottomPadding: 3
                    }
                }
            }
        }
    }

    footer: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            Label { text: studio.status; Layout.fillWidth: true }
            Label { text: "Mock backend · no GPU work"; color: "#f1bf78" }
        }
    }
}

