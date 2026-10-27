"""Skimmer absorption-image viewer; JSON and ASC folders independently selectable.
"""

from pathlib import Path

import json

import sys

import re



import numpy as np

from PySide6.QtCore import QDate, Qt, QTimer

from PySide6.QtWidgets import (

    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,

    QLabel, QLineEdit, QPushButton, QSpinBox, QFileDialog, QMessageBox, QProgressBar, QApplication,

    QDateEdit, QCheckBox, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QAbstractItemView, QDialog, QTabWidget, QTextBrowser,

)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from matplotlib.figure import Figure

from matplotlib.patches import Rectangle



JSON_ROOT = Path("Z:\\Ytterbium\\Experimental_results\\logfiles")

IMAGE_ROOT = Path("Z:\\Ytterbium\\Experimental_results\\ascii_Images")





def json_for_date(date, species):

    """Construct the date-specific JSON path."""

    year = str(date.year())

    month = date.toString("MMM")  # Jan, Feb, ... Oct

    day = date.toString("dd")

    stem = f"{species}_{day}{month}{year}"

    return JSON_ROOT / year / month / day / f"{stem}.json"





def image_folder_for_date(date):

    """Construct the date-specific camera image directory."""

    return IMAGE_ROOT / str(date.year()) / date.toString("MMM") / date.toString("dd")





def read_asc(path):

    """Read numeric ASCII matrix; special camera headers need a sample file."""

    try:

        arr = np.loadtxt(path, dtype=float)

    except ValueError as exc:

        raise ValueError(

            f"Cannot read {path.name} as a numeric matrix. "

            "Please share one .asc file to identify its header format."

        ) from exc

    if arr.ndim != 2 or not arr.size:

        raise ValueError(f"{path.name} is not a 2D numeric image")

    return arr





def load_shot(json_path, shot, image_dir, image_stem=None, record_key=None):

    if not json_path.is_file():

        raise FileNotFoundError(f"Analysis JSON not found:\n{json_path}")

    with json_path.open(encoding="utf-8") as f:

        data = json.load(f)

    records = data.get("_default", {})

    if record_key is not None:

        entry = records.get(str(record_key))

        matches = [entry] if isinstance(entry, dict) and str(entry.get("dx")) == str(shot) else []

    else:

        matches = [e for e in records.values() if str(e.get("dx")) == str(shot)]

    if not matches:

        raise ValueError(f"Shot {shot} not found in {json_path.name}")

    entry = matches[0]

    roi = entry.get("roi")

    if not isinstance(roi, (list, tuple)) or len(roi) != 4:

        raise ValueError(f"Shot {shot} has no valid four-element ROI")

    y0, y1, x0, x1 = map(int, roi)

    if not (0 <= y0 < y1 and 0 <= x0 < x1):

        raise ValueError(f"Invalid ROI: {roi}")



    stem = image_stem or json_path.stem

    paths = [image_dir / f"{letter}{stem}_{shot}.asc" for letter in "abc"]

    for path in paths:

        if not path.is_file():

            raise FileNotFoundError(f"Camera image not found:\n{path}")

    a, b, c = (read_asc(p) for p in paths)

    if not (a.shape == b.shape == c.shape):

        raise ValueError(f"Camera image shapes differ: {a.shape}, {b.shape}, {c.shape}")

    processor_name = str(entry.get("config", {}).get("processor", "")).lower()

    if "fluorescence" in processor_name:

        method = "Fluorescence"

    elif "absorption" in processor_name:

        method = "Absorption"

    else:

        method = "Unknown"

    return_data = (a, b, c)

    with np.errstate(divide="ignore", invalid="ignore"):

        od = np.nan_to_num(-np.log((a - c) / (b - c)), posinf=0, neginf=0).T

    if method == "Fluorescence":

        od = (a - b).T

    if y1 > od.shape[0] or x1 > od.shape[1]:

        raise ValueError(f"ROI {roi} exceeds OD image dimensions {od.shape}")

    return od, (y0, y1, x0, x1), paths, entry, method, return_data





def od_display_limit(entry, od):
    """Read an explicit display limit, otherwise use the finite image maximum.

    Peak OD is an analysis measurement, not a colourbar setting.
    """
    config = entry.get("config", {})
    sources = [entry, config] if isinstance(config, dict) else [entry]
    names = ("OD_max", "OD_max_display", "OD_vmax", "od_vmax",
             "od_max", "OD display max", "OD colour scale max",
             "OD color scale max", "vmax_OD", "maxOD")
    for source in sources:
        for name in names:
            value = source.get(name)
            if isinstance(value, dict):
                value = value.get("value")
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(value) and value > 0:
                return value
    finite = od[np.isfinite(od)]
    maximum = float(finite.max()) if finite.size else 0.0
    return maximum if maximum > 0 else 1.0


class ImageViewer(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("Skimmer — Image Viewer")

        self.resize(1280, 800)

        self.surveillance_mode = None

        self.surveillance_done = set()

        self.surveillance_baseline = set()

        self.surveillance_failures = {}

        self.surveillance_timer = QTimer(self)

        self.surveillance_timer.setInterval(3000)

        self.surveillance_timer.timeout.connect(self.surveil_tick)

        root = QWidget()

        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)
        viewer_page = QWidget()
        layout = QVBoxLayout(viewer_page)
        self.tabs.addTab(viewer_page, "Image viewer")
        instructions = QTextBrowser()
        instructions.setOpenExternalLinks(False)
        instructions.setHtml("""
        <h2>How to use</h2>
        <h3>1. Loading an image</h3>
        <p>Choose the experiment date and species.<br>
        Check the analysis JSON and ASC image folder paths, or use 'Browse' to select them independently.<br>
        Enter the shot number and choose the analysis record if more than one exists.<br>
        Click <b>Load image</b>.</p>
        <h3>2. Image display</h3>
        <p><b>Zoom to ROI</b> limits the view to the region saved in the JSON.
        The coloured rectangles identify the reference and atom regions.<br>
        <b>Imaging method</b> defaults to the method specified in the JSON, but can be overridden.<br>
        Tick <b>Fix OD color scale to 2</b> to fix the colour bar to 0–2; otherwise it will use
        the maximum OD as the upper limit of the colour scale.<br>
        Fluorescence images use an automatically scaled camera-count difference.</p>
        <h3>3. Results and saving</h3>
        <p>The tables show measurements from the selected JSON analysis and mean raw camera counts
        within the saved ROI. <b>Save PNG</b> exports the image and results to the selected ASC
        folder.</p>
        <h3>4. Surveillance</h3>
        <p><b>Surveil directory</b> can process all complete image triplets or only those arriving
        after surveillance starts. It saves PNGs automatically and retries shots whose analysis
        is not yet available. Stop surveillance before changing folders.</p>
        """)
        self.tabs.addTab(instructions, "Instructions")



        date_row = QHBoxLayout()

        date_row.addWidget(QLabel("Experiment date:"))

        self.date_edit = QDateEdit(QDate.currentDate())

        self.date_edit.setCalendarPopup(True)

        self.date_edit.setDisplayFormat("dd MMM yyyy")

        date_row.addWidget(self.date_edit)

        date_row.addWidget(QLabel("Species:"))

        self.species_edit = QLineEdit("Yb-174")

        self.species_edit.setMaximumWidth(140)

        date_row.addWidget(self.species_edit)

        date_row.addStretch()

        layout.addLayout(date_row)



        json_row = QHBoxLayout()

        json_row.addWidget(QLabel("Analysis JSON:"))

        self.json_edit = QLineEdit()

        json_row.addWidget(self.json_edit, 1)

        browse = QPushButton("Browse")

        browse.clicked.connect(self.browse_json)

        json_row.addWidget(browse)

        layout.addLayout(json_row)



        image_row = QHBoxLayout()

        image_row.addWidget(QLabel("ASC image folder:"))

        self.image_dir_edit = QLineEdit()

        image_row.addWidget(self.image_dir_edit, 1)

        browse_images = QPushButton("Browse")

        browse_images.clicked.connect(self.browse_images)

        image_row.addWidget(browse_images)

        self.surveil_button = QPushButton("Surveil directory")

        self.surveil_button.clicked.connect(self.choose_surveillance)

        image_row.addWidget(self.surveil_button)

        layout.addLayout(image_row)



        controls = QHBoxLayout()

        controls.addWidget(QLabel("Shot number:"))

        self.shot_spin = QSpinBox()

        self.shot_spin.setRange(0, 1000000)

        controls.addWidget(self.shot_spin)

        controls.addWidget(QLabel("Analysis:"))

        self.shot_analysis_choice = QComboBox()

        self.shot_analysis_choice.setFixedWidth(260)
        self.shot_analysis_choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.shot_analysis_choice.setMinimumContentsLength(16)

        self.shot_analysis_choice.activated.connect(self.load_image)

        controls.addWidget(self.shot_analysis_choice)

        self.shot_spin.valueChanged.connect(self.refresh_shot_analyses)

        load_button = QPushButton("Load image")

        load_button.clicked.connect(self.load_image)

        controls.addWidget(load_button)

        self.progress = QProgressBar()

        self.progress.setRange(0, 0)  # Busy animation: duration cannot be predicted

        self.progress.setFixedWidth(90)

        self.progress.setFixedHeight(12)

        self.progress.setVisible(False)

        controls.addWidget(self.progress)

        self.processing = False

        self.zoom_checkbox = QCheckBox("Zoom to ROI")

        self.zoom_checkbox.setChecked(True)

        self.zoom_checkbox.toggled.connect(self.draw_image)

        display_controls = QHBoxLayout()
        display_controls.addWidget(self.zoom_checkbox)

        self.fix_od_checkbox = QCheckBox("Fix OD color scale to 2")
        self.fix_od_checkbox.setChecked(False)
        self.fix_od_checkbox.setToolTip("When checked, absorption images use OD 0–2; otherwise use the JSON display limit or image maximum.")
        self.fix_od_checkbox.toggled.connect(self.draw_image)
        display_controls.addWidget(self.fix_od_checkbox)



        display_controls.addWidget(QLabel("Imaging method:"))

        self.method_combo = QComboBox()

        self.method_combo.addItems(["From JSON", "Absorption", "Fluorescence"])

        self.method_combo.currentIndexChanged.connect(self.update_method)

        display_controls.addWidget(self.method_combo)

        self.save_button = QPushButton("Save PNG")

        self.save_button.clicked.connect(self.save_png)

        self.save_button.setEnabled(False)

        display_controls.addWidget(self.save_button)

        controls.addStretch()

        layout.addLayout(controls)
        display_controls.addStretch()
        layout.addLayout(display_controls)



        splitter = QSplitter(Qt.Horizontal)

        self.figure = Figure(figsize=(8, 7), constrained_layout=True)

        self.canvas = FigureCanvasQTAgg(self.figure)

        splitter.addWidget(self.canvas)

        result_panel = QWidget()

        result_layout = QVBoxLayout(result_panel)

        result_layout.addWidget(QLabel("Analysis results (JSON)"))

        self.results_table = QTableWidget(9, 3)

        self.results_table.setHorizontalHeaderLabels(["Parameter", "X", "Y"])

        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.results_table.setSelectionMode(QAbstractItemView.NoSelection)

        self.results_table.setFocusPolicy(Qt.NoFocus)

        self.results_table.setWordWrap(True)

        self.results_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.results_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.results_table.verticalHeader().setVisible(False)

        self.results_table.setMinimumWidth(350)

        self.results_table.setSizeAdjustPolicy(QAbstractItemView.AdjustToContents)

        result_layout.addWidget(self.results_table, 0)

        result_layout.addSpacing(18)

        result_layout.addWidget(QLabel("Raw camera counts — JSON ROI"))

        self.counts_table = QTableWidget(1, 3)

        self.counts_table.setHorizontalHeaderLabels(["a — Atoms + laser", "b — Laser", "c — Reference"])

        self.counts_table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.counts_table.setSelectionMode(QAbstractItemView.NoSelection)

        self.counts_table.setFocusPolicy(Qt.NoFocus)

        self.counts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.counts_table.verticalHeader().setVisible(False)

        self.counts_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.counts_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.counts_table.setMinimumWidth(350)

        self.counts_table.setFixedHeight(70)

        result_layout.addWidget(self.counts_table, 0)

        result_layout.addWidget(QLabel("Mean counts per pixel within the saved ROI"))

        result_layout.addStretch(1)

        splitter.addWidget(result_panel)

        splitter.setStretchFactor(0, 3)

        splitter.setStretchFactor(1, 2)

        splitter.setSizes([870, 550])

        layout.addWidget(splitter, 1)

        self.status = QLabel("Select a date and shot, then click Load image.")

        layout.addWidget(self.status)

        self.watch_indicator = QLabel("●  Surveillance stopped")

        self.watch_indicator.setStyleSheet("color: #777777; font-weight: 600;")

        layout.addWidget(self.watch_indicator)



        self.json_edit.editingFinished.connect(

            lambda: self.refresh_shot_analyses(preferred_path=Path(self.json_edit.text().strip())))

        self.date_edit.dateChanged.connect(self.update_paths)

        self.species_edit.textChanged.connect(self.update_paths)

        self.update_paths()



    def update_paths(self):

        """Update both file locations when the experiment date changes."""

        date = self.date_edit.date()

        self.image_dir_edit.setText(str(image_folder_for_date(date)))

        self.json_edit.setText(str(json_for_date(date, self.species_edit.text().strip())))

        self.refresh_shot_analyses()

        self.stop_surveillance()



    def refresh_shot_analyses(self, *_args, preferred_path=None):

        """Show every analysis record for the selected acquisition shot."""

        shot = self.shot_spin.value()

        current = self.shot_analysis_choice.currentData()

        original = json_for_date(self.date_edit.date(), self.species_edit.text().strip())

        species = self.species_edit.text().strip()

        candidates = [original]

        if original.parent.is_dir():

            candidates.extend(sorted(

                (p for p in original.parent.glob("*.json") if p != original),

                key=lambda p: p.name))

        manual = Path(self.json_edit.text().strip())

        if manual not in candidates:

            candidates.append(manual)

        self.shot_analysis_choice.blockSignals(True)

        self.shot_analysis_choice.clear()

        for path in candidates:

            if not path.is_file():

                continue

            try:

                with path.open(encoding="utf-8") as handle:

                    records = json.load(handle).get("_default", {})

                for key, entry in records.items():

                    if str(entry.get("dx")) != str(shot):

                        continue

                    label = ("Original" if path == original else path.name)

                    roi = entry.get("roi", "unknown")

                    short_label = "Original" if path == original else path.stem
                    short_label = f"{short_label} (record {key})"
                    self.shot_analysis_choice.addItem(short_label, (str(path), str(key)))
                    self.shot_analysis_choice.setItemData(
                        self.shot_analysis_choice.count() - 1,
                        f"{label} | record {key} | ROI {roi}", Qt.ToolTipRole)

            except (OSError, ValueError, AttributeError):

                continue  # A JSON file may be mid-write during acquisition.

        index = -1

        if preferred_path is not None:

            for i in range(self.shot_analysis_choice.count()):

                data = self.shot_analysis_choice.itemData(i)

                if data and Path(data[0]) == preferred_path:

                    index = i

                    break

        if index < 0:

            index = self.shot_analysis_choice.findData(current)

        self.shot_analysis_choice.setCurrentIndex(index if index >= 0 else 0)

        self.shot_analysis_choice.blockSignals(False)

        count = self.shot_analysis_choice.count()

        self.shot_analysis_choice.setToolTip(f"{count} analysis record(s) found for shot {shot}")

        self.shot_analysis_choice.setEnabled(count > 1)



    def image_stem(self):

        """Camera images retain the acquisition date, even after reanalysis."""

        return json_for_date(self.date_edit.date(), self.species_edit.text().strip()).stem



    def browse_json(self):

        current = Path(self.json_edit.text().strip())

        folder = current.parent if current.parent.exists() else JSON_ROOT

        filename, _ = QFileDialog.getOpenFileName(

            self, "Choose analysis JSON", str(folder), "JSON files (*.json)"

        )

        if filename:

            self.json_edit.setText(filename)

            self.refresh_shot_analyses(preferred_path=Path(filename))



    def browse_images(self):

        current = Path(self.image_dir_edit.text().strip())

        folder = current if current.is_dir() else IMAGE_ROOT

        chosen = QFileDialog.getExistingDirectory(

            self, "Choose ASC image folder", str(folder)

        )

        if chosen:

            self.image_dir_edit.setText(chosen)

            self.stop_surveillance()



    def choose_surveillance(self):

        if self.surveillance_timer.isActive():

            self.stop_surveillance()

            return

        dialog = QDialog(self)

        dialog.setWindowTitle("Surveil directory")

        dialog.setModal(True)

        box = QVBoxLayout(dialog)

        box.addWidget(QLabel("Automatically analyse and save PNGs for:"))

        all_button = QPushButton("All images in the directory")

        new_button = QPushButton("Only new images in the directory")

        box.addWidget(all_button)

        box.addWidget(new_button)

        choice = {"mode": None}

        def select(mode):

            choice["mode"] = mode

            dialog.accept()

        all_button.clicked.connect(lambda: select("all"))

        new_button.clicked.connect(lambda: select("new"))

        if dialog.exec() != QDialog.Accepted:

            return

        self.start_surveillance(choice["mode"])



    def available_shots(self):

        """Only shots with all three ASC files are ready for consideration."""

        folder = Path(self.image_dir_edit.text().strip())

        stem = self.image_stem()

        if not folder.is_dir():

            raise FileNotFoundError(f"ASC directory not found: {folder}")

        pattern = re.compile(r"^a" + re.escape(stem) + r"_(\d+)\.asc$", re.IGNORECASE)

        complete = set()

        for file in folder.glob("*.asc"):

            match = pattern.fullmatch(file.name)

            if match:

                number = int(match.group(1))

                if all((folder / f"{letter}{stem}_{number}.asc").is_file() for letter in "abc"):

                    complete.add(number)

        return complete



    def start_surveillance(self, mode):

        try:

            baseline = self.available_shots()

            if not Path(self.json_edit.text().strip()).is_file():

                raise FileNotFoundError("Analysis JSON is not available yet.")

        except Exception as exc:

            QMessageBox.warning(self, "Cannot start surveillance", str(exc))

            return

        self.surveillance_mode = mode

        self.surveillance_baseline = baseline if mode == "new" else set()

        self.surveillance_done = set()

        self.surveillance_failures = {}

        self.surveil_button.setText("Stop surveillance")

        self.surveillance_timer.start()

        self.update_watch_indicator()

        self.status.setText(

            "Surveillance active — " +

            ("only shots arriving after start" if mode == "new" else "all complete shots")

        )

        self.surveil_tick()



    def stop_surveillance(self):

        self.surveillance_timer.stop()

        self.surveillance_mode = None

        if hasattr(self, "surveil_button"):

            self.surveil_button.setText("Surveil directory")

        if hasattr(self, "watch_indicator"):

            self.update_watch_indicator()



    def update_watch_indicator(self):

        """A steady status dot while surveillance is waiting for new shots."""

        if self.surveillance_mode is None:

            self.watch_indicator.setText("●  Surveillance stopped")

            colour = "#777777"

        elif self.processing:

            self.watch_indicator.setText("●  Processing image…")

            colour = "#b87912"

        else:

            self.watch_indicator.setText("●  Watching for new images")

            colour = "#21834a"

        self.watch_indicator.setStyleSheet(f"color: {colour}; font-weight: 600;")



    def set_processing(self, active, message=None):

        """Animate only for manual loads; surveillance uses a quiet status dot."""

        self.processing = active

        self.progress.setVisible(active and self.surveillance_mode is None)

        if message is not None:

            self.status.setText(message)

        self.update_watch_indicator()

        QApplication.processEvents()



    def surveil_tick(self):

        """Poll for complete triplets; retry shots while their JSON analysis is pending."""

        if self.surveillance_mode is None or self.processing:

            return

        try:

            shots = sorted(self.available_shots() - self.surveillance_baseline - self.surveillance_done)

        except Exception as exc:

            self.status.setText(f"Surveillance: {exc}")

            return

        # Process at most two per poll to keep the interface responsive.

        for shot in shots[:2]:

            try:

                self.set_processing(True, f"Surveillance: analysing shot {shot}…")

                json_path = Path(self.json_edit.text().strip())

                image_dir = Path(self.image_dir_edit.text().strip())

                image, roi, paths, entry, detected, raw = load_shot(json_path, shot, image_dir, self.image_stem())

                selected = self.method_combo.currentText()

                method = detected if selected == "From JSON" else selected

                if method == "Unknown":

                    raise ValueError(f"Shot {shot}: imaging method missing from JSON")

                a, b, c = raw

                with np.errstate(divide="ignore", invalid="ignore"):

                    processed = (np.nan_to_num(-np.log((a-c)/(b-c)), posinf=0, neginf=0).T

                                 if method == "Absorption" else (a-b).T)

                self.current_image = (image, roi, json_path, shot, paths, entry, detected, raw)

                self.current_display = (processed, roi, json_path, shot, method)

                self.shot_spin.setValue(shot)

                self.update_results(entry)

                self.draw_image()

                self.save_button.setEnabled(True)

                self.set_processing(True, f"Surveillance: saving shot {shot} PNG…")

                output = self.export_png()

                self.surveillance_done.add(shot)

                self.surveillance_failures.pop(shot, None)

                self.status.setText(f"Surveillance: saved {output.name} ({len(self.surveillance_done)} this session)")

            except Exception as exc:

                message = str(exc)

                # Incomplete analysis/partially written files are retried next poll.

                if self.surveillance_failures.get(shot) != message:

                    self.status.setText(f"Waiting for shot {shot}: {message}")

                self.surveillance_failures[shot] = message

            finally:

                self.set_processing(False)

                if self.surveillance_mode is not None and shot in self.surveillance_done:

                    self.status.setText(

                        f"Surveillance active — last saved shot {shot} "

                        f"({len(self.surveillance_done)} saved this session)")



    def load_image(self):

        if self.processing:

            return

        self.set_processing(True, f"Loading shot {self.shot_spin.value()}…")

        try:

            json_path = Path(self.json_edit.text().strip())

            shot = self.shot_spin.value()

            chosen = self.shot_analysis_choice.currentData()

            if chosen is not None:

                json_path = Path(chosen[0])

                record_key = chosen[1]

            else:

                record_key = None

            image, roi, paths, entry, method, raw = load_shot(json_path, shot, Path(self.image_dir_edit.text().strip()), self.image_stem(), record_key)

            self.current_image = (image, roi, json_path, shot, paths, entry, method, raw)

            self.update_results(entry)

            self.update_method()

            self.save_button.setEnabled(hasattr(self, "current_display"))

            self.status.setText("Loaded: " + ", ".join(p.name for p in paths))

        except Exception as exc:

            QMessageBox.critical(self, "Could not load image", str(exc))

            self.status.setText(str(exc))

        finally:

            self.set_processing(False)



    @staticmethod

    def format_result(item):

        if item is None:

            return "—"

        if isinstance(item, dict):

            value, error = item.get("value"), item.get("error")

        else:

            value, error = item, None

        if not isinstance(value, (int, float)):

            return str(value) if value is not None else "—"

        def fmt(x):

            return f"{x:.3g}"

        return fmt(value) + (" ± " + fmt(error) if isinstance(error, (int, float)) else "")



    def mean_camera_counts(self):

        """Mean raw a/b/c pixel counts inside the saved JSON ROI."""

        if not hasattr(self, "current_image"):

            return [None, None, None]

        _, roi, _, _, _, _, _, raw = self.current_image

        y0, y1, x0, x1 = roi

        means = []

        for array in raw:

            oriented = array.T

            if not (0 <= y0 < y1 <= oriented.shape[0] and

                    0 <= x0 < x1 <= oriented.shape[1]):

                raise ValueError(f"ROI {roi} exceeds camera image dimensions {oriented.shape}")

            means.append(float(np.mean(oriented[y0:y1, x0:x1])))

        return means



    def analysis_table_rows(self, entry):

        return [[label, self.format_result(entry.get(xkey)),

                 self.format_result(entry.get(ykey)) if ykey else ""]

                for label, xkey, ykey in self.result_rows()]



    def update_results(self, entry):

        rows = self.analysis_table_rows(entry)

        self.results_table.setRowCount(len(rows))

        for i, fields in enumerate(rows):

            for j, value in enumerate(fields):

                self.results_table.setItem(i, j, QTableWidgetItem(value))

        self.results_table.resizeRowsToContents()

        self.fit_results_table_height()

        for col, mean in enumerate(self.mean_camera_counts()):

            self.counts_table.setItem(0, col, QTableWidgetItem(

                f"{mean:.3f}" if mean is not None else "—"))



    def fit_results_table_height(self):

        """Make every result row visible without a scrollbar."""

        table = self.results_table

        header = table.horizontalHeader().height()

        rows = sum(table.rowHeight(i) for i in range(table.rowCount()))

        frame = 2 * table.frameWidth()

        table.setFixedHeight(header + rows + frame + 4)



    def export_png(self):

        """Save image and analysis beside the raw ASC images, without a dialog."""

        if not hasattr(self, "current_display"):

            raise RuntimeError("No image loaded")

        od, roi, json_path, shot, method = self.current_display

        entry = self.current_image[5]

        image_dir = Path(self.image_dir_edit.text().strip())

        date_label = self.date_edit.date().toString("ddMMMyyyy")

        chosen = self.shot_analysis_choice.currentData()

        version = (f", {Path(chosen[0]).stem}, record {chosen[1]}" if chosen else "")

        filename = image_dir / f"Image Dx{shot}, {date_label}, {method}{version}.png"

        if not image_dir.is_dir():

            raise FileNotFoundError(f"Image directory not found: {image_dir}")

        fig = Figure(figsize=(12.5, 6.4), dpi=160)

        grid = fig.add_gridspec(1, 2, width_ratios=[1.15, 1.35],

                               left=0.055, right=0.985, bottom=0.07,

                               top=0.93, wspace=0.045)

        ax = fig.add_subplot(grid[0, 0])

        self.render_image(ax, fig, od, roi, json_path, shot, method)

        results_ax = fig.add_subplot(grid[0, 1])

        results_ax.axis("off")

        results_ax.set_title("Analysis results (JSON)",

                             loc="left", fontsize=14, pad=12)

        cells = self.analysis_table_rows(entry)

        # Keep scientific notation compact in PNG tables without rounding away
        # any of the significant digits already shown by format_result().
        def compact_exponents(value):
            return re.sub(r"e\+?(-?)0*(\d+)",
                          lambda m: "e" + m.group(1) + m.group(2), value)

        cells = [[compact_exponents(value) for value in row] for row in cells]

        tbl = results_ax.table(cellText=cells,

                               colLabels=["Parameter", "X", "Y"],

                               colWidths=[0.40, 0.30, 0.30],

                               cellLoc="left", loc="upper center",

                               bbox=[0, 0.30, 1, 0.67])

        tbl.auto_set_font_size(False)

        tbl.set_fontsize(11)

        for (r, c), cell in tbl.get_celld().items():

            cell.set_linewidth(0.6)

            cell.set_edgecolor("#c8c8c8")

            cell.PAD = 0.025

            if r > 0 and c > 0 and len(cell.get_text().get_text()) > 18:
                cell.get_text().set_fontsize(9.5)

            if r == 0:

                cell.set_facecolor("#e9eef5")

                cell.get_text().set_weight("bold")

        means = self.mean_camera_counts()

        results_ax.text(0, 0.22, "Raw camera counts — JSON ROI",

                        transform=results_ax.transAxes, fontsize=12, weight="bold")

        count_tbl = results_ax.table(

            cellText=[[f"{m:.3f}" if m is not None else "—" for m in means]],

            colLabels=["a — Atoms + laser", "b — Laser", "c — Reference"],

            colWidths=[0.34, 0.33, 0.33], cellLoc="center",

            bbox=[0, 0.09, 1, 0.12])

        count_tbl.auto_set_font_size(False)

        count_tbl.set_fontsize(11)

        for (r, c), cell in count_tbl.get_celld().items():

            cell.set_linewidth(0.6)

            cell.set_edgecolor("#c8c8c8")

            if r == 0:

                cell.set_facecolor("#e9eef5")

                cell.get_text().set_weight("bold")

        fig.savefig(filename, dpi=160, facecolor="white", bbox_inches="tight", pad_inches=0.12)

        return filename



    def save_png(self):

        """Save immediately, without a save dialog."""

        try:

            filename = self.export_png()

            self.status.setText(f"Saved PNG: {filename}")

        except Exception as exc:

            QMessageBox.critical(self, "Could not save PNG", str(exc))



    @staticmethod

    def result_rows():

        return [

            ("Gaussian height", "Gaussian x Height", "Gaussian y Height"),

            ("Gaussian centre (px)", "Gaussian x Center (px)", "Gaussian y Center (px)"),

            ("Gaussian width (px)", "Gaussian x Width (px)", "Gaussian y Width (px)"),

            ("Gaussian offset", "Gaussian x Offset", "Gaussian y Offset"),

            ("Gaussian centre (µm)", "Gaussian x Center (μm)", "Gaussian y Center (μm)"),

            ("Gaussian width (µm)", "Gaussian x Width (μm)", "Gaussian y Width (μm)"),

            ("Gaussian atom number", "Gaussian x Atomnumber", "Gaussian y Atomnumber"),

            ("Pixel-sum atom number", "Pixelsum Atomnumber", None),

            ("Pixel-sum peak OD", "Pixelsum Peak OD", None),

        ]



    def update_method(self, *_):

        if not hasattr(self, "current_image"):

            return

        image, roi, json_path, shot, paths, entry, detected, raw = self.current_image

        selected = self.method_combo.currentText()

        method = detected if selected == "From JSON" else selected

        if method == "Unknown":

            self.status.setText("No recognised imaging method in JSON. Select Absorption or Fluorescence.")

            self.figure.clear()

            self.canvas.draw_idle()

            return

        a, b, c = raw

        with np.errstate(divide="ignore", invalid="ignore"):

            image = (np.nan_to_num(-np.log((a-c)/(b-c)), posinf=0, neginf=0).T

                     if method == "Absorption" else (a-b).T)

        self.current_display = (image, roi, json_path, shot, method)

        self.draw_image()



    def render_image(self, ax, figure, od, roi, json_path, shot, method):

        """Use identical image formatting for the GUI and exported PNG."""

        y0, y1, x0, x1 = roi

        if method == "Absorption":

            im = ax.imshow(od, origin="upper", cmap="jet", vmin=0,
                           vmax=(2.0 if self.fix_od_checkbox.isChecked()
                                 else od_display_limit(self.current_image[5], od)),

                           interpolation="nearest")

            color_label = "Optical density (OD)"

        else:

            im = ax.imshow(od, origin="upper", cmap="jet", interpolation="nearest")

            color_label = "Fluorescence (camera counts difference)"

        figure.colorbar(im, ax=ax, label=color_label)

        h = (y1 - y0) // 2

        if h > 1 and x1 - x0 > 1:

            ax.add_patch(Rectangle((x0 + 1, y0 + 1), x1 - x0 - 1, h - 1,

                                   fill=False, edgecolor="red", linewidth=2,

                                   label="Reference"))

            ax.add_patch(Rectangle((x0 + 1, y0 + 1 + h), x1 - x0 - 1, h - 1,

                                   fill=False, edgecolor="lime", linewidth=2,

                                   label="Atoms"))

            ax.legend(loc="upper right", fontsize=8)

        if self.zoom_checkbox.isChecked():

            pad_x = max(3, (x1 - x0) * 0.05)

            pad_y = max(3, (y1 - y0) * 0.05)

            ax.set_xlim(x0 - pad_x, x1 + pad_x)

            ax.set_ylim(y0 - pad_y, y1 + pad_y)

        else:

            ax.set_xlim(-0.5, od.shape[1] - 0.5)

            ax.set_ylim(-0.5, od.shape[0] - 0.5)

        ax.set(xlabel="X (pixels)", ylabel="Y (pixels)",

               title=f"Image Dx{shot}, {self.date_edit.date().toString('ddMMMyyyy')}, {method}")



    def draw_image(self, *_):

        if not hasattr(self, "current_image"):

            return

        if not hasattr(self, "current_display"):

            return

        od, roi, json_path, shot, method = self.current_display

        y0, y1, x0, x1 = roi



        self.figure.clear()

        ax = self.figure.add_subplot(111)

        self.render_image(ax, self.figure, od, roi, json_path, shot, method)

        self.canvas.draw_idle()





if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = ImageViewer()

    window.show()

    sys.exit(app.exec())
