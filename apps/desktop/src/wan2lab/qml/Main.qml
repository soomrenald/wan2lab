import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: window
    width: 1440
    height: 900
    minimumWidth: 1040
    minimumHeight: 680
    visible: true
    title: studio.projectName + " — Wan2Lab"
    color: "#11151c"

    function loadSegmentInspector() {
        segmentMode.currentIndex = Math.max(
            0, segmentMode.find(studio.selectedSegmentMode)
        )
        continuationPolicy.currentIndex = Math.max(
            0, continuationPolicy.find(studio.selectedSegmentContinuationPolicy)
        )
        segmentPrompt.text = studio.selectedSegmentPrompt
        segmentNegativePrompt.text = studio.selectedSegmentNegativePrompt
        let action = studio.selectedSegmentAction
        actionMotion.text = action.motion_instruction
        actionStartPose.text = action.starting_pose_ref
        actionEndPose.text = action.ending_pose_ref
        actionCharacterTrajectory.text = action.character_trajectory
        actionCameraTrajectory.text = action.camera_trajectory
        actionContacts.text = action.contact_constraints
        actionSpeed.text = action.speed_easing
        actionPoseAccuracy.value = action.pose_accuracy_preference
        generationFps.text = String(studio.selectedSegmentGenerationFps)
        frameRounding.currentIndex = Math.max(
            0, frameRounding.find(studio.selectedSegmentFrameRounding)
        )
    }

    Connections {
        target: studio
        function onProjectChanged() { window.loadSegmentInspector() }
    }

    Component.onCompleted: loadSegmentInspector()

    FileDialog {
        id: sheetImageDialog
        title: "Import character-sheet image"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)"]
        onAccepted: studio.importSheetEntryForSheet(
            sheetEntrySheet.value,
            selectedFile,
            sheetEntryName.text
        )
    }

    FileDialog {
        id: sheetReplacementDialog
        title: "Replace pose/view image non-destructively"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)"]
        onAccepted: studio.replaceSheetEntry(
            sheetEntrySheet.value,
            sheetReviewEntry.value,
            selectedFile
        )
    }

    FileDialog {
        id: characterAdapterDialog
        title: "Import immutable character adapter"
        nameFilters: ["Model adapters (*.safetensors *.pt *.ckpt)", "All files (*)"]
        onAccepted: studio.importCharacterAdapter(
            sheetEntrySheet.value,
            adapterTarget.currentText,
            selectedFile,
            adapterFamily.currentText,
            adapterKind.currentText,
            adapterModelFamily.text,
            adapterTrigger.text,
            Number(adapterStrength.text)
        )
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
        id: mannequinGuideDialog
        property string guideKind: "shaded"
        title: "Import Blender-rendered mannequin guide"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)"]
        onAccepted: studio.importMannequinGuide(selectedFile, guideKind)
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

    FileDialog {
        id: segmentAssetDialog
        property string assetRole: "start_image"
        title: "Choose immutable segment input"
        nameFilters: ["Media (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv *.webm)"]
        onAccepted: studio.importSegmentAsset(
            selectedSegment.value,
            assetRole,
            selectedFile
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
            ScrollView {
                anchors.fill: parent
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                width: parent.width
                Label { text: "Project & Assets"; font.bold: true }
                Label { text: "Project output / runtime settings"; color: "#aeb9cb" }
                RowLayout {
                    SpinBox {
                        id: projectWidth
                        from: 64
                        to: 8192
                        value: studio.projectWidth
                    }
                    SpinBox {
                        id: projectHeight
                        from: 64
                        to: 8192
                        value: studio.projectHeight
                    }
                    SpinBox {
                        id: segmentBudget
                        from: 250
                        to: 60000
                        stepSize: 250
                        value: studio.defaultSegmentBudgetMs
                    }
                }
                RowLayout {
                    TextField {
                        id: projectKreaBackend
                        Layout.fillWidth: true
                        text: studio.defaultKreaBackendId
                        placeholderText: "Krea backend ID"
                    }
                    TextField {
                        id: projectKreaModel
                        Layout.fillWidth: true
                        text: studio.defaultKreaModelId
                        placeholderText: "Krea model ID"
                    }
                }
                RowLayout {
                    ComboBox {
                        id: projectMemoryPolicy
                        Layout.fillWidth: true
                        model: ["safe_16gb", "balanced", "maximum_residency"]
                        Component.onCompleted: currentIndex = Math.max(
                            0, find(studio.memoryPolicy)
                        )
                    }
                    ComboBox {
                        id: projectContinuation
                        Layout.fillWidth: true
                        model: [
                            "authored_anchor",
                            "generated_last_frame",
                            "corrected_continuation",
                            "dual_boundary",
                            "overlap"
                        ]
                        Component.onCompleted: currentIndex = Math.max(
                            0, find(studio.defaultContinuationPolicy)
                        )
                    }
                }
                RowLayout {
                    TextField {
                        id: projectFfmpeg
                        Layout.fillWidth: true
                        text: studio.ffmpegExecutable
                        placeholderText: "FFmpeg executable"
                    }
                    Button {
                        text: "Apply settings"
                        onClicked: studio.updateProjectSettings(
                            projectWidth.value,
                            projectHeight.value,
                            segmentBudget.value,
                            projectKreaBackend.text,
                            projectKreaModel.text,
                            projectMemoryPolicy.currentText,
                            projectContinuation.currentText,
                            projectFfmpeg.text
                        )
                    }
                }
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
                Label { text: "Detailed identity / appearance"; color: "#aeb9cb" }
                TextField {
                    id: stableDescription
                    Layout.fillWidth: true
                    placeholderText: "Stable face / body description"
                }
                RowLayout {
                    TextField {
                        id: identityTrigger
                        Layout.fillWidth: true
                        placeholderText: "Identity trigger text"
                    }
                    TextField {
                        id: permanentFeatures
                        Layout.fillWidth: true
                        placeholderText: "Permanent features, comma-separated"
                    }
                }
                RowLayout {
                    TextField {
                        id: clothingState
                        Layout.fillWidth: true
                        placeholderText: "Clothing state"
                    }
                    TextField {
                        id: hairstyleState
                        Layout.fillWidth: true
                        placeholderText: "Hairstyle state"
                    }
                }
                TextField {
                    id: makeupAccessories
                    Layout.fillWidth: true
                    placeholderText: "Makeup / accessories"
                }
                RowLayout {
                    TextField {
                        id: visibleFeatures
                        Layout.fillWidth: true
                        placeholderText: "Visible features, comma-separated"
                    }
                    TextField {
                        id: nudityState
                        Layout.fillWidth: true
                        placeholderText: "Nudity state (optional)"
                    }
                }
                Button {
                    text: "Update selected profile"
                    Layout.fillWidth: true
                    onClicked: studio.updateCharacterProfile(
                        sheetEntrySheet.value,
                        identityPrompt.text,
                        stableDescription.text,
                        identityTrigger.text,
                        permanentFeatures.text,
                        stylePrompt.text,
                        clothingState.text,
                        hairstyleState.text,
                        makeupAccessories.text,
                        visibleFeatures.text,
                        nudityState.text
                    )
                }
                Label { text: studio.characterNames.join(" · "); color: "#8dd7c4"; wrapMode: Text.Wrap }
                Label { text: "Identity / appearance adapters"; color: "#aeb9cb" }
                RowLayout {
                    ComboBox { id: adapterTarget; model: ["identity", "appearance"] }
                    ComboBox { id: adapterFamily; model: ["krea", "wan"] }
                    ComboBox { id: adapterKind; model: ["lora", "lokr"] }
                }
                RowLayout {
                    TextField {
                        id: adapterModelFamily
                        Layout.fillWidth: true
                        text: adapterFamily.currentText === "krea" ? "krea2" : "wan2.2"
                        placeholderText: "Compatible model family"
                    }
                    TextField {
                        id: adapterStrength
                        Layout.preferredWidth: 58
                        text: "1.0"
                        validator: DoubleValidator { bottom: -10; top: 10 }
                    }
                }
                RowLayout {
                    TextField {
                        id: adapterTrigger
                        Layout.fillWidth: true
                        placeholderText: "Identity trigger (if required)"
                    }
                    Button { text: "Import adapter"; onClicked: characterAdapterDialog.open() }
                }
                Label {
                    Layout.fillWidth: true
                    text: studio.characterAdapterLabels.join("\n")
                    color: "#8dd7c4"
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    SpinBox {
                        id: sheetEntrySheet
                        from: 0
                        to: Math.max(0, studio.characterNames.length - 1)
                    }
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
                        onClicked: studio.generateCharacterSheetEntryForSheet(
                            sheetEntrySheet.value,
                            sheetEntryName.text,
                            sheetPosePrompt.text
                        )
                    }
                }
                Label { text: studio.sheetEntryNames.join("\n"); color: "#aeb9cb"; wrapMode: Text.Wrap }
                GridView {
                    id: sheetEntryGrid
                    Layout.fillWidth: true
                    Layout.preferredHeight: 300
                    cellWidth: 128
                    cellHeight: 148
                    clip: true
                    model: studio.sheetEntryCards
                    delegate: Frame {
                        required property var modelData
                        width: 120
                        height: 140
                        background: Rectangle {
                            color: "#111822"
                            border.color: "#43516a"
                            radius: 5
                        }
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 4
                            Image {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 88
                                source: modelData.image_url
                                fillMode: Image.PreserveAspectFit
                                cache: false
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.name
                                elide: Text.ElideRight
                                color: "#e8edf5"
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.approval_state + " · " + modelData.metadata
                                elide: Text.ElideRight
                                color: "#8f9bb0"
                                font.pixelSize: 10
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                sheetEntrySheet.value = modelData.sheet_index
                                sheetReviewEntry.value = modelData.entry_index
                                sheetReviewName.text = modelData.name
                                sheetViewLabel.text = modelData.view_label
                                sheetPoseLabel.text = modelData.pose_label
                                sheetFramingLabel.text = modelData.framing_label
                                sheetExpressionLabel.text = modelData.expression_label
                                sheetMannequinLink.currentIndex = modelData.mannequin_scene_index + 1
                            }
                        }
                    }
                }
                RowLayout {
                    SpinBox { id: sheetReviewEntry; from: 0; to: 999; value: 0 }
                    TextField {
                        id: sheetReviewName
                        Layout.fillWidth: true
                        placeholderText: "Rename selected entry"
                    }
                    ComboBox {
                        id: sheetReviewState
                        model: ["draft", "approved", "rejected"]
                    }
                }
                RowLayout {
                    TextField {
                        id: sheetViewLabel
                        Layout.fillWidth: true
                        placeholderText: "View / angle"
                    }
                    TextField {
                        id: sheetPoseLabel
                        Layout.fillWidth: true
                        placeholderText: "Pose"
                    }
                }
                RowLayout {
                    TextField {
                        id: sheetFramingLabel
                        Layout.fillWidth: true
                        placeholderText: "Framing"
                    }
                    TextField {
                        id: sheetExpressionLabel
                        Layout.fillWidth: true
                        placeholderText: "Expression"
                    }
                }
                ComboBox {
                    id: sheetMannequinLink
                    Layout.fillWidth: true
                    model: ["No mannequin link"].concat(studio.mannequinNames)
                }
                RowLayout {
                    Button {
                        text: "Save metadata"
                        onClicked: studio.updateSheetEntryMetadata(
                            sheetEntrySheet.value,
                            sheetReviewEntry.value,
                            sheetReviewName.text,
                            sheetViewLabel.text,
                            sheetPoseLabel.text,
                            sheetFramingLabel.text,
                            sheetExpressionLabel.text,
                            sheetMannequinLink.currentIndex - 1
                        )
                    }
                    Button {
                        text: "Replace image"
                        onClicked: sheetReplacementDialog.open()
                    }
                }
                RowLayout {
                    Button {
                        text: "Save review"
                        onClicked: studio.reviewSheetEntry(
                            sheetEntrySheet.value,
                            sheetReviewEntry.value,
                            sheetReviewName.text,
                            sheetReviewState.currentText
                        )
                    }
                    Button {
                        text: "Remove from sheet"
                        onClicked: studio.removeSheetEntry(
                            sheetEntrySheet.value,
                            sheetReviewEntry.value
                        )
                    }
                }
                RowLayout {
                    TextField {
                        id: duplicateAppearanceName
                        Layout.fillWidth: true
                        placeholderText: "New appearance name"
                    }
                    TextField {
                        id: duplicateAppearancePrompt
                        Layout.fillWidth: true
                        placeholderText: "New style / clothing"
                    }
                }
                Button {
                    text: "Duplicate sheet into appearance"
                    Layout.fillWidth: true
                    onClicked: studio.duplicateSheetAppearance(
                        sheetEntrySheet.value,
                        duplicateAppearanceName.text,
                        duplicateAppearancePrompt.text
                    )
                }
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
                RowLayout {
                    SpinBox { id: regionSheet; from: 0; to: 999; value: 0 }
                    SpinBox { id: regionEntry; from: 0; to: 999; value: 0 }
                    TextField {
                        id: regionPrompt
                        Layout.fillWidth: true
                        placeholderText: "Region prompt"
                    }
                }
                Rectangle {
                    id: regionCanvas
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(
                        120,
                        width * studio.projectHeight / studio.projectWidth
                    )
                    color: "#0b1018"
                    border.color: "#44526a"
                    border.width: 1
                    clip: true
                    property real dragStartX: 0
                    property real dragStartY: 0
                    property real dragCurrentX: 0
                    property real dragCurrentY: 0
                    property bool dragging: false

                    Label {
                        anchors.centerIn: parent
                        text: "Drag a character region"
                        color: "#647086"
                        visible: studio.keyframeRegionRectangles.length === 0
                            && !regionCanvas.dragging
                    }
                    Repeater {
                        model: studio.keyframeRegionRectangles
                        delegate: Rectangle {
                            required property var modelData
                            x: modelData.x0 / studio.projectWidth * regionCanvas.width
                            y: modelData.y0 / studio.projectHeight * regionCanvas.height
                            width: (modelData.x1 - modelData.x0)
                                / studio.projectWidth * regionCanvas.width
                            height: (modelData.y1 - modelData.y0)
                                / studio.projectHeight * regionCanvas.height
                            color: "#286957d9"
                            border.color: "#a89cff"
                            border.width: 2
                            Label {
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.margins: 3
                                text: modelData.name
                                color: "white"
                                elide: Text.ElideRight
                                width: parent.width - 6
                            }
                        }
                    }
                    Rectangle {
                        visible: regionCanvas.dragging
                        x: Math.min(regionCanvas.dragStartX, regionCanvas.dragCurrentX)
                        y: Math.min(regionCanvas.dragStartY, regionCanvas.dragCurrentY)
                        width: Math.abs(regionCanvas.dragCurrentX - regionCanvas.dragStartX)
                        height: Math.abs(regionCanvas.dragCurrentY - regionCanvas.dragStartY)
                        color: "#306957d9"
                        border.color: "#c4baff"
                        border.width: 2
                    }
                    MouseArea {
                        anchors.fill: parent
                        onPressed: function(mouse) {
                            regionCanvas.dragStartX = mouse.x
                            regionCanvas.dragStartY = mouse.y
                            regionCanvas.dragCurrentX = mouse.x
                            regionCanvas.dragCurrentY = mouse.y
                            regionCanvas.dragging = true
                        }
                        onPositionChanged: function(mouse) {
                            if (!regionCanvas.dragging)
                                return
                            regionCanvas.dragCurrentX = Math.max(
                                0, Math.min(regionCanvas.width, mouse.x)
                            )
                            regionCanvas.dragCurrentY = Math.max(
                                0, Math.min(regionCanvas.height, mouse.y)
                            )
                        }
                        onReleased: function(mouse) {
                            regionCanvas.dragCurrentX = Math.max(
                                0, Math.min(regionCanvas.width, mouse.x)
                            )
                            regionCanvas.dragCurrentY = Math.max(
                                0, Math.min(regionCanvas.height, mouse.y)
                            )
                            regionX0.value = Math.round(
                                Math.min(regionCanvas.dragStartX, regionCanvas.dragCurrentX)
                                / regionCanvas.width * studio.projectWidth
                            )
                            regionY0.value = Math.round(
                                Math.min(regionCanvas.dragStartY, regionCanvas.dragCurrentY)
                                / regionCanvas.height * studio.projectHeight
                            )
                            regionX1.value = Math.max(regionX0.value + 1, Math.round(
                                Math.max(regionCanvas.dragStartX, regionCanvas.dragCurrentX)
                                / regionCanvas.width * studio.projectWidth
                            ))
                            regionY1.value = Math.max(regionY0.value + 1, Math.round(
                                Math.max(regionCanvas.dragStartY, regionCanvas.dragCurrentY)
                                / regionCanvas.height * studio.projectHeight
                            ))
                            regionCanvas.dragging = false
                        }
                    }
                }
                TextField {
                    id: regionAdapters
                    Layout.fillWidth: true
                    placeholderText: "Krea adapters: adapter-id=0.8, adapter-id=1.0"
                }
                RowLayout {
                    SpinBox { id: regionX0; from: 0; to: 4096; value: 0 }
                    SpinBox { id: regionY0; from: 0; to: 4096; value: 0 }
                    SpinBox { id: regionX1; from: 1; to: 4096; value: 640 }
                    SpinBox { id: regionY1; from: 1; to: 4096; value: 720 }
                }
                RowLayout {
                    Button {
                        text: "Add region"
                        onClicked: studio.addKeyframeRegionWithAdapters(
                            regionSheet.value,
                            regionEntry.value,
                            regionX0.value,
                            regionY0.value,
                            regionX1.value,
                            regionY1.value,
                            regionPrompt.text,
                            regionAdapters.text
                        )
                    }
                    Button { text: "Clear"; onClicked: studio.clearKeyframeRegions() }
                    Label { text: studio.keyframeRegionLabels.length + " region(s)"; color: "#8dd7c4" }
                }
                TextField {
                    id: keyframeScenePrompt
                    Layout.fillWidth: true
                    placeholderText: "Scene prompt"
                }
                RowLayout {
                    TextField {
                        id: keyframeEnvironmentPrompt
                        Layout.fillWidth: true
                        placeholderText: "Environment"
                    }
                    TextField {
                        id: keyframeLightingPrompt
                        Layout.fillWidth: true
                        placeholderText: "Lighting"
                    }
                }
                ComboBox {
                    id: keyframeSource
                    Layout.fillWidth: true
                    model: studio.keyframeSourceLabels
                }
                RowLayout {
                    Button {
                        text: "Generate regional keyframe"
                        onClicked: studio.generateRegionalKeyframeFromSource(
                            Number(keyframeTime.text),
                            keyframeScenePrompt.text,
                            keyframeEnvironmentPrompt.text,
                            keyframeLightingPrompt.text,
                            keyframeSource.currentIndex
                        )
                    }
                    SpinBox { id: keyframeReviewIndex; from: 0; to: 999; value: 0 }
                    Button {
                        text: "Approve keyframe"
                        onClicked: studio.approveKeyframe(keyframeReviewIndex.value)
                    }
                    Button {
                        text: "Move"
                        onClicked: studio.retimeKeyframe(
                            keyframeReviewIndex.value,
                            Number(keyframeTime.text)
                        )
                    }
                    Button {
                        text: "Fit canvas"
                        onClicked: studio.fitKeyframeToCanvas(keyframeReviewIndex.value)
                    }
                }
                Label { text: "Keyframe face refinement"; color: "#aeb9cb" }
                RowLayout {
                    ComboBox {
                        id: keyframeFaceIdentity
                        Layout.fillWidth: true
                        model: studio.characterNames
                    }
                    ComboBox {
                        id: keyframeFaceReference
                        Layout.fillWidth: true
                        model: studio.sheetEntryNames
                    }
                }
                RowLayout {
                    SpinBox { id: keyframeFaceX0; from: 0; to: 4096; value: 400 }
                    SpinBox { id: keyframeFaceY0; from: 0; to: 4096; value: 120 }
                    SpinBox { id: keyframeFaceX1; from: 1; to: 4096; value: 880 }
                    SpinBox { id: keyframeFaceY1; from: 1; to: 4096; value: 600 }
                }
                TextField {
                    id: keyframeFacePrompt
                    Layout.fillWidth: true
                    placeholderText: "Identity/detail refinement note"
                }
                Button {
                    Layout.fillWidth: true
                    text: "Confirm box & refine keyframe face"
                    enabled: studio.kreaLoaded
                        && keyframeFaceIdentity.currentIndex >= 0
                        && keyframeFaceReference.currentIndex >= 0
                    onClicked: studio.refineKeyframeFace(
                        keyframeReviewIndex.value,
                        keyframeFaceIdentity.currentIndex,
                        keyframeFaceReference.currentIndex,
                        keyframeFaceX0.value,
                        keyframeFaceY0.value,
                        keyframeFaceX1.value,
                        keyframeFaceY1.value,
                        keyframeFacePrompt.text
                    )
                }
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
                    Layout.fillWidth: true
                    Button {
                        text: "Runtime status"
                        Layout.fillWidth: true
                        onClicked: studio.inspectWanRuntimeStatus()
                    }
                    Button {
                        text: "Release all models"
                        Layout.fillWidth: true
                        onClicked: studio.releaseAllModels()
                    }
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
                    onActivated: studio.selectWanModel(index)
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
                        model: studio.wanPrecisionOptions
                        displayText: count > 0 ? currentText : "No compatible precision"
                    }
                    ComboBox {
                        id: wanQuantization
                        Layout.fillWidth: true
                        model: studio.wanQuantizationOptions
                        displayText: count > 0 ? currentText : "No compatible quantization"
                    }
                }
                RowLayout {
                    ComboBox {
                        id: wanOffload
                        Layout.fillWidth: true
                        model: studio.wanOffloadOptions
                        displayText: count > 0 ? currentText : "No compatible offload mode"
                    }
                    Button {
                        text: "Load"
                        enabled: wanModel.count > 0
                            && wanVae.count > 0
                            && wanTextEncoder.count > 0
                            && wanPrecision.count > 0
                            && wanQuantization.count > 0
                            && wanOffload.count > 0
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
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: studio.wanModelCompatibility
                    color: "#8f9bb0"
                }
                Item { Layout.fillHeight: true }
                }
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
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: previewMode.currentText === "Review"
                                ? "Segment Review"
                                : previewMode.currentText === "Keyframe"
                                    ? "Keyframe Preview"
                                    : "Integrated Mannequin Viewport"
                            font.pixelSize: 22
                        }
                        Item { Layout.fillWidth: true }
                        ComboBox {
                            id: previewMode
                            model: ["Review", "Keyframe", "Mannequin"]
                        }
                        SpinBox {
                            id: previewKeyframe
                            visible: previewMode.currentText === "Keyframe"
                            enabled: studio.keyframeLabels.length > 0
                            from: 0
                            to: Math.max(0, studio.keyframeLabels.length - 1)
                            onValueModified: studio.selectPreviewKeyframe(value)
                        }
                    }
                    Image {
                        id: stillPreview
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: previewMode.currentText !== "Review"
                            && source.toString().length > 0
                        source: previewMode.currentText === "Keyframe"
                            ? studio.keyframePreviewUrl
                            : studio.mannequinPreviewUrl
                        fillMode: Image.PreserveAspectFit
                        cache: false
                    }
                    MediaPlayer {
                        id: reviewPlayer
                        source: studio.reviewVideoUrl
                        videoOutput: reviewVideoOutput
                        onSourceChanged: position = 0
                    }
                    VideoOutput {
                        id: reviewVideoOutput
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: previewMode.currentText === "Review"
                            && studio.reviewVideoUrl.toString().length > 0
                        fillMode: VideoOutput.PreserveAspectFit
                    }
                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        visible: (previewMode.currentText === "Review"
                            && studio.reviewVideoUrl.toString().length === 0)
                            || (previewMode.currentText !== "Review"
                                && stillPreview.source.toString().length === 0)
                        text: previewMode.currentText === "Review"
                            ? "Generate a segment to open its immutable review video"
                            : previewMode.currentText === "Keyframe"
                                ? "Import or generate a keyframe to preview it"
                                : "Create a mannequin scene to open its viewport"
                        color: "#7f8ca0"
                    }
                    Label {
                        visible: previewMode.currentText === "Keyframe"
                            && studio.keyframePreviewUrl.toString().length > 0
                        text: studio.keyframePreviewMetadata
                        color: "#8dd7c4"
                    }
                    RowLayout {
                        visible: reviewVideoOutput.visible
                        Layout.fillWidth: true
                        Button {
                            text: reviewPlayer.playbackState === MediaPlayer.PlayingState
                                ? "Pause"
                                : "Play"
                            onClicked: reviewPlayer.playbackState === MediaPlayer.PlayingState
                                ? reviewPlayer.pause()
                                : reviewPlayer.play()
                        }
                        Button {
                            text: "Stop"
                            onClicked: reviewPlayer.stop()
                        }
                        CheckBox {
                            id: reviewLoop
                            text: "Loop"
                            checked: true
                            onCheckedChanged: reviewPlayer.loops = checked
                                ? MediaPlayer.Infinite
                                : 1
                        }
                        Slider {
                            id: reviewSeek
                            Layout.fillWidth: true
                            from: 0
                            to: Math.max(1, reviewPlayer.duration)
                            value: reviewPlayer.position
                            onMoved: reviewPlayer.position = value
                        }
                        Label {
                            text: (reviewPlayer.position / 1000).toFixed(2)
                                + " / "
                                + (reviewPlayer.duration / 1000).toFixed(2)
                                + " s"
                            color: "#aeb9cb"
                        }
                    }
                    ListView {
                        id: reviewFrameStrip
                        visible: reviewVideoOutput.visible
                        Layout.fillWidth: true
                        Layout.preferredHeight: 42
                        orientation: ListView.Horizontal
                        spacing: 4
                        clip: true
                        model: studio.reviewFrameLabels
                        delegate: Button {
                            required property string modelData
                            required property int index
                            width: 44
                            height: 34
                            text: modelData
                            highlighted: Math.floor(
                                reviewPlayer.position
                                * studio.reviewGenerationFps
                                / 1000
                            ) === index
                            onClicked: {
                                reviewPlayer.position = Math.round(
                                    index * 1000 / studio.reviewGenerationFps
                                )
                                replacementFrameIndex.value = index
                            }
                        }
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
            ScrollView {
                anchors.fill: parent
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                width: parent.width
                Label { text: "Context Inspector"; font.bold: true }
                RowLayout {
                    Label { text: "Segment"; color: "#aeb9cb" }
                    SpinBox {
                        id: selectedSegment
                        from: 0
                        to: Math.max(0, studio.segmentCount - 1)
                        enabled: studio.segmentCount > 0
                        onValueModified: {
                            studio.selectReviewSegment(value)
                            window.loadSegmentInspector()
                        }
                    }
                    ComboBox {
                        id: segmentMode
                        Layout.fillWidth: true
                        model: ["prompt", "i2v", "first_last", "animate", "replace"]
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "Revision"; color: "#aeb9cb" }
                    ComboBox {
                        Layout.fillWidth: true
                        model: studio.reviewRevisionLabels
                        currentIndex: studio.reviewRevisionIndex
                        enabled: count > 0
                        onActivated: studio.selectReviewRevision(index)
                    }
                }
                ComboBox {
                    id: continuationPolicy
                    Layout.fillWidth: true
                    model: [
                        "authored_anchor",
                        "generated_last_frame",
                        "corrected_continuation",
                        "dual_boundary",
                        "overlap"
                    ]
                    onActivated: studio.setSegmentContinuationPolicy(
                        selectedSegment.value,
                        currentText
                    )
                }
                Label { text: "Mode inputs"; color: "#aeb9cb" }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    Button {
                        text: "Start image…"
                        onClicked: {
                            segmentAssetDialog.assetRole = "start_image"
                            segmentAssetDialog.open()
                        }
                    }
                    Button {
                        text: "End image…"
                        onClicked: {
                            segmentAssetDialog.assetRole = "end_image"
                            segmentAssetDialog.open()
                        }
                    }
                    Button {
                        text: "Character ref…"
                        onClicked: {
                            segmentAssetDialog.assetRole = "reference_character"
                            segmentAssetDialog.open()
                        }
                    }
                    Button {
                        text: "Driving video…"
                        onClicked: {
                            segmentAssetDialog.assetRole = "driving_video"
                            segmentAssetDialog.open()
                        }
                    }
                    Button {
                        text: "Source video…"
                        onClicked: {
                            segmentAssetDialog.assetRole = "source_video"
                            segmentAssetDialog.open()
                        }
                    }
                    Button {
                        text: "Mask image…"
                        onClicked: {
                            segmentAssetDialog.assetRole = "mask"
                            segmentAssetDialog.open()
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: studio.segmentInputSummary
                    color: "#8f9bb0"
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
                Label { text: "Structured action"; color: "#aeb9cb" }
                TextField {
                    id: actionMotion
                    Layout.fillWidth: true
                    placeholderText: "Motion instruction"
                }
                RowLayout {
                    TextField {
                        id: actionStartPose
                        Layout.fillWidth: true
                        placeholderText: "Starting pose ref ID"
                    }
                    TextField {
                        id: actionEndPose
                        Layout.fillWidth: true
                        placeholderText: "Ending pose ref ID"
                    }
                }
                TextField {
                    id: actionCharacterTrajectory
                    Layout.fillWidth: true
                    placeholderText: "Character path / trajectory"
                }
                TextField {
                    id: actionCameraTrajectory
                    Layout.fillWidth: true
                    placeholderText: "Camera trajectory"
                }
                TextField {
                    id: actionContacts
                    Layout.fillWidth: true
                    placeholderText: "Contact constraints, comma-separated"
                }
                TextField {
                    id: actionSpeed
                    Layout.fillWidth: true
                    placeholderText: "Motion speed / easing"
                }
                Label {
                    text: "Pose accuracy preference: " + actionPoseAccuracy.value.toFixed(2)
                    color: "#aeb9cb"
                }
                Slider {
                    id: actionPoseAccuracy
                    Layout.fillWidth: true
                    from: 0
                    to: 1
                    value: 0.5
                }
                Button {
                    Layout.fillWidth: true
                    text: "Save action controls"
                    enabled: studio.segmentCount > 0
                    onClicked: studio.setSegmentAction(
                        selectedSegment.value,
                        actionMotion.text,
                        actionStartPose.text,
                        actionEndPose.text,
                        actionCharacterTrajectory.text,
                        actionCameraTrajectory.text,
                        actionContacts.text,
                        actionSpeed.text,
                        actionPoseAccuracy.value
                    )
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Textual motion controls bind into Wan conditioning. Pose reference IDs remain visible in metadata unless the selected wrapper exposes a typed pose input."
                    color: "#8f9bb0"
                }
                Label {
                    text: studio.backendParameterDescriptors.length > 0
                        ? "Backend parameters"
                        : "Inspect backend to discover parameters"
                    color: "#aeb9cb"
                }
                Label { text: "Advanced generation timing"; color: "#aeb9cb" }
                RowLayout {
                    TextField {
                        id: generationFps
                        Layout.fillWidth: true
                        placeholderText: "Generation FPS"
                        validator: DoubleValidator { bottom: 0.01; top: 240 }
                    }
                    ComboBox {
                        id: frameRounding
                        model: ["nearest", "floor", "ceil"]
                    }
                    Button {
                        text: "Apply"
                        enabled: studio.segmentCount > 0
                        onClicked: studio.setSegmentTemporalSettings(
                            selectedSegment.value,
                            Number(generationFps.text),
                            frameRounding.currentText
                        )
                    }
                }
                Label {
                    text: studio.selectedSegmentFrameCount > 0
                        ? studio.selectedSegmentFrameCount + " valid generated frames"
                        : "Frame count is derived from backend rules"
                    color: "#8f9bb0"
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
                            text: String(modelData.value)
                            onEditingFinished: studio.setSegmentBackendParameter(
                                selectedSegment.value,
                                String(modelData.key),
                                text
                            )
                        }
                    }
                }
                Label { text: "Character assignments"; color: "#aeb9cb" }
                Repeater {
                    model: studio.selectedSegmentCharacterAssignments
                    delegate: CheckBox {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.name
                        checked: modelData.assigned
                        onClicked: studio.setSegmentCharacterAssignment(
                            selectedSegment.value,
                            modelData.identity_index,
                            checked
                        )
                    }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    visible: studio.selectedSegmentCharacterAssignments.length === 0
                    text: "Create a character identity to assign it to this segment."
                    color: "#8f9bb0"
                }
                Label { text: "Review and provenance"; color: "#aeb9cb" }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: studio.reviewMetadata
                    color: "#8f9bb0"
                }
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
                RowLayout {
                    SpinBox { id: faceX0; from: 0; to: 4096; value: 400 }
                    SpinBox { id: faceY0; from: 0; to: 4096; value: 120 }
                    SpinBox { id: faceX1; from: 1; to: 4096; value: 880 }
                    SpinBox { id: faceY1; from: 1; to: 4096; value: 600 }
                }
                RowLayout {
                    Button {
                        text: "Krea edit frame"
                        enabled: !studio.frameModificationRunning && studio.kreaLoaded
                        onClicked: studio.generateFrameEditWithKrea(
                            selectedSegment.value,
                            replacementFrameIndex.value,
                            replacementPrompt.text,
                            faceX0.value,
                            faceY0.value,
                            faceX1.value,
                            faceY1.value,
                            false,
                            propagateBoundary.checked
                        )
                    }
                    Button {
                        text: "Confirm region & refine face"
                        enabled: !studio.frameModificationRunning && studio.kreaLoaded
                        onClicked: studio.generateFrameEditWithKrea(
                            selectedSegment.value,
                            replacementFrameIndex.value,
                            replacementPrompt.text,
                            faceX0.value,
                            faceY0.value,
                            faceX1.value,
                            faceY1.value,
                            true,
                            propagateBoundary.checked
                        )
                    }
                }
                RowLayout {
                    TextField {
                        id: batchFrameIndices
                        Layout.fillWidth: true
                        placeholderText: "Batch frames, e.g. 4,8,12"
                    }
                    Button {
                        text: "Krea batch repair"
                        enabled: !studio.frameModificationRunning && studio.kreaLoaded
                        onClicked: studio.generateBatchFrameEditsWithKrea(
                            selectedSegment.value,
                            batchFrameIndices.text,
                            replacementPrompt.text,
                            propagateBoundary.checked
                        )
                    }
                }
                Label {
                    text: "Batch identity refinement"
                    color: "#aeb9cb"
                }
                RowLayout {
                    ComboBox {
                        id: batchIdentity
                        Layout.fillWidth: true
                        model: studio.characterNames
                        displayText: currentIndex >= 0
                            ? "Identity: " + currentText
                            : "Select identity"
                    }
                    ComboBox {
                        id: batchReference
                        Layout.fillWidth: true
                        model: studio.sheetEntryNames
                        displayText: currentIndex >= 0
                            ? "Reference: " + currentText
                            : "Select sheet reference"
                    }
                }
                Button {
                    Layout.fillWidth: true
                    text: "Detect faces in batch frames"
                    enabled: !studio.frameModificationRunning
                        && studio.kreaLoaded
                        && batchIdentity.currentIndex >= 0
                    onClicked: studio.detectBatchFaces(
                        selectedSegment.value,
                        batchFrameIndices.text,
                        batchIdentity.currentIndex
                    )
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: "#d6b76b"
                    text: "Choose the correct candidate for each frame; Wan2Lab never assumes the largest face is the target."
                }
                ComboBox {
                    id: faceProposalChoice
                    Layout.fillWidth: true
                    model: studio.faceProposalSummaries
                }
                RowLayout {
                    Button {
                        text: "Confirm candidate"
                        enabled: faceProposalChoice.currentIndex >= 0
                        onClicked: studio.confirmDetectedBatchFace(
                            faceProposalChoice.currentIndex
                        )
                    }
                    Button {
                        text: "Confirm manual box for Frame field"
                        enabled: studio.confirmedFaceFrames.length > 0
                        onClicked: studio.confirmManualBatchFace(
                            replacementFrameIndex.value,
                            faceX0.value,
                            faceY0.value,
                            faceX1.value,
                            faceY1.value
                        )
                    }
                }
                Repeater {
                    model: studio.confirmedFaceFrames
                    Label {
                        text: modelData
                        color: modelData.indexOf("required") >= 0 ? "#d6b76b" : "#91d6a8"
                    }
                }
                Label {
                    text: "Identity drift review"
                    color: "#aeb9cb"
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: "#8f9bb0"
                    text: "Detection and association warnings are available now; identity-similarity scores appear only when the active backend exposes that capability."
                }
                Repeater {
                    model: studio.identityWarningSummaries
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: modelData
                        color: "#d6b76b"
                    }
                }
                ComboBox {
                    id: checkpointProposal
                    Layout.fillWidth: true
                    model: studio.checkpointProposalSummaries
                }
                RowLayout {
                    Button {
                        text: "Approve checkpoint"
                        enabled: checkpointProposal.currentIndex >= 0
                        onClicked: studio.approveIdentityCheckpoint(
                            checkpointProposal.currentIndex
                        )
                    }
                    Button {
                        text: "Apply approved checkpoint"
                        enabled: checkpointProposal.currentIndex >= 0
                            && !studio.frameModificationRunning
                        onClicked: studio.applyIdentityCheckpoint(
                            checkpointProposal.currentIndex
                        )
                    }
                }
                Button {
                    Layout.fillWidth: true
                    text: "Refine confirmed identity batch"
                    enabled: studio.faceBatchReady
                        && !studio.frameModificationRunning
                        && batchReference.currentIndex >= 0
                    onClicked: studio.refineConfirmedFaceBatch(
                        replacementPrompt.text,
                        batchReference.currentIndex,
                        propagateBoundary.checked
                    )
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
                RowLayout {
                    ComboBox {
                        id: mannequinInstance
                        Layout.fillWidth: true
                        model: studio.mannequinInstanceNames
                        displayText: count > 0 ? currentText : "No mannequin"
                        onActivated: studio.selectMannequinInstance(index)
                    }
                    TextField {
                        id: newMannequinName
                        Layout.fillWidth: true
                        placeholderText: "Additional figure"
                    }
                    Button {
                        text: "Add"
                        enabled: mannequinInstance.count > 0
                        onClicked: studio.addMannequinInstance(newMannequinName.text)
                    }
                }
                Label { text: "Selected joint rotation X / Y / Z"; color: "#aeb9cb" }
                ComboBox {
                    id: mannequinJoint
                    Layout.fillWidth: true
                    model: studio.mannequinJointNames
                    displayText: count > 0 ? currentText : "No joints"
                }
                RowLayout {
                    TextField {
                        id: jointRotationX
                        Layout.fillWidth: true
                        text: "0"
                        placeholderText: "X°"
                        validator: DoubleValidator { bottom: -180; top: 180 }
                    }
                    TextField {
                        id: jointRotationY
                        Layout.fillWidth: true
                        text: "0"
                        placeholderText: "Y°"
                        validator: DoubleValidator { bottom: -180; top: 180 }
                    }
                    TextField {
                        id: jointRotationZ
                        Layout.fillWidth: true
                        text: "0"
                        placeholderText: "Z°"
                        validator: DoubleValidator { bottom: -180; top: 180 }
                    }
                    Button {
                        text: "Pose"
                        enabled: mannequinJoint.currentIndex >= 0
                        onClicked: studio.setMannequinJointRotation(
                            mannequinJoint.currentIndex,
                            Number(jointRotationX.text),
                            Number(jointRotationY.text),
                            Number(jointRotationZ.text)
                        )
                    }
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
                Label { text: "Camera position / angle / framing"; color: "#aeb9cb" }
                RowLayout {
                    TextField { id: cameraX; Layout.fillWidth: true; text: "0"; placeholderText: "X" }
                    TextField { id: cameraY; Layout.fillWidth: true; text: "0.9"; placeholderText: "Y" }
                    TextField { id: cameraZ; Layout.fillWidth: true; text: "6"; placeholderText: "Z" }
                }
                RowLayout {
                    TextField { id: cameraYaw; Layout.fillWidth: true; text: "0"; placeholderText: "Yaw°" }
                    TextField { id: cameraPitch; Layout.fillWidth: true; text: "0"; placeholderText: "Pitch°" }
                    TextField { id: cameraFraming; Layout.fillWidth: true; text: "1"; placeholderText: "Frame 0.2–1" }
                    Button {
                        text: "Apply"
                        onClicked: studio.setMannequinCamera(
                            Number(cameraX.text),
                            Number(cameraY.text),
                            Number(cameraZ.text),
                            Number(cameraYaw.text),
                            Number(cameraPitch.text),
                            Number(cameraFraming.text)
                        )
                    }
                }
                Label { text: "Body height / width / limb scale"; color: "#aeb9cb" }
                RowLayout {
                    TextField { id: bodyHeight; Layout.fillWidth: true; text: "1" }
                    TextField { id: bodyWidth; Layout.fillWidth: true; text: "1" }
                    TextField { id: limbScale; Layout.fillWidth: true; text: "1" }
                    Button {
                        text: "Apply"
                        onClicked: studio.setMannequinProportions(
                            Number(bodyHeight.text),
                            Number(bodyWidth.text),
                            Number(limbScale.text)
                        )
                    }
                }
                Label { text: "Key light intensity / position"; color: "#aeb9cb" }
                RowLayout {
                    TextField { id: lightIntensity; Layout.fillWidth: true; text: "1" }
                    TextField { id: lightX; Layout.fillWidth: true; text: "2" }
                    TextField { id: lightY; Layout.fillWidth: true; text: "4" }
                    TextField { id: lightZ; Layout.fillWidth: true; text: "4" }
                    Button {
                        text: "Apply"
                        onClicked: studio.setMannequinLight(
                            Number(lightIntensity.text),
                            Number(lightX.text),
                            Number(lightY.text),
                            Number(lightZ.text)
                        )
                    }
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
                    ComboBox {
                        id: savedMannequinPose
                        Layout.fillWidth: true
                        model: studio.mannequinPoseNames
                    }
                    Button {
                        text: "Apply pose"
                        enabled: savedMannequinPose.currentIndex >= 0
                        onClicked: studio.applySavedMannequinPose(savedMannequinPose.currentIndex)
                    }
                }
                RowLayout {
                    SpinBox {
                        id: mannequinRegion
                        from: 0
                        to: Math.max(0, studio.keyframeRegionRectangles.length - 1)
                        enabled: studio.keyframeRegionRectangles.length > 0
                    }
                    Button {
                        text: "Associate region"
                        enabled: mannequinRegion.enabled
                        onClicked: studio.associateMannequinRegion(mannequinRegion.value)
                    }
                }
                Label { text: "Scene prop / contact constraint"; color: "#aeb9cb" }
                RowLayout {
                    TextField { id: propName; Layout.fillWidth: true; placeholderText: "Prop name" }
                    TextField { id: propX; Layout.fillWidth: true; text: "0"; placeholderText: "X" }
                    TextField { id: propY; Layout.fillWidth: true; text: "0"; placeholderText: "Y" }
                    TextField { id: propZ; Layout.fillWidth: true; text: "0"; placeholderText: "Z" }
                    Button {
                        text: "Add"
                        onClicked: studio.addMannequinProp(
                            propName.text,
                            Number(propX.text),
                            Number(propY.text),
                            Number(propZ.text)
                        )
                    }
                }
                RowLayout {
                    TextField { id: contactJoint; Layout.fillWidth: true; placeholderText: "Joint, e.g. wrist_l" }
                    TextField { id: contactX; Layout.fillWidth: true; text: "0"; placeholderText: "X" }
                    TextField { id: contactY; Layout.fillWidth: true; text: "0"; placeholderText: "Y" }
                    TextField { id: contactZ; Layout.fillWidth: true; text: "0"; placeholderText: "Z" }
                    Button {
                        text: "Add"
                        onClicked: studio.addMannequinContact(
                            contactJoint.text,
                            Number(contactX.text),
                            Number(contactY.text),
                            Number(contactZ.text)
                        )
                    }
                }
                RowLayout {
                    Button { text: "Render guides"; onClicked: studio.renderCurrentMannequinGuides() }
                    Button { text: "Import Blender"; onClicked: blenderSceneDialog.open() }
                }
                RowLayout {
                    Button {
                        text: "Import shaded"
                        onClicked: {
                            mannequinGuideDialog.guideKind = "shaded"
                            mannequinGuideDialog.open()
                        }
                    }
                    Button {
                        text: "Import depth"
                        onClicked: {
                            mannequinGuideDialog.guideKind = "depth"
                            mannequinGuideDialog.open()
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: studio.mannequinConditioningPath
                    color: "#8dd7c4"
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: studio.mannequinSceneSummary
                    color: "#8dd7c4"
                    wrapMode: Text.Wrap
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#344052" }
                Label { text: "Activity"; font.bold: true }
                ListView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 180
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
