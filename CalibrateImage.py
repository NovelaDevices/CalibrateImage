import os
import time

import FreeCAD
import FreeCADGui

# FreeCAD provides a `PySide` shim that redirects to whichever real
# binding is installed (PySide2 for Qt5 builds, PySide6 for Qt6
# builds). Using the shim keeps the workbench portable across both.
from PySide import QtCore, QtWidgets

hidden_objects = []
calibration_points = []
calibration_filter = None
calibration_crosshair = None
calibration_motion = None
calibration_keyboard = None
calibration_line_switch = None
calibration_line_coords = None
calibration_label_translation = None
calibration_label_text = None
calibration_img = None

ICON_PATH = os.path.join(
    FreeCAD.getUserAppDataDir(),
    "Mod",
    "ImageCalibration",
    "Resources",
    "icons",
    "ruler.svg"
)


def q_to_float(value):
    """Unwrap a FreeCAD Base.Quantity (or plain number) to a Python float."""
    return float(value.Value) if hasattr(value, "Value") else float(value)


def project_onto_image_plane(world_point, img):
    """Project a world-space point onto the image's own plane.

    The image plane passes through img.Placement.Base with normal
    img.Placement.Rotation * (0,0,1). Return the closest point on
    that plane."""

    origin = img.Placement.Base
    normal = img.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    normal.normalize()

    delta = world_point - origin
    distance = delta.dot(normal)

    return world_point - normal * distance


def orient_camera_to_image(img):

    global hidden_objects

    doc = FreeCAD.ActiveDocument

    hidden_objects = []

    for obj in doc.Objects:

        if obj != img:

            if obj.ViewObject.Visibility:
                hidden_objects.append(obj)

            obj.ViewObject.Visibility = False

    img.ViewObject.Visibility = True

    view = FreeCADGui.activeDocument().activeView()

    # Pick a standard view whose normal matches the image plane's
    # own normal. ImagePlane's local +Z is its face normal, so we
    # rotate (0,0,1) by the object's placement and match against
    # the six principal axes.
    normal = img.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

    axes = [
        (FreeCAD.Vector(0, 0, 1),  view.viewTop),
        (FreeCAD.Vector(0, 0, -1), view.viewBottom),
        (FreeCAD.Vector(0, 1, 0),  view.viewRear),
        (FreeCAD.Vector(0, -1, 0), view.viewFront),
        (FreeCAD.Vector(1, 0, 0),  view.viewRight),
        (FreeCAD.Vector(-1, 0, 0), view.viewLeft),
    ]

    best_dot = -2.0
    best_view_fn = view.viewTop

    for axis, view_fn in axes:
        dot = normal.dot(axis)
        if dot > best_dot:
            best_dot = dot
            best_view_fn = view_fn

    best_view_fn()
    view.fitAll()


def restore_visibility():

    global hidden_objects

    for obj in hidden_objects:
        obj.ViewObject.Visibility = True

    hidden_objects = []


def ensure_rubberband_line(view):
    """Build the rubberband line's Coin nodes once and leave them
    permanently attached to the scene graph, toggling visibility via
    an SoSwitch."""

    global calibration_line_switch
    global calibration_line_coords
    global calibration_label_translation
    global calibration_label_text

    from pivy import coin

    root = view.getSceneGraph()

    if calibration_line_switch is not None:
        try:
            already_attached = root.findChild(calibration_line_switch) >= 0
        except Exception:
            already_attached = False

        if already_attached:
            return

    switch = coin.SoSwitch()
    switch.whichChild = -1

    # SoAnnotation renders its children after everything else and
    # skips the depth buffer — so the line always draws on top of
    # the image regardless of how the image is placed in space.
    # Previously we used an SoSeparator + SoTranslation(0, 0, 0.5)
    # to hop above the image, but that assumed the image sat on the
    # global XY plane. Annotation nodes have no orientation
    # dependency.
    line_sep = coin.SoAnnotation()

    material = coin.SoMaterial()
    material.diffuseColor = (1.0, 1.0, 0.0)

    draw_style = coin.SoDrawStyle()
    draw_style.lineWidth = 2

    coords = coin.SoCoordinate3()
    coords.point.setValues(
        0,
        2,
        [
            (0, 0, 0),
            (0, 0, 0)
        ]
    )

    line_set = coin.SoLineSet()
    line_set.numVertices.setValue(2)

    # Distance readout — SoText2 renders as screen-aligned 2D text at
    # a 3D anchor. Own SoSeparator so the SoTranslation only affects
    # the text, not the line coordinates that follow.
    label_group = coin.SoSeparator()

    label_material = coin.SoMaterial()
    label_material.diffuseColor = (1.0, 1.0, 0.0)

    label_translation = coin.SoTranslation()
    label_translation.translation = (0, 0, 0)

    label_font = coin.SoFont()
    label_font.size = 14
    label_font.name = "Arial"

    label_text = coin.SoText2()
    label_text.string = ""

    label_group.addChild(label_material)
    label_group.addChild(label_translation)
    label_group.addChild(label_font)
    label_group.addChild(label_text)

    line_sep.addChild(material)
    line_sep.addChild(draw_style)
    line_sep.addChild(coords)
    line_sep.addChild(line_set)
    line_sep.addChild(label_group)

    switch.addChild(line_sep)

    root.addChild(switch)

    calibration_line_switch = switch
    calibration_line_coords = coords
    calibration_label_translation = label_translation
    calibration_label_text = label_text


def show_rubberband_line(point):

    global calibration_line_switch

    view = FreeCADGui.activeDocument().activeView()

    ensure_rubberband_line(view)

    calibration_line_coords.point.set1Value(0, point.x, point.y, point.z)
    calibration_line_coords.point.set1Value(1, point.x, point.y, point.z)

    calibration_line_switch.whichChild = 0


def hide_rubberband_line():

    global calibration_line_switch

    if calibration_line_switch is not None:
        calibration_line_switch.whichChild = -1

    if calibration_label_text is not None:
        calibration_label_text.string = ""


def cleanup_calibration_nodes(img):

    global calibration_filter
    global calibration_motion
    global calibration_keyboard
    global calibration_crosshair

    view = FreeCADGui.activeDocument().activeView()

    if calibration_filter:
        view.getSceneGraph().removeChild(
            calibration_filter.callback
        )
        calibration_filter = None

    if calibration_motion:
        view.getSceneGraph().removeChild(
            calibration_motion.callback
        )
        calibration_motion = None

    if calibration_keyboard:
        view.getSceneGraph().removeChild(
            calibration_keyboard.callback
        )
        calibration_keyboard = None

    if img is not None:
        img.ViewObject.Selectable = True

    if calibration_crosshair:
        view.getSceneGraph().removeChild(
            calibration_crosshair
        )
        calibration_crosshair = None

    hide_rubberband_line()


def start_calibration(img, on_two_points_placed):

    global calibration_points
    global calibration_img
    global calibration_filter

    # Force-clean any stale state from a previous run whose panel
    # wasn't fully torn down (e.g. crash mid-calibration).
    if calibration_filter is not None:
        FreeCAD.Console.PrintMessage(
            "Cleaning up previous calibration state.\n"
        )
        cleanup_calibration_nodes(calibration_img)
        clear_calibration_state()

    calibration_img = img
    calibration_points = []

    from pivy import coin

    view = FreeCADGui.activeDocument().activeView()

    global calibration_crosshair

    calibration_crosshair = create_crosshair()

    view.getSceneGraph().addChild(
        calibration_crosshair
    )

    img.ViewObject.Selectable = False

    class CalibrationCallback:

        def __init__(self):
            self.callback = coin.SoEventCallback()

            self.callback.addEventCallback(
                coin.SoMouseButtonEvent.getClassTypeId(),
                self.mouse_event
            )

        def mouse_event(self, node, event_callback):

            event = event_callback.getEvent()

            if event.getState() != coin.SoButtonEvent.DOWN:
                return

            if event.getButton() != coin.SoMouseButtonEvent.BUTTON1:
                return

            pos = event.getPosition()

            x = pos[0]
            y = pos[1]

            FreeCAD.Console.PrintMessage(
                f"Click: {x}, {y}\n"
            )

            point = view.getPoint(
                x,
                y
            )

            # Project the point onto the image plane. view.getPoint()
            # returns whatever surface the ray happened to hit
            # (crosshair, image, empty background), so distances vary
            # depending on what was under the cursor. Projecting onto
            # the image's own plane keeps measurements stable and
            # works regardless of how the image is oriented in space.
            point = project_onto_image_plane(point, img)

            calibration_points.append(point)

            FreeCAD.Console.PrintMessage(
                f"Point {len(calibration_points)}: {point}\n"
            )

            event_callback.setHandled()

            if len(calibration_points) == 1:

                QtCore.QTimer.singleShot(
                    0,
                    lambda pt=point: show_rubberband_line(pt)
                )

            elif len(calibration_points) == 2:

                p1 = calibration_points[0]
                p2 = calibration_points[1]

                distance = p1.distanceToPoint(p2)

                FreeCAD.Console.PrintMessage(
                    f"Measured distance: {distance:.3f} mm\n"
                )

                QtCore.QTimer.singleShot(
                    0,
                    lambda d=distance: on_two_points_placed(d)
                )

    class MotionCallback:

        def __init__(self):
            self.callback = coin.SoEventCallback()

            self.callback.addEventCallback(
                coin.SoLocation2Event.getClassTypeId(),
                self.mouse_move
            )

        def mouse_move(self, node, event_callback):

            global calibration_crosshair

            event = event_callback.getEvent()

            pos = event.getPosition()

            x = pos[0]
            y = pos[1]

            point = view.getPoint(
                x,
                y
            )

            projected = project_onto_image_plane(point, img)

            if calibration_crosshair:

                calibration_crosshair.translation.translation.setValue(
                    projected.x,
                    projected.y,
                    projected.z
                )

            if calibration_line_coords and len(calibration_points) == 1:

                p1 = calibration_points[0]

                calibration_line_coords.point.set1Value(
                    1,
                    projected.x,
                    projected.y,
                    projected.z
                )

                distance = p1.distanceToPoint(projected)

                if calibration_label_text is not None:
                    calibration_label_text.string = f"{distance:.3f} mm"

                if calibration_label_translation is not None:
                    # Anchor label at midpoint of the line.
                    mid = (p1 + projected) * 0.5
                    calibration_label_translation.translation = (
                        mid.x, mid.y, mid.z
                    )

    class KeyboardCallback:

        def __init__(self):
            self.callback = coin.SoEventCallback()

            self.callback.addEventCallback(
                coin.SoKeyboardEvent.getClassTypeId(),
                self.key_event
            )

        def key_event(self, node, event_callback):

            event = event_callback.getEvent()

            if event.getState() != coin.SoButtonEvent.DOWN:
                return

            if event.getKey() != coin.SoKeyboardEvent.ESCAPE:
                return

            event_callback.setHandled()

            QtCore.QTimer.singleShot(
                0,
                lambda: FreeCADGui.Control.closeDialog()
            )

    calibration_filter = CalibrationCallback()

    view.getSceneGraph().addChild(
        calibration_filter.callback
    )

    global calibration_motion

    calibration_motion = MotionCallback()

    view.getSceneGraph().addChild(
        calibration_motion.callback
    )

    global calibration_keyboard

    calibration_keyboard = KeyboardCallback()

    view.getSceneGraph().addChild(
        calibration_keyboard.callback
    )

    FreeCAD.Console.PrintMessage(
        "Calibration mode started. Click two points, or press Escape to cancel.\n"
    )


def clear_calibration_state():
    """Reset the click-state globals so mouse_event treats the next
    click as the first point. Used by the panel's Reset button and
    during cleanup."""

    global calibration_points
    global calibration_img

    calibration_points = []
    calibration_img = None


class CalibrationTaskPanel:
    """Task panel for image calibration. Docked in FreeCAD's Combo
    View, non-modal so the user can still zoom/pan the 3D view and
    click reference points while the panel is open.

    Flow: panel opens → panel starts calibration mode (Coin event
    callbacks that update the panel via on_two_points_placed) →
    user clicks two points → user types real-world distance →
    user clicks OK → panel applies the scale and closes."""

    def __init__(self, img):

        self.img = img
        self.measured_distance = 0.0
        self.real_distance_mm = 0.0

        # FreeCAD task panels look for a `form` attribute (QWidget
        # or list of them) to embed in the Combo View. Ours is a
        # single QWidget built programmatically.
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Calibrate Image")

        layout = QtWidgets.QVBoxLayout(self.form)

        header = QtWidgets.QLabel(f"<b>{img.Label}</b>")
        layout.addWidget(header)

        self.instruction = QtWidgets.QLabel(
            "Click two reference points on the image."
        )
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)

        grid = QtWidgets.QGridLayout()

        grid.addWidget(QtWidgets.QLabel("Measured:"), 0, 0)
        self.measured_label = QtWidgets.QLabel("—")
        grid.addWidget(self.measured_label, 0, 1)

        grid.addWidget(QtWidgets.QLabel("Real distance:"), 1, 0)
        self.real_input = QtWidgets.QLineEdit()
        self.real_input.setPlaceholderText("e.g. 10 mm, 5 cm, 2 in")
        self.real_input.setEnabled(False)
        grid.addWidget(self.real_input, 1, 1)

        grid.addWidget(QtWidgets.QLabel("Scale factor:"), 2, 0)
        self.scale_label = QtWidgets.QLabel("—")
        grid.addWidget(self.scale_label, 2, 1)

        grid.addWidget(QtWidgets.QLabel("New size:"), 3, 0)
        self.size_label = QtWidgets.QLabel("—")
        self.size_label.setTextFormat(QtCore.Qt.RichText)
        grid.addWidget(self.size_label, 3, 1)

        layout.addLayout(grid)

        self.reset_btn = QtWidgets.QPushButton("Reset points")
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self.reset_points)
        layout.addWidget(self.reset_btn)

        layout.addStretch()

        self.real_input.textChanged.connect(self.update_preview)

        # Wire up the Coin event callbacks. When the user places both
        # points, our on_two_points_placed method fires (deferred via
        # QTimer inside start_calibration for scene-graph safety).
        start_calibration(img, self.on_two_points_placed)

    def getStandardButtons(self):
        return int(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
        )

    def accept(self):

        if self.real_distance_mm <= 0 or self.measured_distance <= 0:
            self.instruction.setText(
                "<span style='color:#c05050;'>Click two points and "
                "enter a valid distance before accepting.</span>"
            )
            return False  # keep panel open

        scale_factor = self.real_distance_mm / self.measured_distance

        doc = FreeCAD.ActiveDocument

        doc.openTransaction("Calibrate Image")
        try:
            self.img.XSize = q_to_float(self.img.XSize) * scale_factor
            self.img.YSize = q_to_float(self.img.YSize) * scale_factor

            record_calibration_metadata(
                self.img,
                scale_factor,
                self.measured_distance,
                self.real_distance_mm
            )
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            self._cleanup()
            raise

        doc.recompute()

        FreeCAD.Console.PrintMessage(
            f"Scale factor: {scale_factor:.6f} — image resized to "
            f"{q_to_float(self.img.XSize):.3f} x "
            f"{q_to_float(self.img.YSize):.3f} mm.\n"
        )

        self._cleanup()
        return True  # close panel

    def reject(self):

        FreeCAD.Console.PrintMessage(
            "Calibration cancelled — image not resized.\n"
        )
        self._cleanup()
        return True  # close panel

    def _cleanup(self):

        cleanup_calibration_nodes(self.img)
        clear_calibration_state()
        restore_visibility()

    def reset_points(self):

        clear_calibration_state()
        hide_rubberband_line()

        self.measured_distance = 0.0
        self.real_distance_mm = 0.0
        self.measured_label.setText("—")
        self.real_input.setEnabled(False)
        self.real_input.clear()
        self.scale_label.setText("—")
        self.size_label.setText("—")
        self.reset_btn.setEnabled(False)
        self.instruction.setText(
            "Click two reference points on the image."
        )

    def on_two_points_placed(self, distance):

        self.measured_distance = distance
        self.measured_label.setText(f"<b>{distance:.3f} mm</b>")

        default_text = f"{distance:.3f} mm"
        self.real_input.setEnabled(True)
        self.real_input.setText(default_text)
        self.real_input.selectAll()
        self.real_input.setFocus()

        self.reset_btn.setEnabled(True)
        self.instruction.setText(
            "Enter the real-world distance and click OK."
        )
        # setText above already fires textChanged → update_preview,
        # but call it explicitly for clarity.
        self.update_preview(default_text)

    def update_preview(self, text):

        if self.measured_distance == 0:
            return

        try:
            parsed = q_to_float(
                FreeCAD.Units.Quantity(text).getValueAs("mm")
            )
        except Exception:
            self.scale_label.setText(
                "<span style='color:#c05050;'>can't parse</span>"
            )
            self.size_label.setText("—")
            self.real_distance_mm = 0.0
            return

        if parsed <= 0:
            self.scale_label.setText(
                "<span style='color:#c05050;'>must be positive</span>"
            )
            self.size_label.setText("—")
            self.real_distance_mm = 0.0
            return

        scale_factor = parsed / self.measured_distance
        new_x = q_to_float(self.img.XSize) * scale_factor
        new_y = q_to_float(self.img.YSize) * scale_factor

        self.scale_label.setText(f"×{scale_factor:.4f}")
        self.size_label.setText(
            f"{q_to_float(self.img.XSize):.2f} × "
            f"{q_to_float(self.img.YSize):.2f} mm  →  "
            f"<b>{new_x:.2f} × {new_y:.2f} mm</b>"
        )
        self.real_distance_mm = parsed


def record_calibration_metadata(img, scale_factor, measured_distance, real_distance):

    if not hasattr(img, "CalibrationScaleFactor"):
        img.addProperty(
            "App::PropertyFloat",
            "CalibrationScaleFactor",
            "Calibration",
            "Scale factor applied by the last Image Calibration run"
        )

    if not hasattr(img, "CalibrationDate"):
        img.addProperty(
            "App::PropertyString",
            "CalibrationDate",
            "Calibration",
            "Date/time this image was last calibrated"
        )

    if not hasattr(img, "CalibrationMeasuredDistance"):
        img.addProperty(
            "App::PropertyFloat",
            "CalibrationMeasuredDistance",
            "Calibration",
            "On-screen distance (mm, pre-scale) between the two clicked reference points"
        )

    if not hasattr(img, "CalibrationRealDistance"):
        img.addProperty(
            "App::PropertyFloat",
            "CalibrationRealDistance",
            "Calibration",
            "User-entered real-world distance (mm) the two reference points represent"
        )

    img.CalibrationScaleFactor = q_to_float(scale_factor)
    img.CalibrationDate = time.strftime("%Y-%m-%d %H:%M:%S")
    img.CalibrationMeasuredDistance = q_to_float(measured_distance)
    img.CalibrationRealDistance = q_to_float(real_distance)


def create_crosshair():

    from pivy import coin

    crosshair = coin.SoAnnotation()

    translation = coin.SoTranslation()

    crosshair.translation = translation

    coords = coin.SoCoordinate3()

    lines = coin.SoLineSet()

    lines.numVertices.setValues(
        0,
        2,
        [2, 2]
    )

    coords.point.setValues(
        0,
        4,
        [
            (-5, 0, 0),
            (5, 0, 0),
            (0, -5, 0),
            (0, 5, 0)
        ]
    )

    material = coin.SoMaterial()
    material.diffuseColor = (1.0, 0.0, 0.0)

    crosshair.addChild(material)
    crosshair.addChild(translation)
    crosshair.addChild(coords)
    crosshair.addChild(lines)

    return crosshair


class CalibrateImageCommand:

    def GetResources(self):

        return {
            "MenuText": "Calibrate Image",
            "ToolTip": "Calibrate an imported Image Plane",
            "Pixmap": ICON_PATH
        }

    def Activated(self):

        selection = FreeCADGui.Selection.getSelection()

        if len(selection) == 0:

            FreeCAD.Console.PrintError(
                "No image selected.\n"
            )

            return

        if len(selection) > 1:

            FreeCAD.Console.PrintError(
                "Multiple objects selected. "
                "Please select one image only.\n"
            )

            return

        img = selection[0]

        if img.TypeId != "Image::ImagePlane":

            FreeCAD.Console.PrintError(
                "Selected object is not an Image Plane.\n"
            )

            return

        FreeCAD.Console.PrintMessage(
            "Image selected:\n"
        )

        FreeCAD.Console.PrintMessage(
            f"{img.Label}\n"
        )

        FreeCAD.Console.PrintMessage(
            f"Size: {img.XSize} x {img.YSize}\n"
        )

        orient_camera_to_image(img)

        FreeCAD.Console.PrintMessage(
            "Camera aligned to image.\n"
        )

        # Close any existing task panel before opening ours — FreeCAD
        # only allows one at a time.
        FreeCADGui.Control.closeDialog()

        panel = CalibrationTaskPanel(img)
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):

        return True


FreeCADGui.addCommand(
    "CalibrateImage",
    CalibrateImageCommand()
)