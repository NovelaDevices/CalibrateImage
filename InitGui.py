import FreeCAD
import FreeCADGui

# Import at module load (not lazy in Initialize) so the CalibrateImage
# command is registered before anyone right-clicks — the context menu
# manipulator references it by name and would silently drop the entry
# if the command wasn't yet defined.
import CalibrateImage  # noqa: F401


class ImageCalibrationContextMenu:
    """Best-effort context menu manipulator. FreeCAD 1.0's manipulator
    API confirms modifyMenuBar and modifyToolBars, but modifyContextMenu
    isn't documented — some builds ignore it. Kept here for builds where
    it works. When it doesn't, the Workbench.ContextMenu method below
    still handles right-click within our own workbench."""

    def modifyMenuBar(self):
        return []

    def modifyToolBars(self):
        return []

    def modifyContextMenu(self, recipient):

        selection = FreeCADGui.Selection.getSelection()

        if len(selection) != 1:
            return []

        if selection[0].TypeId != "Image::ImagePlane":
            return []

        return [
            {
                "insert": "CalibrateImage",
                "menuItem": "Std_Delete",
            }
        ]


class ImageCalibrationWorkbench(FreeCADGui.Workbench):
    MenuText = "Image Calibration"
    ToolTip = "Tools for calibrating reference images"

    def Initialize(self):
        self.appendMenu(
            "Image Calibration",
            [
                "CalibrateImage"
            ]
        )
        self.appendToolbar(
            "Image Calibration",
            [
                "CalibrateImage"
            ]
        )

    def ContextMenu(self, recipient):
        """Called by FreeCAD when the user right-clicks in the tree
        or 3D view while this workbench is active. Guaranteed-stable
        API — the manipulator above is best-effort for the broader
        case of 'right-click regardless of active workbench'."""

        selection = FreeCADGui.Selection.getSelection()

        if len(selection) == 1 and selection[0].TypeId == "Image::ImagePlane":
            self.appendContextMenu("", ["CalibrateImage"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(
    ImageCalibrationWorkbench()
)

# Register context menu manipulator AFTER the workbench. Wrapped in
# try/except because addWorkbenchManipulator exists but may ignore
# modifyContextMenu on some FreeCAD 1.0 builds — silent no-op is fine,
# the ContextMenu method in the workbench still covers the primary
# use case.
try:
    FreeCADGui.addWorkbenchManipulator(ImageCalibrationContextMenu())
except AttributeError:
    pass