from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, QRectF, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from ..models import Rect, UiTarget


class _OverlayPanel(QWidget):
    def __init__(self, screen_rect: Rect, labels: list[tuple[int, UiTarget]]) -> None:
        super().__init__()
        self.screen_rect = screen_rect
        self.labels = labels
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(
            round(screen_rect.x),
            round(screen_rect.y),
            round(screen_rect.width),
            round(screen_rect.height),
        )

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        for number, target in self.labels:
            x = target.rect.x - self.screen_rect.x
            y = target.rect.y - self.screen_rect.y
            label = str(number)
            metrics = painter.fontMetrics()
            width = max(24, metrics.horizontalAdvance(label) + 12)
            rect = QRectF(x - width / 2, y - 12, width, 24)
            painter.setPen(QPen(QColor("#18232d"), 2))
            painter.setBrush(QColor(255, 210, 64, 245))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QColor("#18232d"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)


class NumberedTargetOverlay:
    """Transparent, voice-selected overlay; showing it never takes focus."""

    def __init__(self, app: QApplication | None = None) -> None:
        self.app = app or QApplication.instance()
        self.targets: dict[int, UiTarget] = {}
        self.panels: list[_OverlayPanel] = []
        self._signals = _OverlaySignals()
        self._signals.show_requested.connect(self._show_panels, Qt.ConnectionType.QueuedConnection)
        self._signals.hide_requested.connect(self._hide_panels, Qt.ConnectionType.QueuedConnection)

    def show_targets(self, targets: list[UiTarget]) -> None:
        self.targets = {index: target for index, target in enumerate(targets, start=1)}
        if self._on_gui_thread():
            self._show_panels(targets)
        else:
            self._signals.show_requested.emit(targets)

    def _show_panels(self, targets: list[UiTarget]) -> None:
        self._hide_panels()
        # Keep the latest mapping if several worker updates were queued.
        if list(self.targets.values()) != targets:
            return
        if self.app is None:
            return
        for screen in self.app.screens():
            geometry = screen.geometry()  # Qt gives device-independent coordinates.
            screen_rect = Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
            labels = [
                (number, target)
                for number, target in self.targets.items()
                if _rects_intersect(screen_rect, target.rect)
            ]
            if not labels:
                continue
            panel = _OverlayPanel(screen_rect, labels)
            panel.show()
            panel.raise_()
            self.panels.append(panel)

    def choose(self, number: int) -> UiTarget | None:
        target = self.targets.get(number)
        if target is not None:
            self.hide()
        return target

    def hide(self) -> None:
        self.targets.clear()
        if self._on_gui_thread():
            self._hide_panels()
        else:
            self._signals.hide_requested.emit()

    def _hide_panels(self) -> None:
        for panel in self.panels:
            panel.close()
        self.panels.clear()

    def _on_gui_thread(self) -> bool:
        return self.app is None or QThread.currentThread() is self.app.thread()


class _OverlaySignals(QObject):
    show_requested = pyqtSignal(list)
    hide_requested = pyqtSignal()


@dataclass(slots=True, frozen=True)
class GridCell:
    number: int
    rect: Rect


class RecursiveScreenGrid:
    """Deterministic inaccessible-canvas fallback with recursive 3×3 cells."""

    def __init__(self, rect: Rect, divisions: int = 3) -> None:
        if divisions < 2 or divisions > 9:
            raise ValueError("Grid divisions must be between 2 and 9.")
        self.root = rect
        self.current = rect
        self.divisions = divisions
        self.depth = 0

    def cells(self) -> list[GridCell]:
        width = self.current.width / self.divisions
        height = self.current.height / self.divisions
        result: list[GridCell] = []
        number = 1
        for row in range(self.divisions):
            for column in range(self.divisions):
                result.append(
                    GridCell(
                        number,
                        Rect(
                            self.current.x + column * width,
                            self.current.y + row * height,
                            width,
                            height,
                        ),
                    )
                )
                number += 1
        return result

    def descend(self, number: int) -> Rect:
        cell = next((item for item in self.cells() if item.number == number), None)
        if cell is None:
            raise ValueError(f"Grid cell must be between 1 and {self.divisions ** 2}.")
        self.current = cell.rect
        self.depth += 1
        return self.current

    def center(self) -> tuple[float, float]:
        return self.current.center

    def reset(self) -> None:
        self.current = self.root
        self.depth = 0


def _rects_intersect(left: Rect, right: Rect) -> bool:
    return not (
        right.x + right.width <= left.x
        or right.y + right.height <= left.y
        or right.x >= left.x + left.width
        or right.y >= left.y + left.height
    )
