from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from automated_image_capture.validation import ValidationSettings, points


class ValidationPreview(QLabel):
    line_changed = pyqtSignal(float)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.validation_visible = False
        self.line_editable = True
        self.line_fraction = 0.05
        self.entry_fraction = 0.75
        self.source_size = (0, 0)
        self.tracks = []

    def _image_rect(self):
        width, height = self.source_size
        if not width or not height:
            return 0.0, 0.0, 0.0, 0.0
        scale = min(self.width() / width, self.height() / height)
        return (
            (self.width() - width * scale) / 2,
            (self.height() - height * scale) / 2,
            width * scale,
            height * scale,
        )

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self.validation_visible:
            return
        x, y, width, height = self._image_rect()
        if width <= 0:
            return
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#f43f5e"), 2))
        line = round(x + width * self.line_fraction)
        painter.drawLine(line, round(y), line, round(y + height))
        painter.drawText(line + 5, round(y + 20), "Abschluss")
        painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.PenStyle.DashLine))
        entry = round(x + width * self.entry_fraction)
        painter.drawLine(entry, round(y), entry, round(y + height))
        painter.drawText(entry + 5, round(y + 20), "Eintritt")
        for track in self.tracks:
            polygon = points(track.detection)
            tx = x + float(polygon[:, 0].min()) / self.source_size[0] * width
            ty = y + float(polygon[:, 1].max()) / self.source_size[1] * height
            color = "#94a3b8" if track.finalized else "#f59e0b" if track.ambiguous else "#4ade80"
            painter.setPen(QColor(color))
            painter.drawText(
                round(tx),
                round(ty + 18),
                f"ID {track.track_id}" + (" · fixiert" if track.finalized else ""),
            )

    def _choose_line(self, event):
        if not self.validation_visible or not self.line_editable:
            return
        x, y, width, height = self._image_rect()
        if width > 0 and y <= event.position().y() <= y + height:
            fraction = max(0.0, min(0.5, (event.position().x() - x) / width))
            self.line_changed.emit(fraction)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._choose_line(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._choose_line(event)


class ValidationPanel(QWidget):
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    speed_requested = pyqtSignal(float)
    export_requested = pyqtSignal(str)
    reset_requested = pyqtSignal()
    line_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.status = QLabel("Modell laden; Bandrichtung Rechts, Bildbewegung rechts → links.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 200)
        self.speed.setValue(10)
        self.speed.setSuffix(" mm/s")
        speed_row = QHBoxLayout()
        speed_row.addWidget(self.speed)
        self.apply_speed = QPushButton("Übernehmen")
        self.apply_speed.clicked.connect(lambda: self.speed_requested.emit(self.speed.value()))
        speed_row.addWidget(self.apply_speed)
        form.addRow("Bandgeschwindigkeit", speed_row)
        self.line = QDoubleSpinBox()
        self.line.setRange(0, 50)
        self.line.setValue(5)
        self.line.setSuffix(" % vom linken Rand")
        self.line.valueChanged.connect(lambda value: self.line_changed.emit(value / 100))
        form.addRow("Abschlusslinie", self.line)
        self.output = QLineEdit(str(Path.home() / "Pictures" / "YOLOValidation"))
        browse = QPushButton("Ordner …")
        browse.clicked.connect(self._browse)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output)
        output_row.addWidget(browse)
        self.browse = browse
        form.addRow("Sitzungssicherung", output_row)
        layout.addLayout(form)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Erweiterte Auswertung")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.advanced_toggle)
        self.parameters = QGroupBox()
        self.parameters.hide()
        self.advanced_toggle.toggled.connect(self.parameters.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda opened: self.advanced_toggle.setArrowType(
                Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
            )
        )
        advanced = QFormLayout(self.parameters)
        self.min_observations = QSpinBox()
        self.min_observations.setRange(2, 1000)
        self.min_observations.setValue(5)
        self.min_duration = QDoubleSpinBox()
        self.min_duration.setRange(0, 60)
        self.min_duration.setValue(0.25)
        self.min_duration.setSuffix(" s")
        self.min_consensus = QDoubleSpinBox()
        self.min_consensus.setRange(51, 100)
        self.min_consensus.setValue(70)
        self.min_consensus.setSuffix(" %")
        advanced.addRow("Gültige Beobachtungen", self.min_observations)
        advanced.addRow("Beobachtungsdauer", self.min_duration)
        advanced.addRow("Konsensanteil", self.min_consensus)
        layout.addWidget(self.parameters)
        buttons = QHBoxLayout()
        self.start = QPushButton("Validation starten")
        self.stop = QPushButton("Stop")
        self.start.clicked.connect(self.start_requested.emit)
        self.stop.clicked.connect(self.stop_requested.emit)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Aktuell verfolgte Bauteile"))
        self.active = self._table(["ID", "Rohpose", "Konsenspose", "N", "Status"])
        self.active.setMinimumHeight(90)
        layout.addWidget(self.active)
        layout.addWidget(QLabel("Abgeschlossene Bauteile · Doppelklick öffnet den Beleg"))
        self.results = self._table(["ID", "Zeit", "Pose", "Konsens", "N", "Status", "Grund"])
        self.results.setMinimumHeight(150)
        self.results.cellDoubleClicked.connect(self._open_image)
        layout.addWidget(self.results)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        # Counts and export/reset stay visible while the setup/results area scrolls.
        layout = root
        layout.addWidget(QLabel("Summen · Konsensanteil ist keine Modellwahrscheinlichkeit"))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.counts = self._table(["Modellpose / Status", "Anzahl"])
        self.counts.setMinimumHeight(110)
        self.counts.setMaximumHeight(150)
        layout.addWidget(self.counts)
        actions = QHBoxLayout()
        self.export = QPushButton("Exportieren …")
        self.reset = QPushButton("Liste zurücksetzen")
        self.export.clicked.connect(self._export)
        self.reset.clicked.connect(self._reset)
        actions.addWidget(self.export)
        actions.addWidget(self.reset)
        layout.addLayout(actions)
        self._controller = None
        self.set_busy(False)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setResizeContentsPrecision(100)
        table.verticalHeader().hide()
        return table

    @staticmethod
    def _row(table, row, values):
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(str(value)))

    def settings(self) -> ValidationSettings:
        return ValidationSettings(
            speed_mm_s=self.speed.value(),
            exit_fraction=self.line.value() / 100,
            min_observations=self.min_observations.value(),
            min_duration_s=self.min_duration.value(),
            min_consensus=self.min_consensus.value() / 100,
        ).validated()

    def set_busy(self, busy):
        for widget in (self.line, self.parameters, self.output, self.browse, self.reset):
            widget.setEnabled(not busy)
        self.start.setEnabled(not busy)
        self.stop.setEnabled(busy)
        self.apply_speed.setEnabled(busy)

    def refresh(self, controller):
        self._controller = controller
        self.status.setText(controller.message)
        self.set_busy(controller.running)
        self.apply_speed.setEnabled(controller.state == "running")
        self.speed.setEnabled(controller.state in {"idle", "running"})
        self.export.setEnabled(controller.store is not None)
        tracks = (
            [t for t in controller.tracker.tracks.values() if not t.finalized]
            if controller.tracker
            else []
        )
        self.active.setRowCount(len(tracks))
        classes = controller.model.classes if controller.model else {}
        for row, track in enumerate(tracks):
            winner = classes.get(track.winner, "–")
            self._row(
                self.active,
                row,
                [
                    track.track_id,
                    track.detection.class_name,
                    f"{winner} ({track.consensus:.0%})",
                    track.observations,
                    "Mehrdeutig" if track.ambiguous else "Beobachte",
                ],
            )
        results = controller.tracker.results if controller.tracker else []
        if self.results.rowCount() > len(results):
            self.results.setRowCount(0)
        for index in range(self.results.rowCount(), len(results)):
            result = results[index]
            self.results.insertRow(index)
            self._row(
                self.results,
                index,
                [
                    result.track_id,
                    datetime.fromtimestamp(result.finished_at).strftime("%H:%M:%S"),
                    result.pose,
                    f"{result.consensus:.0%}",
                    result.observations,
                    result.status,
                    result.reason,
                ],
            )
            for column in range(self.results.columnCount()):
                self.results.item(index, column).setToolTip(result.reason)
        totals = controller.tracker.pose_counts() if controller.tracker else {}
        statuses = (
            controller.tracker.counts() if controller.tracker else {"Unklar": 0, "Unvollständig": 0}
        )
        self.summary.setText(
            f"Abgeschlossen: {len(results)} · Erkannt: {sum(totals.values())}\n"
            f"Unklar: {statuses['Unklar']} · Unvollständig: {statuses['Unvollständig']}"
        )
        rows = [(f"{cid}: {name}", totals.get(cid, 0)) for cid, name in classes.items()]
        rows += list(statuses.items())
        self.counts.setRowCount(len(rows))
        for index, values in enumerate(rows):
            self._row(self.counts, index, values)

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, "Sitzungsordner", self.output.text())
        if directory:
            self.output.setText(directory)

    def _export(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Ergebnisse exportieren", self.output.text()
        )
        if directory:
            self.export_requested.emit(directory)

    def _reset(self):
        if (
            self.results.rowCount()
            and QMessageBox.question(
                self,
                "Liste zurücksetzen",
                "Die aktuelle Liste zurücksetzen? "
                "Die lokale Sitzungssicherung und bisherige Exporte bleiben erhalten.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.reset_requested.emit()

    def _open_image(self, row, _column):
        controller = self._controller
        if controller is not None and controller.store is not None:
            record = controller.tracker.results[row]
            if record.image:
                path = controller.store.directory / record.image
                if path.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
