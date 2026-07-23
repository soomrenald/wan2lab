import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
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

    FileDialog {
        id: sheetImageDialog
        title: "Import character-sheet image"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)"]
        onAccepted: studio.importSheetEntry(selectedFile, sheetEntryName.text)
    }

    FileDialog {
        id: saveProjectDialog
        title: "Save Wan2Lab project"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Wan2Lab project (*.wan2lab.json)", "JSON (*.json)"]
        onAccepted: studio.saveProjectFile(selectedFile)
    }

    FileDialog {
        id: openProjectDialog
        title: "Open Wan2Lab project"
        fileMode: FileDialog.OpenFile
        nameFilters: ["Wan2Lab project (*.wan2lab.json *.json)"]
        onAccepted: studio.openProjectFile(selectedFile)
    }

    FileDialog {
        id: exportVideoDialog
        title: "Export approved timeline"
        fileMode: FileDialog.SaveFile
        nameFilters: ["MP4 video (*.mp4)"]
        onAccepted: studio.exportApprovedVideo(selectedFile)
    }

    FileDialog {
        id: blenderSceneDialog
        title: "Import Blender mannequin JSON"
        nameFilters: ["Wan2Lab mannequin scene (*.json)"]
        onAccepted: studio.importBlenderScene(selectedFile)
    }

    FileDialog {
        id: keyframeImageDialog
        title: "Import keyframe image"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)"]
        onAccepted: studio.importKeyframe(selectedFile, Number(keyframeTime.text))
    }

    FileDialog {
        id: replacementFrameDialog
        title: "Choose replacement frame"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)"]
        onAccepted: studio.modifyFrame(
            selectedSegment.value,
            replacementFrameIndex.value,
            selectedFile,
            replacementPrompt.text,
            propagateBoundary.checked
        )
    }

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
            Button { text: "Open"; onClicked: openProjectDialog.open() }
            Button { text: "Save"; onClicked: saveProjectDialog.open() }
            Button { text: "Plan"; onClicked: studio.planMockTimeline() }
            Button {
                text: studio.generationRunning ? "Cancel generation" : "Generate next"
                onClicked: studio.generationRunning
                    ? studio.cancelGeneration()
                    : studio.generateNextMockSegment()
            }
            Button { text: "Approve"; onClicked: studio.approveCurrentSegment() }
            TextField {
                id: rejectionReason
                placeholderText: "Rejection reason"
                Layout.preferredWidth: 170
            }
            Button { text: "Reject"; onClicked: studio.rejectCurrentSegment(rejectionReason.text) }
            Button { text: "Regenerate"; onClicked: studio.regenerateRejectedMockSegment() }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        Frame {
            Layout.preferredWidth: 285
            Layout.fillHeight: true
            background: Rectangle { color: "#191f29"; radius: 8 }
            ColumnLayout {
                anchors.fill: parent
                Label { text: "Project & Assets"; font.bold: true }
                Label { text: "Character identity"; color: "#aeb9cb" }
                TextField { id: characterName; Layout.fillWidth: true; placeholderText: "Character name" }
                TextField { id: identityPrompt; Layout.fillWidth: true; placeholderText: "Stable identity prompt" }
                TextField { id: appearanceName; Layout.fillWidth: true; placeholderText: "Appearance name" }
                TextField { id: stylePrompt; Layout.fillWidth: true; placeholderText: "Style / clothing prompt" }
                Button {
                    text: "Create character & sheet"
                    Layout.fillWidth: true
                    onClicked: studio.addCharacter(
                        characterName.text,
                        identityPrompt.text,
                        appearanceName.text,
                        stylePrompt.text
                    )
                }
                Label { text: studio.characterNames.join(" · "); color: "#8dd7c4"; wrapMode: Text.Wrap }
                RowLayout {
                    TextField {
                        id: sheetEntryName
                        Layout.fillWidth: true
                        placeholderText: "Pose/view entry name"
                    }
                    Button { text: "Import"; onClicked: sheetImageDialog.open() }
                }
                RowLayout {
                    TextField {
                        id: sheetPosePrompt
                        Layout.fillWidth: true
                        placeholderText: "Generated pose / view prompt"
                    }
                    Button {
                        text: "Generate"
                        onClicked: studio.generateCharacterSheetEntry(
                            sheetEntryName.text,
                            sheetPosePrompt.text
                        )
                    }
                }
                Label { text: studio.sheetEntryNames.join("\n"); color: "#aeb9cb"; wrapMode: Text.Wrap }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#344052" }
                Label { text: "Exact-time keyframe"; font.bold: true }
                RowLayout {
                    TextField {
                        id: keyframeTime
                        Layout.fillWidth: true
                        placeholderText: "Seconds"
                        text: "0"
                        validator: DoubleValidator { bottom: 0; top: studio.durationSeconds }
                    }
                    Button { text: "Import"; onClicked: keyframeImageDialog.open() }
                }
                Label { text: studio.keyframeLabels.join("\n"); color: "#aeb9cb"; wrapMode: Text.Wrap }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#344052" }
                Label { text: "Runtime"; font.bold: true }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: studio.runtimeVersions
                    color: "#8dd7c4"
                }
                Button {
                    text: "Inspect local Wan backend"
                    Layout.fillWidth: true
                    onClicked: studio.inspectLocalWanBackend()
                }
                RowLayout {
                    Button {
                        text: "Inspect Krea"
                        Layout.fillWidth: true
                        onClicked: studio.inspectLocalKreaBackend()
                    }
                    Button {
                        text: studio.kreaLoaded ? "Krea loaded" : "Load Krea"
                        enabled: !studio.kreaLoaded
                        onClicked: studio.loadLocalKreaBackend()
                    }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: studio.kreaStatus
                    color: "#8dd7c4"
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: studio.backendStatus
                    color: "#f1bf78"
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: studio.backendModels.length > 0
                        ? "Models: " + studio.backendModels.join(" · ")
                        : "No compatible Wan model discovered"
                    color: "#aeb9cb"
                }
                ComboBox {
                    id: wanModel
                    Layout.fillWidth: true
                    model: studio.backendModels
                    enabled: count > 0
                    displayText: count > 0 ? currentText : "Wan model"
                }
                ComboBox {
                    id: wanVae
                    Layout.fillWidth: true
                    model: studio.backendVaeModels
                    enabled: count > 0
                    displayText: count > 0 ? currentText : "Wan VAE"
                }
                ComboBox {
                    id: wanTextEncoder
                    Layout.fillWidth: true
                    model: studio.backendTextEncoderModels
                    enabled: count > 0
                    displayText: count > 0 ? currentText : "Wan text encoder"
                }
                RowLayout {
                    ComboBox {
                        id: wanPrecision
                        Layout.fillWidth: true
                        model: ["bf16", "fp16", "fp32", "fp16_fast"]
                    }
                    ComboBox {
                        id: wanQuantization
                        Layout.fillWidth: true
                        model: ["disabled", "fp8_e4m3fn", "fp8_e4m3fn_scaled", "fp8_e5m2"]
                    }
                }
                RowLayout {
                    ComboBox {
                        id: wanOffload
                        Layout.fillWidth: true
                        model: ["offload_device", "main_device"]
                    }
                    Button {
                        text: "Load"
                        enabled: wanModel.count > 0 && wanVae.count > 0 && wanTextEncoder.count > 0
                        onClicked: studio.loadLocalWanModel(
                            wanModel.currentIndex,
                            wanVae.currentText,
                            wanTextEncoder.currentText,
                            wanPrecision.currentText,
                            wanQuantization.currentText,
                            wanOffload.currentText
                        )
                    }
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
                    anchors.fill: parent
                    anchors.margins: 12
                    Label {
                        text: studio.mannequinPreviewUrl.toString().length > 0
                            ? "Integrated Mannequin Viewport"
                            : "Video / Keyframe Preview"
                        font.pixelSize: 22
                    }
                    Image {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: source.toString().length > 0
                        source: studio.mannequinPreviewUrl
                        fillMode: Image.PreserveAspectFit
                        cache: false
                    }
                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        visible: studio.mannequinPreviewUrl.toString().length === 0
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
                        ListView {
                            anchors.fill: parent
                            anchors.margins: 8
                            clip: true
                            spacing: 4
                            model: studio.timelineBlocks
                            delegate: Rectangle {
                                required property string modelData
                                width: ListView.view.width
                                height: 28
                                radius: 4
                                color: modelData.startsWith("K ") ? "#2e5266" : "#3b315f"
                                Label {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    verticalAlignment: Text.AlignVCenter
                                    text: modelData
                                    color: "#e8edf5"
                                }
                            }
                            Label {
                                anchors.centerIn: parent
                                visible: parent.count === 0
                                text: "Plan the exact-time timeline"
                                color: "#aeb9cb"
                            }
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
                RowLayout {
                    Label { text: "Segment"; color: "#aeb9cb" }
                    SpinBox {
                        id: selectedSegment
                        from: 0
                        to: Math.max(0, studio.segmentCount - 1)
                        enabled: studio.segmentCount > 0
                    }
                    ComboBox {
                        id: segmentMode
                        Layout.fillWidth: true
                        model: ["prompt", "i2v", "first_last", "animate", "replace"]
                    }
                }
                TextField {
                    id: segmentPrompt
                    Layout.fillWidth: true
                    placeholderText: "Segment prompt"
                }
                TextField {
                    id: segmentNegativePrompt
                    Layout.fillWidth: true
                    placeholderText: "Negative prompt"
                }
                Button {
                    text: "Apply segment settings"
                    Layout.fillWidth: true
                    enabled: studio.segmentCount > 0
                    onClicked: studio.updateSegmentInspector(
                        selectedSegment.value,
                        segmentMode.currentText,
                        segmentPrompt.text,
                        segmentNegativePrompt.text
                    )
                }
                Label {
                    text: studio.backendParameterDescriptors.length > 0
                        ? "Backend parameters"
                        : "Inspect backend to discover parameters"
                    color: "#aeb9cb"
                }
                Repeater {
                    model: studio.backendParameterDescriptors
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label {
                            Layout.preferredWidth: 115
                            elide: Text.ElideRight
                            text: modelData.display_name
                            color: "#aeb9cb"
                        }
                        TextField {
                            Layout.fillWidth: true
                            text: String(modelData.default)
                            onEditingFinished: studio.setSegmentBackendParameter(
                                selectedSegment.value,
                                String(modelData.key),
                                text
                            )
                        }
                    }
                }
                Label { text: "Character assignments"; color: "#aeb9cb" }
                Label { text: "Review and provenance"; color: "#aeb9cb" }
                RowLayout {
                    Label { text: "Frame"; color: "#aeb9cb" }
                    SpinBox {
                        id: replacementFrameIndex
                        from: 0
                        to: 10000
                    }
                    CheckBox {
                        id: propagateBoundary
                        text: "Propagate boundary"
                    }
                }
                TextField {
                    id: replacementPrompt
                    Layout.fillWidth: true
                    placeholderText: "Frame modification note / prompt"
                }
                Button {
                    Layout.fillWidth: true
                    text: studio.frameModificationRunning
                        ? "Cancel frame modification"
                        : "Modify frame…"
                    enabled: studio.segmentCount > 0
                    onClicked: studio.frameModificationRunning
                        ? studio.cancelFrameModification()
                        : replacementFrameDialog.open()
                }
                RowLayout {
                    Label { text: "Output FPS"; color: "#aeb9cb" }
                    SpinBox {
                        id: outputFps
                        from: 1
                        to: 120
                        value: Math.round(studio.outputFps)
                        onValueModified: studio.setOutputFps(value)
                    }
                    Button {
                        text: studio.exportRunning ? "Cancel" : "Export"
                        onClicked: studio.exportRunning
                            ? studio.cancelExport()
                            : exportVideoDialog.open()
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#344052" }
                Label { text: "Mannequin pose & camera"; font.bold: true }
                RowLayout {
                    TextField {
                        id: mannequinName
                        Layout.fillWidth: true
                        placeholderText: "Pose scene name"
                        text: "Standing pose"
                    }
                    Button { text: "Create"; onClicked: studio.createMannequinScene(mannequinName.text) }
                }
                Label { text: "Left arm: " + Math.round(leftArm.value) + "°"; color: "#aeb9cb" }
                Slider {
                    id: leftArm
                    Layout.fillWidth: true
                    from: -150; to: 150; value: 0
                    onMoved: studio.setMannequinArmPose(value, rightArm.value)
                }
                Label { text: "Right arm: " + Math.round(rightArm.value) + "°"; color: "#aeb9cb" }
                Slider {
                    id: rightArm
                    Layout.fillWidth: true
                    from: -150; to: 150; value: 0
                    onMoved: studio.setMannequinArmPose(leftArm.value, value)
                }
                Label { text: "Camera: " + Math.round(focalLength.value) + " mm"; color: "#aeb9cb" }
                Slider {
                    id: focalLength
                    Layout.fillWidth: true
                    from: 18; to: 120; value: 50
                    onMoved: studio.setMannequinFocalLength(value)
                }
                RowLayout {
                    TextField {
                        id: poseName
                        Layout.fillWidth: true
                        placeholderText: "Saved pose name"
                    }
                    Button { text: "Save pose"; onClicked: studio.saveCurrentMannequinPose(poseName.text) }
                }
                RowLayout {
                    Button { text: "Render guides"; onClicked: studio.renderCurrentMannequinGuides() }
                    Button { text: "Import Blender"; onClicked: blenderSceneDialog.open() }
                }
                Label {
                    Layout.fillWidth: true
                    text: studio.mannequinConditioningPath
                    color: "#8dd7c4"
                    wrapMode: Text.Wrap
                }
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
            Label { text: studio.generationBackendLabel; color: "#f1bf78" }
        }
    }
}
