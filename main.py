import sys
import json
import math
import statistics
from html import escape
from pathlib import Path
from PySide6.QtCore import QDate, Qt, QMimeData, QTimer, QSettings
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QDateEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QMessageBox, QAbstractItemView, QPlainTextEdit, QLineEdit, QComboBox, QTabWidget, QFileDialog
)
DATA_ROOT = Path(r"Z:\Ytterbium\Experimental_results\logfiles")
BASIC_COLUMNS = [
    ("Shot", "dx", "integer"),
    ("Variable", "variable", "number"),
    ("N (pixel sum)", "Pixelsum Atomnumber", "atoms"),
    ("N (Gaussian X)", "Gaussian x Atomnumber", "atoms"),
    ("N (Gaussian Y)", "Gaussian y Atomnumber", "atoms"),
    ("X width (µm)", "Gaussian x Width (μm)", "number"),
    ("Y width (µm)", "Gaussian y Width (μm)", "number"),
    ("Peak OD", "Pixelsum Peak OD", "number"),
]
DETAIL_COLUMNS = [
    ("X centre (µm)", "Gaussian x Center (μm)", "number"),
    ("Y centre (µm)", "Gaussian y Center (μm)", "number"),
    ("X height", "Gaussian x Height", "number"),
    ("Y height", "Gaussian y Height", "number"),
    ("X offset", "Gaussian x Offset", "number"),
    ("Y offset", "Gaussian y Offset", "number"),
]
def numeric(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None
def parameter(shot, key, field="value"):
    item = shot.get(key)
    if isinstance(item, dict):
        return numeric(item.get(field))
    return numeric(item) if field == "value" else None
def valid_gaussian(shot, axis):
    """Check centre, width, and atom number for one Gaussian projection."""
    roi = shot.get("roi")
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return False
    limits = (roi[2], roi[3]) if axis == "x" else (roi[0], roi[1])
    lo, hi = (numeric(v) for v in limits)
    if lo is None or hi is None or hi <= lo:
        return False
    prefix = f"Gaussian {axis} "
    centre = parameter(shot, prefix + "Center (px)")
    width = parameter(shot, prefix + "Width (px)")
    atoms = parameter(shot, prefix + "Atomnumber")
    if any(v is None for v in (centre, width, atoms)):
        return False
    if not (lo <= centre <= hi):
        return False
    if not (0 < width < hi - lo):
        return False
    if atoms <= 0:
        return False
    return True
def display(value, kind):
    n = numeric(value)
    if n is None:
        return "N/A"
    if kind == "integer":
        return str(int(n))
    if kind == "atoms":
        return f"{n:.3e}"
    return f"{n:.3f}"
def parse_shot_selection(text, max_shot=None):
    """Return None for all shots, or selected dx values including N-end ranges."""
    if not text.strip():
        return None
    selected = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError("Empty item in image number selection.")
        if "-" in part:
            pieces = [x.strip() for x in part.split("-")]
            if len(pieces) != 2 or not pieces[0].isdigit():
                raise ValueError(f"Invalid range: {part}")
            first = int(pieces[0])
            if pieces[1].lower() == "end":
                if max_shot is None or first > max_shot:
                    continue
                last = max_shot
            elif pieces[1].isdigit():
                last = int(pieces[1])
            else:
                raise ValueError(f"Invalid range: {part}")
            if last < first:
                raise ValueError(f"Range must increase: {part}")
            if last - first > 100000:
                raise ValueError("Range is too large.")
            selected.update(range(first, last + 1))
        elif part.isdigit():
            selected.add(int(part))
        else:
            raise ValueError(f"Invalid image number: {part}")
    return selected

class NumericTableItem(QTableWidgetItem):
    """Sort numerical shot IDs numerically, not alphabetically."""
    def __lt__(self, other):
        a = numeric(self.text())
        b = numeric(other.text())
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)

class Skimmer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Skimmer - Experimental Results")
        self.resize(1200, 700)
        self.shots = []
        self.visible_shots = []
        self.loaded_date = None
        self.loaded_files = []
        self.watched_folder = None
        self.folder_signature = None
        self.settings = QSettings("Skimmer", "ExperimentalResults")
        # Check the selected folder every five seconds.
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.check_for_updates)
        self.refresh_timer.start()
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        self.main_tabs = QTabWidget()
        outer_layout.addWidget(self.main_tabs)

        results_page = QWidget()
        layout = QVBoxLayout(results_page)
        self.main_tabs.addTab(results_page, "Results")

        # The selected root contains Year / Month / Day subfolders.
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(QLabel("Data folder:"))
        self.data_folder = QLineEdit(str(self.settings.value("data_root", str(DATA_ROOT))))
        self.data_folder.setToolTip("Root logfiles folder containing Year / Month / Day folders")
        self.data_folder.editingFinished.connect(self.change_data_folder)
        folder_row.addWidget(self.data_folder, 1)
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self.browse_data_folder)
        folder_row.addWidget(browse_button)
        layout.addLayout(folder_row)

        self.date_selector = QDateEdit()
        self.date_selector.setCalendarPopup(True)
        self.date_selector.setDisplayFormat("dd MMM yyyy")
        self.date_selector.setDate(QDate.currentDate())
        load_button = QPushButton("Load Results")
        load_button.clicked.connect(lambda: self.load_results())
        self.analysis_mode = QComboBox()
        self.analysis_mode.addItems([
            "Original date only", "Reanalyses only", "Original + reanalyses"
        ])
        self.analysis_mode.setToolTip(
            "Original: JSON filename matches experiment date. "
            "Reanalyses: other dated Yb-174 JSON files in that day's folder."
        )
        self.analysis_mode.currentIndexChanged.connect(lambda _index: self.load_results())
        self.auto_refresh = QCheckBox("Auto-refresh (5 s)")
        self.auto_refresh.setChecked(True)
        self.image_filter = QLineEdit()
        self.image_filter.setPlaceholderText("All, or e.g. 10-20, 35, 40-end")
        self.image_filter.setMinimumWidth(210)
        self.image_filter.returnPressed.connect(self.update_table)
        filter_button = QPushButton("Apply")
        filter_button.clicked.connect(self.update_table)
        self.variable_header = QLineEdit("Variable")
        self.variable_header.setPlaceholderText("e.g. Hold time (ms)")
        self.variable_header.setMinimumWidth(145)
        self.variable_header.setToolTip(
            "Change only the displayed column heading; source files remain unchanged."
        )
        self.variable_header.textChanged.connect(self.update_variable_header)
        self.details_checkbox = QCheckBox("Show detailed fit parameters and errors")
        self.details_checkbox.toggled.connect(self.update_table)
        # Keep each label immediately next to its corresponding control.
        def add_group(row, label, widget):
            group = QHBoxLayout()
            group.setSpacing(5)
            group.addWidget(QLabel(label))
            group.addWidget(widget)
            row.addLayout(group)

        controls = QHBoxLayout()
        controls.setSpacing(18)
        add_group(controls, "Experiment date:", self.date_selector)
        controls.addWidget(load_button)
        add_group(controls, "Analysis files:", self.analysis_mode)
        controls.addWidget(self.auto_refresh)
        controls.addStretch()

        controls_second = QHBoxLayout()
        controls_second.setSpacing(18)
        add_group(controls_second, "Image numbers:", self.image_filter)
        controls_second.addWidget(filter_button)
        add_group(controls_second, "Variable header:", self.variable_header)
        controls_second.addWidget(self.details_checkbox)
        controls_second.addStretch()

        layout.addLayout(controls)
        layout.addLayout(controls_second)
        self.info_label = QLabel("Select a date and load results.")
        layout.addWidget(self.info_label)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        table_actions = QHBoxLayout()
        copy_table = QPushButton("Copy Table")
        copy_table.clicked.connect(self.copy_table)
        table_actions.addWidget(copy_table)
        table_actions.addStretch()
        layout.addLayout(table_actions)
        layout.addWidget(QLabel("Averages (selected images; valid values only)"))
        self.average_table = QTableWidget()
        self.average_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.average_table.setMaximumHeight(145)
        self.average_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.average_table)
        average_actions = QHBoxLayout()
        copy_averages = QPushButton("Copy Averages")
        copy_averages.clicked.connect(self.copy_averages)
        average_actions.addWidget(copy_averages)
        average_actions.addStretch()
        layout.addLayout(average_actions)
        summary_actions = QHBoxLayout()
        copy_all = QPushButton("Copy Full Summary")
        copy_all.clicked.connect(self.copy_full_summary)
        summary_actions.addWidget(copy_all)
        summary_actions.addStretch()
        layout.addLayout(summary_actions)

        # Main tab 2: experiment configuration and invalid-fit criteria.
        experiment_page = QWidget()
        experiment_layout = QVBoxLayout(experiment_page)
        experiment_layout.addWidget(QLabel("Experiment / analysis configuration"))
        self.configuration = QPlainTextEdit()
        self.configuration.setReadOnly(True)
        experiment_layout.addWidget(self.configuration)
        experiment_layout.addWidget(QLabel("Criteria for Invalid results"))
        self.invalid_criteria = QPlainTextEdit()
        self.invalid_criteria.setReadOnly(True)
        self.invalid_criteria.setPlainText(self.invalid_rules_text())
        experiment_layout.addWidget(self.invalid_criteria)
        self.main_tabs.addTab(experiment_page, "Experiment information")

        # Main tab 3: usage instructions.
        instructions_page = QWidget()
        instructions_layout = QVBoxLayout(instructions_page)
        self.usage_instructions = QPlainTextEdit()
        self.usage_instructions.setReadOnly(True)
        self.usage_instructions.setPlainText(self.usage_text())
        instructions_layout.addWidget(self.usage_instructions)
        self.main_tabs.addTab(instructions_page, "How to use")

    def browse_data_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, "Choose data folder (contains Year / Month / Day)",
            self.data_folder.text().strip() or str(DATA_ROOT)
        )
        if selected:
            self.data_folder.setText(selected)
            self.change_data_folder()

    def change_data_folder(self):
        path = self.data_folder.text().strip().strip('"')
        if not path:
            self.data_folder.setText(str(DATA_ROOT))
            path = str(DATA_ROOT)
        self.settings.setValue("data_root", path)
        # Prevent surveillance of the previously selected lab folder.
        self.watched_folder = None
        self.folder_signature = None
        self.statusBar().showMessage("Data folder changed. Click Load Results.", 5000)

    def folder_state(self, folder):
        """Snapshot file names, sizes and modification times."""
        if not folder.is_dir():
            return None
        try:
            return tuple(sorted(
                (p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in folder.glob("*.json")
            ))
        except OSError:
            return None
    def check_for_updates(self):
        if not self.auto_refresh.isChecked() or self.watched_folder is None:
            return
        signature = self.folder_state(self.watched_folder)
        if signature is not None and signature != self.folder_signature:
            self.load_results(background=True)
    def load_results(self, background=False):
        selected_date = self.date_selector.date().toPython()
        folder = (Path(self.data_folder.text().strip().strip('"')) / str(selected_date.year)
                  / selected_date.strftime("%b") / selected_date.strftime("%d"))
        # On a manual load, switch the monitored folder to the selected date.
        if not background:
            self.watched_folder = folder
            self.folder_signature = None
        elif folder != self.watched_folder:
            return
        signature_before = self.folder_state(folder)
        if signature_before is None:
            if not background:
                QMessageBox.warning(self, "Directory not found",
                                    f"Cannot access:\\\n{folder}")
            self.statusBar().showMessage("Waiting for experiment folder...", 5000)
            return
        # Select by the date embedded in the filename, not by shot dx.
        # Reanalyses are files with a different date in this experiment folder.
        species_files = sorted(folder.glob("Yb-174_*.json"))
        expected_name = f"Yb-174_{selected_date:%d%b%Y}.json"
        mode = self.analysis_mode.currentIndex()
        if mode == 0:  # Original date only
            json_files = [p for p in species_files if p.name == expected_name]
        elif mode == 1:  # Reanalyses only
            json_files = [p for p in species_files if p.name != expected_name]
        else:  # Both
            json_files = species_files
        if not json_files:
            if not background:
                QMessageBox.information(self, "No results",
                                        f"No matching JSON files found in:\\\n{folder}")
            self.statusBar().showMessage("Waiting for JSON results...", 5000)
            return
        new_shots, loaded_files, configs = [], [], []
        try:
            for path in json_files:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                records = data.get("_default", {})
                if not isinstance(records, dict):
                    raise ValueError(f"Unexpected JSON structure in {path.name}")
                for entry_id, shot in records.items():
                    if not isinstance(shot, dict):
                        continue
                    record = dict(shot)
                    record["_entry_id"] = entry_id
                    record["_source_file"] = path.name
                    new_shots.append(record)
                    if isinstance(shot.get("config"), dict):
                        configs.append(shot["config"])
                loaded_files.append(path.name)
        except (OSError, ValueError, TypeError) as error:
            # A writer may temporarily leave JSON incomplete. Keep the
            # previous results and retry automatically at the next poll.
            if not background:
                QMessageBox.warning(self, "File error", str(error))
            else:
                self.statusBar().showMessage(
                    "Data being written; will retry automatically.", 5000
                )
            return
        signature_after = self.folder_state(folder)
        if signature_after != signature_before:
            # Files changed while we were reading them: retry next time.
            return
        # Display shots in numerical order, regardless of JSON insertion order.
        self.shots = sorted(
            new_shots,
            key=lambda shot: (
                numeric(shot.get("dx")) is None,
                numeric(shot.get("dx")) if numeric(shot.get("dx")) is not None else float("inf"),
            ),
        )
        self.loaded_date = selected_date
        self.loaded_files = loaded_files
        self.folder_signature = signature_after
        self.update_configuration(configs)
        self.update_table()
        self.statusBar().showMessage(
            f"Loaded {len(new_shots)} entries from {len(loaded_files)} file(s).",
            4000
        )
    def update_variable_header(self, _text):
        """Update headings and copied output without reloading data."""
        if self.table.columnCount():
            self.table.setHorizontalHeaderItem(
                1, QTableWidgetItem(self.variable_header.text().strip() or "Variable")
            )
        if self.average_table.columnCount():
            self.average_table.setHorizontalHeaderItem(
                0, QTableWidgetItem(self.variable_header.text().strip() or "Variable")
            )
    def columns(self):
        columns = list(BASIC_COLUMNS)
        columns[1] = (
            self.variable_header.text().strip() or "Variable",
            "variable",
            "number",
        )
        if self.details_checkbox.isChecked():
            columns.extend(DETAIL_COLUMNS)
            for label, key, kind in list(columns):
                if key.startswith("Gaussian "):
                    columns.append((f"{label} error", key + " [error]", kind))
        return columns
    def update_table(self):
        try:
            shot_numbers = [numeric(shot.get("dx")) for shot in self.shots]
            max_shot = int(max(n for n in shot_numbers if n is not None)) if any(n is not None for n in shot_numbers) else None
            selected = parse_shot_selection(self.image_filter.text(), max_shot)
        except ValueError as error:
            QMessageBox.warning(self, "Invalid image selection", str(error))
            return
        self.visible_shots = [
            shot for shot in self.shots
            if selected is None or (
                numeric(shot.get("dx")) is not None
                and numeric(shot.get("dx")) in selected
            )
        ]
        if self.loaded_date is not None:
            invalid_x = sum(not valid_gaussian(s, "x") for s in self.visible_shots)
            invalid_y = sum(not valid_gaussian(s, "y") for s in self.visible_shots)
            self.info_label.setText(
                f"{self.loaded_date:%d %B %Y}  |  "
                f"Showing {len(self.visible_shots)} of {len(self.shots)} entries  |  "
                f"Invalid X: {invalid_x}, Invalid Y: {invalid_y}"
            )
        columns = self.columns()
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setRowCount(len(self.visible_shots))
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([label for label, _, _ in columns])
        for row, shot in enumerate(self.visible_shots):
            x_ok = valid_gaussian(shot, "x")
            y_ok = valid_gaussian(shot, "y")
            for col, (_, key, kind) in enumerate(columns):
                base_key = key.removesuffix(" [error]")
                axis = "x" if base_key.startswith("Gaussian x ") else (
                    "y" if base_key.startswith("Gaussian y ") else None
                )
                if axis is not None and not (x_ok if axis == "x" else y_ok):
                    text = "Invalid"
                elif key.endswith(" [error]"):
                    text = display(parameter(shot, base_key, "error"), kind)
                else:
                    text = display(parameter(shot, key), kind)
                    if base_key == "Pixelsum Atomnumber":
                        value = parameter(shot, base_key)
                        if value is not None and value < 0:
                            text = "Invalid"
                item = NumericTableItem(text) if col == 0 else QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.update_averages()
    def update_averages(self):
        """Mean of each numeric measurement for the selected shots."""
        columns = self.columns()[1:]  # Shot identifiers must not be averaged
        self.average_table.clear()
        self.average_table.setColumnCount(len(columns))
        self.average_table.setRowCount(2)
        self.average_table.setVerticalHeaderLabels(["Mean", "Std dev"])
        self.average_table.setHorizontalHeaderLabels(
            [label for label, _, _ in columns]
        )
        for col, (_, key, kind) in enumerate(columns):
            values = []
            base_key = key.removesuffix(" [error]")
            axis = ("x" if base_key.startswith("Gaussian x ") else
                    "y" if base_key.startswith("Gaussian y ") else None)
            for shot in self.visible_shots:
                if axis and not valid_gaussian(shot, axis):
                    continue
                if key.endswith(" [error]"):
                    value = parameter(shot, base_key, "error")
                else:
                    value = parameter(shot, key)
                if value is None:
                    continue
                if base_key == "Pixelsum Atomnumber" and value < 0:
                    continue
                values.append(value)
            mean = sum(values) / len(values) if values else None
            text = display(mean, kind) if mean is not None else "N/A"
            item = QTableWidgetItem(text)
            item.setToolTip(f"{len(values)} valid value(s)")
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.average_table.setItem(0, col, item)
            # Sample standard deviation (n-1); undefined for fewer than 2 values.
            std = statistics.stdev(values) if len(values) >= 2 else None
            std_item = QTableWidgetItem(
                display(std, kind) if std is not None else "N/A"
            )
            std_item.setToolTip(f"{len(values)} valid value(s); sample SD")
            std_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.average_table.setItem(1, col, std_item)
    def averages_text(self):
        if self.average_table.columnCount() == 0:
            return ""
        headers = ["Statistic"] + [
            self.average_table.horizontalHeaderItem(c).text()
            for c in range(self.average_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row, name in enumerate(("Mean", "Std dev")):
            values = [name] + [
                self.average_table.item(row, c).text()
                for c in range(self.average_table.columnCount())
            ]
            lines.append("\t".join(values))
        return "\n".join(lines)
    def averages_html(self):
        if self.average_table.columnCount() == 0:
            return ""
        style = "border:1px solid #b8c2cc;padding:5px 9px;font-family:Arial,sans-serif;"
        headings = f'<th style="{style}background-color:#e9eef4;">Statistic</th>'
        headings += "".join(
            f'<th style="{style}background-color:#e9eef4;">'
            f'{escape(self.average_table.horizontalHeaderItem(c).text())}</th>'
            for c in range(self.average_table.columnCount())
        )
        rows = []
        for row, name in enumerate(("Mean", "Std dev")):
            cells = f'<th style="{style}">{name}</th>'
            cells += "".join(
                f'<td style="{style}text-align:right;">'
                f'{escape(self.average_table.item(row, c).text())}</td>'
                for c in range(self.average_table.columnCount())
            )
            rows.append('<tr>' + cells + '</tr>')
        return ('<table style="border-collapse:collapse;"><thead><tr>'
                + headings + '</tr></thead><tbody>' + ''.join(rows)
                + '</tbody></table>')
    def update_configuration(self, configs):
        if not configs:
            self.configuration.setPlainText("No configuration information available.")
            return
        config = configs[0]
        species_data = config.get("speciesdata", {})
        lines = [
            f"Species: {config.get('species', 'Unknown')}",
            f"Imaging processor: {config.get('processor', 'N/A')}",
            f"I/Isat: {config.get('IoverIsat', 'N/A')}",
            f"OD saturation setting: {config.get('OD_sat', 'N/A')}",
        ]
        wavelength = numeric(species_data.get("wavelength"))
        pixel_size = numeric(config.get("pixelsize"))
        if wavelength is not None:
            lines.append(f"Wavelength: {wavelength * 1e9:.3f} nm")
        if pixel_size is not None:
            lines.append(f"Effective pixel size: {pixel_size * 1e6:.3f} µm")
        if any(c != config for c in configs):
            lines.append("Note: Configuration varies between entries.")
        self.configuration.setPlainText("\n".join(lines))

    @staticmethod
    def usage_text():
        return (
            "1. Choose the data folder (the root containing Year / Month / Day folders), "
            "then choose the experiment date and click Load Results. "
            "Browse changes the folder, which is remembered next time.\n"
            "2. Analysis files: choose Original date only, Reanalyses only, "
            "or Original + reanalyses. Reanalyses are JSON files with a "
            "different date in the selected experiment folder.\n"
            "3. Auto-refresh (5 s) reloads results when JSON files change.\n"
            "4. Image numbers: leave blank for all shots, or enter e.g. "
            "10-20, 35, 40-end and click Apply (or press Enter).\n"
            "5. Variable header changes the displayed heading, not the data.\n"
            "6. Show detailed fit parameters and errors adds extra columns.\n"
            "7. Click column headings to sort the table. Shots load in "
            "ascending numerical order by default.\n"
            "8. Averages show the mean and sample standard deviation for "
            "the currently filtered shots, excluding invalid values.\n"
            "9. Copy Table, Copy Averages and Copy Full Summary put results "
            "on the clipboard for pasting into OneNote or Excel.\n"
            "10. Check Experiment information for the fit-quality cuts."
        )

    @staticmethod
    def invalid_rules_text():
        return ("Invalid criteria (checked separately for Gaussian X and Y):\n"
                "• ROI must have four values and valid, increasing limits.\n"
                "• Fitted centre, width and atom number must be present and finite.\n"
                "• Fitted centre must lie within the ROI limits (inclusive).\n"
                "• Fitted width must satisfy 0 < width < ROI extent (in pixels).\n"
                "• Fitted Gaussian atom number must be > 0.\n"
                "• Pixel-sum atom number is marked Invalid if negative.\n"
                "Invalid values are excluded from averages; source JSON is unchanged.")
    def table_text(self):
        if self.table.rowCount() == 0:
            return ""
        lines = ["\t".join(self.table.horizontalHeaderItem(c).text()
                           for c in range(self.table.columnCount()))]
        for row in range(self.table.rowCount()):
            lines.append("\t".join(self.table.item(row, col).text()
                                   for col in range(self.table.columnCount())))
        return "\n".join(lines)
    def table_html(self):
        """Build an HTML table that OneNote/Word can paste as a real table."""
        if self.table.rowCount() == 0:
            return ""
        cell_style = (
            "border:1px solid #b8c2cc;padding:5px 9px;"
            "font-family:Arial,sans-serif;font-size:10pt;"
        )
        html = [
            '<table style="border-collapse:collapse;border-spacing:0;">',
            '<thead><tr>',
        ]
        for col in range(self.table.columnCount()):
            label = escape(self.table.horizontalHeaderItem(col).text())
            html.append(
                f'<th style="{cell_style}background-color:#e9eef4;'
                f'text-align:right;font-weight:bold;">{label}</th>'
            )
        html.append('</tr></thead><tbody>')
        for row in range(self.table.rowCount()):
            bg = '#ffffff' if row % 2 == 0 else '#f5f7fa'
            html.append('<tr>')
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                value = escape(item.text() if item else '')
                html.append(
                    f'<td style="{cell_style}background-color:{bg};'
                    f'text-align:right;">{value}</td>'
                )
            html.append('</tr>')
        html.append('</tbody></table>')
        return ''.join(html)
    def set_rich_clipboard(self, html, plain_text):
        """Provide both rich HTML and plain text for different paste targets."""
        mime = QMimeData()
        mime.setHtml('<html><body>' + html + '</body></html>')
        mime.setText(plain_text)
        QApplication.clipboard().setMimeData(mime)
    def copy_table(self):
        html = self.table_html()
        if html:
            self.set_rich_clipboard(html, self.table_text())
            self.statusBar().showMessage("Rich-text table copied", 4000)
    def copy_averages(self):
        html = self.averages_html()
        if html:
            self.set_rich_clipboard(html, self.averages_text())
            self.statusBar().showMessage("Averages and standard deviations copied", 4000)
    def copy_full_summary(self):
        if not self.visible_shots:
            return
        lines = [
            "EXPERIMENTAL RESULTS",
            f"Date: {self.loaded_date:%d %B %Y}",
            f"Entries shown: {len(self.visible_shots)} of {len(self.shots)}",
            f"Image selection: {self.image_filter.text().strip() or 'All'}",
            f"Files: {', '.join(self.loaded_files)}",
            "", "ANALYSIS CONFIGURATION", self.configuration.toPlainText(),
            "", "INVALID CRITERIA", self.invalid_rules_text(),
            "", "SHOT RESULTS", self.table_text(), "", "AVERAGES", self.averages_text()
        ]
        info = [
            f"Date: {self.loaded_date:%d %B %Y}",
            f"Entries shown: {len(self.visible_shots)} of {len(self.shots)}",
            f"Image selection: {self.image_filter.text().strip() or 'All'}",
            f"Files: {', '.join(self.loaded_files)}",
        ]
        html = (
            '<h2 style="font-family:Arial,sans-serif;">Experimental results</h2>'
            + ''.join(f'<p style="margin:3px 0;font-family:Arial,sans-serif;">'
                      f'{escape(line)}</p>' for line in info)
            + '<h3>Analysis configuration</h3>'
            + '<p style="font-family:Arial,sans-serif;">'
            + '<br>'.join(escape(line) for line in
                          self.configuration.toPlainText().splitlines())
            + '</p><h3>Invalid criteria</h3><p>' + '<br>'.join(escape(line) for line in self.invalid_rules_text().splitlines()) + '</p><h3>Shot results</h3>' + self.table_html() + '<h3>Averages</h3>' + self.averages_html()
        )
        self.set_rich_clipboard(html, "\n".join(lines))
        self.statusBar().showMessage("Rich-text summary copied", 4000)
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Skimmer()
    window.show()
    sys.exit(app.exec())
