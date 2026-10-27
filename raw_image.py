"""Raw camera-count inspector for Yb ASC triplets.

Install: python -m pip install numpy matplotlib PySide6

Run:     python raw_image_inspector.py

No subtraction, optical-density calculation, fitting, or image normalisation.

Images are rotated 90 degrees anticlockwise. The displayed Y axis starts at zero at the bottom, without changing the visible image orientation.

"""

from pathlib import Path

import sys

import numpy as np

from PySide6.QtCore import QDate

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,

    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QDateEdit,

    QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,

    QCheckBox, QTabWidget, QTextBrowser)

from matplotlib.figure import Figure

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from matplotlib.widgets import RectangleSelector

from matplotlib.patches import Rectangle

IMAGE_ROOT = Path(r"Z:\Ytterbium\Experimental_results\ascii_Images")

def image_folder_for_date(date):

    return IMAGE_ROOT / str(date.year()) / date.toString('MMM') / date.toString('dd')

def load_asc(path):

    try:

        array = np.loadtxt(path, dtype=float)

    except ValueError as exc:

        raise ValueError(f"Cannot parse {path.name} as a numeric ASC matrix: {exc}") from exc

    if array.ndim != 2 or array.size == 0:

        raise ValueError(f"Not a two-dimensional image: {path}")

    return array

class RawImageInspector(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle('Raw ASC pixel counts — 90° anticlockwise')

        self.resize(1500, 900)

        self.arrays = None

        self.region = None

        self.selectors = []

        self.axes = []

        self.roi_patches = []

        central = QWidget()

        self.setCentralWidget(central)

        outer_layout = QVBoxLayout(central)
        tabs = QTabWidget()
        outer_layout.addWidget(tabs)
        viewer = QWidget()
        tabs.addTab(viewer, 'Raw image viewer')
        instructions = QTextBrowser()
        instructions.setOpenExternalLinks(False)
        instructions.setHtml("""
        <h2>Raw image inspector — Instructions</h2>
        <h3>1. Loading raw images</h3>
        <p>Choose the experiment date and species, then check the ASC folder path or use
        <b>Browse</b> to select a different folder. Enter the shot number and click
        <b>Load raw images</b>. The three ASC files (a, b and c) are displayed side by side.</p>
        <h3>2. Image display</h3>
        <p>The images show raw camera counts without subtraction, optical-density
        calculation, fitting or normalisation. They are rotated 90° anticlockwise,
        with Y increasing upwards from zero at the bottom.</p>
        <p><b>Same colour scale for all three</b> applies a common minimum and maximum
        across the three images. Untick it to scale each image independently.</p>
        <h3>3. Selecting a region</h3>
        <p>Drag a rectangle on an image to select a region of interest (ROI).
        Alternatively, enter the square centre X and Y and its side length in pixels,
        then click <b>Apply square</b>. Tick <b>Click image to position square</b>
        to place the square by clicking an image. Use <b>Clear selected region</b>
        to return to statistics for the full image.</p>
        <h3>4. Counts and statistics</h3>
        <p>The table reports the number of pixels, mean, minimum, maximum and standard
        deviation of raw counts in the selected region for each image. Without a
        selection, these statistics cover the full image. Click a pixel (when
        square-placement mode is off) to read its exact counts in all three images.</p>
        """)
        tabs.addTab(instructions, 'Instructions')
        layout = QVBoxLayout(viewer)

        top = QHBoxLayout()

        top.addWidget(QLabel('Experiment date:'))

        self.date_edit = QDateEdit(QDate.currentDate())

        self.date_edit.setCalendarPopup(True)

        self.date_edit.setDisplayFormat('dd MMM yyyy')

        self.date_edit.dateChanged.connect(self.update_folder)

        top.addWidget(self.date_edit)

        top.addWidget(QLabel('Species:'))

        self.species = QLineEdit('Yb-174')

        self.species.setMaximumWidth(110)

        top.addWidget(self.species)

        top.addWidget(QLabel('Shot:'))

        self.shot = QSpinBox()

        self.shot.setRange(0, 1000000)

        top.addWidget(self.shot)

        load = QPushButton('Load raw images')

        load.clicked.connect(self.load_images)

        top.addWidget(load)

        top.addStretch()

        layout.addLayout(top)

        pathrow = QHBoxLayout()

        pathrow.addWidget(QLabel('ASC folder:'))

        self.folder = QLineEdit()

        pathrow.addWidget(self.folder, 1)

        browse = QPushButton('Browse')

        browse.clicked.connect(self.browse_folder)

        pathrow.addWidget(browse)

        layout.addLayout(pathrow)

        opts = QHBoxLayout()

        self.same_scale = QCheckBox('Same colour scale for all three')

        self.same_scale.setChecked(True)

        self.same_scale.toggled.connect(self.draw_images)

        opts.addWidget(self.same_scale)

        clear = QPushButton('Clear selected region')

        clear.clicked.connect(self.clear_region)

        opts.addWidget(clear)

        opts.addWidget(QLabel('Drag a rectangle, or set a square centre and side length below.'))

        opts.addStretch()

        layout.addLayout(opts)

        square_row = QHBoxLayout()

        square_row.addWidget(QLabel('Square centre X:'))

        self.centre_x = QSpinBox()

        self.centre_x.setRange(0, 100000)

        square_row.addWidget(self.centre_x)

        square_row.addWidget(QLabel('Centre Y:'))

        self.centre_y = QSpinBox()

        self.centre_y.setRange(0, 100000)

        square_row.addWidget(self.centre_y)

        square_row.addWidget(QLabel('Side length (pixels):'))

        self.square_size = QSpinBox()

        self.square_size.setRange(1, 100000)

        self.square_size.setValue(50)

        square_row.addWidget(self.square_size)

        apply_square = QPushButton('Apply square')

        apply_square.clicked.connect(self.apply_square)

        square_row.addWidget(apply_square)

        self.click_to_centre = QCheckBox('Click image to position square')

        self.click_to_centre.toggled.connect(self.update_selection_mode)

        square_row.addWidget(self.click_to_centre)

        square_row.addStretch()

        layout.addLayout(square_row)

        self.figure = Figure(figsize=(14, 5), constrained_layout=True)

        self.canvas = FigureCanvasQTAgg(self.figure)

        self.canvas.mpl_connect('button_press_event', self.pixel_clicked)

        layout.addWidget(self.canvas, 1)

        self.region_label = QLabel('Region: full image (until you draw a rectangle)')

        layout.addWidget(self.region_label)

        self.table = QTableWidget(3, 6)

        self.table.setHorizontalHeaderLabels(['Image', 'Pixels', 'Mean (counts)', 'Minimum', 'Maximum', 'Std. dev.'])

        self.table.verticalHeader().setVisible(False)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.table.setMaximumHeight(145)

        layout.addWidget(self.table)

        self.pixel_label = QLabel('Click an individual pixel to read its exact counts in a, b and c.')

        layout.addWidget(self.pixel_label)

        self.status = QLabel('Choose the experiment date and shot, then load the images.')

        layout.addWidget(self.status)

        self.update_folder()

    def update_folder(self):

        self.folder.setText(str(image_folder_for_date(self.date_edit.date())))

    def browse_folder(self):

        current = Path(self.folder.text().strip())

        selected = QFileDialog.getExistingDirectory(self, 'Choose ASC image folder',

                                                     str(current if current.is_dir() else IMAGE_ROOT))

        if selected:

            self.folder.setText(selected)

    def load_images(self):

        stem = f'{self.species.text().strip()}_{self.date_edit.date().toString("ddMMMyyyy")}'

        folder = Path(self.folder.text().strip())

        paths = [folder / f'{letter}{stem}_{self.shot.value()}.asc' for letter in 'abc']

        try:

            for path in paths:

                if not path.is_file():

                    raise FileNotFoundError(f'ASC file not found: {path}')

            # Rotate 90 degrees anticlockwise, then reverse the array rows

            # so origin="lower" keeps the *same visual orientation*.

            # Coordinates and square selection now count Y from the bottom.

            arrays = [np.flipud(np.rot90(load_asc(path), k=1)) for path in paths]

            if not (arrays[0].shape == arrays[1].shape == arrays[2].shape):

                raise ValueError(f'Image dimensions differ: {[a.shape for a in arrays]}')

        except Exception as exc:

            QMessageBox.critical(self, 'Cannot load images', str(exc))

            return

        self.arrays = arrays

        self.centre_x.setMaximum(arrays[0].shape[1] - 1)

        self.centre_y.setMaximum(arrays[0].shape[0] - 1)

        self.square_size.setMaximum(min(arrays[0].shape))

        self.centre_x.setValue(arrays[0].shape[1] // 2)

        self.centre_y.setValue(arrays[0].shape[0] // 2)

        self.region = None

        self.draw_images()

        self.update_statistics()

        self.pixel_label.setText('Click an individual pixel to read its exact counts in a, b and c.')

        self.status.setText('Loaded: ' + ', '.join(p.name for p in paths))

    def draw_images(self, *_):

        if self.arrays is None:

            return

        self.selectors.clear()

        self.figure.clear()

        self.axes = []

        self.roi_patches = []

        labels = ['a — laser + atoms', 'b — laser only', 'c — reference / dark']

        global_min = min(float(np.nanmin(a)) for a in self.arrays)

        global_max = max(float(np.nanmax(a)) for a in self.arrays)

        for i, (array, title) in enumerate(zip(self.arrays, labels)):

            ax = self.figure.add_subplot(1, 3, i + 1)

            self.axes.append(ax)

            args = {'vmin': global_min, 'vmax': global_max} if self.same_scale.isChecked() else {}

            im = ax.imshow(array, cmap='gray', origin='lower', interpolation='nearest', **args)

            ax.set_title(title)

            ax.set_xlabel('Column x (pixel)')

            ax.set_ylabel('Row y (pixel)')

            self.figure.colorbar(im, ax=ax, shrink=0.75, label='Raw camera counts')

            selector = RectangleSelector(ax, self.rectangle_selected, useblit=False,

                                         button=[1], minspanx=2, minspany=2,

                                         spancoords='data', interactive=False)

            selector.set_active(not self.click_to_centre.isChecked())

            self.selectors.append(selector)

            if self.region is not None:

                y0, y1, x0, x1 = self.region

                patch = Rectangle((x0 - 0.5, y0 - 0.5), x1-x0, y1-y0,

                                  fill=False, edgecolor='red', linewidth=1.5)

                ax.add_patch(patch)

                self.roi_patches.append(patch)

        self.canvas.draw_idle()

    def rectangle_selected(self, click, release):

        if self.arrays is None or None in (click.xdata, click.ydata, release.xdata, release.ydata):

            return

        height, width = self.arrays[0].shape

        x0 = max(0, min(width, int(np.floor(min(click.xdata, release.xdata) + 0.5))))

        x1 = max(0, min(width, int(np.floor(max(click.xdata, release.xdata) + 0.5)) + 1))

        y0 = max(0, min(height, int(np.floor(min(click.ydata, release.ydata) + 0.5))))

        y1 = max(0, min(height, int(np.floor(max(click.ydata, release.ydata) + 0.5)) + 1))

        if x1 <= x0 or y1 <= y0:

            return

        self.region = (y0, y1, x0, x1)

        self.draw_images()

        self.update_statistics()

    def update_selection_mode(self, *_):

        for selector in self.selectors:

            selector.set_active(not self.click_to_centre.isChecked())

    def apply_square(self):

        """Square coordinates refer to the rotated image with Y=0 at the bottom."""

        if self.arrays is None:

            QMessageBox.information(self, 'No images', 'Load the raw images first.')

            return

        height, width = self.arrays[0].shape

        side = self.square_size.value()

        cx, cy = self.centre_x.value(), self.centre_y.value()

        # Exact side length for odd and even squares; centre is the central

        # pixel for odd sizes and the upper-left central pixel for even sizes.

        x0 = cx - (side - 1) // 2

        y0 = cy - (side - 1) // 2

        x1, y1 = x0 + side, y0 + side

        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:

            QMessageBox.warning(self, 'Square outside image',

                                'Choose a centre and side length that fit within the rotated image.')

            return

        self.region = (y0, y1, x0, x1)

        self.draw_images()

        self.update_statistics()

    def clear_region(self):

        self.region = None

        if self.arrays is not None:

            self.draw_images()

            self.update_statistics()

    def update_statistics(self):

        if self.arrays is None:

            return

        height, width = self.arrays[0].shape

        y0, y1, x0, x1 = self.region or (0, height, 0, width)

        self.region_label.setText(f'Region: x = {x0}…{x1-1}, y = {y0}…{y1-1} '

                                  f'({(x1-x0)*(y1-y0)} pixels)' +

                                  (' [full image]' if self.region is None else ' [selected ROI]'))

        for row, (letter, arr) in enumerate(zip('abc', self.arrays)):

            values = arr[y0:y1, x0:x1]

            fields = [letter, str(values.size), f'{np.mean(values):.3f}',

                      f'{np.min(values):.3f}', f'{np.max(values):.3f}',

                      f'{np.std(values):.3f}']

            for col, field in enumerate(fields):

                self.table.setItem(row, col, QTableWidgetItem(field))

    def pixel_clicked(self, event):

        if self.arrays is None or event.inaxes not in self.axes or event.xdata is None or event.ydata is None:

            return

        x, y = int(round(event.xdata)), int(round(event.ydata))

        if self.click_to_centre.isChecked():

            self.centre_x.setValue(x)

            self.centre_y.setValue(y)

            self.apply_square()

            return

        h, w = self.arrays[0].shape

        if not (0 <= x < w and 0 <= y < h):

            return

        a, b, c = (arr[y, x] for arr in self.arrays)

        self.pixel_label.setText(f'Pixel (x={x}, y={y}) — laser + atoms (a): {a:g} counts; '

                                 f'laser only (b): {b:g} counts; reference (c): {c:g} counts')

if __name__ == '__main__':

    app = QApplication(sys.argv)

    window = RawImageInspector()

    window.show()

    sys.exit(app.exec())
