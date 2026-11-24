"""Persistent header and code-rail controls shared by diagram editor hosts."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class DiagramEditorHeader(QWidget):
    def __init__(self, editor_kind: str, scope: str, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.settings = QSettings("StillPoint", "StillPoint")
        scope_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
        self.prefix = f"diagramEditors/{editor_kind}/{scope_key}"
        self._splitter = None
        self._ai_panel = None
        self._vertical_splitter = None
        self._action_icons = []
        self._code_sizes = self.settings.value(f"{self.prefix}/codeRailSizes", [400, 600], type=list)
        self._ai_sizes = self.settings.value(f"{self.prefix}/aiPaneSizes", [640, 60], type=list)
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.header_toggle = QToolButton(self)
        self.header_toggle.setCheckable(True)
        self.header_toggle.setAccessibleName("Roll up diagram header and AI pane")
        self.header_toggle.setIconSize(QSize(18, 18))
        self.header_toggle.setChecked(self.settings.value(f"{self.prefix}/headerCollapsed", False, type=bool))
        self.header_toggle.toggled.connect(self._header_toggled)
        self.code_toggle = QToolButton(self)
        self.code_toggle.setCheckable(True)
        self.code_toggle.setAccessibleName("Hide diagram code rail")
        self.code_toggle.setIconSize(QSize(18, 18))
        self.code_toggle.setChecked(self.settings.value(f"{self.prefix}/codeRailHidden", False, type=bool))
        self.code_toggle.toggled.connect(self._code_toggled)
        layout.addWidget(self.header_toggle, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.content, 1)
        layout.addStretch()
        self._update_icons()

    def add_row(self, layout):
        self.content_layout.addLayout(layout)

    def add_action(self, button, asset):
        """Add an action that remains available with the header rolled up."""
        button.setIconSize(QSize(18, 18))
        self.layout().insertWidget(1, button, 0, Qt.AlignLeft | Qt.AlignTop)
        self._action_icons.append((button, asset))
        self._set_icon(button, asset)

    def add_preview_controls(self, layout):
        """Keep preview actions at the right end of the persistent top row."""
        self.layout().addLayout(layout)

    def wrap_code_surface(self, splitter):
        """Keep the code toggle beside the splitter, outside the header."""
        surface = QWidget()
        layout = QHBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.code_toggle, 0, Qt.AlignTop)
        layout.addWidget(splitter, 1)
        return surface

    def bind(self, splitter, ai_panel=None, vertical_splitter=None):
        self._splitter = splitter
        self._ai_panel = ai_panel
        self._vertical_splitter = vertical_splitter
        # Mermaid's external-browser preview has only a code surface locally.
        self.code_toggle.setEnabled(splitter.count() > 1)
        self.restore_visibility()

    def restore_visibility(self):
        self._apply_header_visibility()
        self._apply_code_visibility()

    def _header_toggled(self, collapsed):
        if collapsed and self._ai_panel is not None and not self._ai_panel.isHidden():
            self._ai_sizes = self._vertical_splitter.sizes()
            self.settings.setValue(f"{self.prefix}/aiPaneSizes", self._ai_sizes)
        self._apply_header_visibility()
        self.settings.setValue(f"{self.prefix}/headerCollapsed", collapsed)
        self.settings.sync()

    def _code_toggled(self, hidden):
        if self._splitter is None:
            return
        if hidden and not self._splitter.widget(0).isHidden():
            self._code_sizes = self._splitter.sizes()
            self.settings.setValue(f"{self.prefix}/codeRailSizes", self._code_sizes)
        self._apply_code_visibility()
        self.settings.setValue(f"{self.prefix}/codeRailHidden", hidden)
        self.settings.sync()

    def _apply_header_visibility(self):
        collapsed = self.header_toggle.isChecked()
        self.content.setVisible(not collapsed)
        if self._ai_panel is not None:
            was_hidden = self._ai_panel.isHidden()
            self._ai_panel.setVisible(not collapsed)
            if not collapsed and (was_hidden or self._vertical_splitter.sizes()[-1] == 0):
                self._vertical_splitter.setSizes([int(size) for size in self._ai_sizes])
        self.layout().invalidate()
        self.updateGeometry()
        self._update_icons()

    def _apply_code_visibility(self):
        if self._splitter is None:
            return
        code = self._splitter.widget(0)
        hidden = self.code_toggle.isChecked() and self.code_toggle.isEnabled()
        was_hidden = code.isHidden()
        code.setVisible(not hidden)
        if not hidden and (was_hidden or self._splitter.sizes()[0] == 0):
            self._splitter.setSizes([int(size) for size in self._code_sizes])
        self._update_icons()

    def _update_icons(self):
        for button, asset in self._action_icons:
            self._set_icon(button, asset)
        self._set_icon(self.header_toggle, "expand-horiz.svg" if self.header_toggle.isChecked() else "collapse-horiz.svg")
        self.header_toggle.setToolTip("Expand header and AI pane" if self.header_toggle.isChecked()
                                     else "Roll up header and AI pane")
        self._set_icon(self.code_toggle, "show-sidebar.svg" if self.code_toggle.isChecked() else "hide-sidebar.svg")
        self.code_toggle.setToolTip("Show code rail" if self.code_toggle.isChecked() else "Hide code rail")

    def _set_icon(self, button, asset):
        path = Path(__file__).resolve().parents[2] / "assets" / asset
        pixmap = QIcon(str(path)).pixmap(button.iconSize())
        if pixmap.isNull():
            return
        tinted = QPixmap(pixmap.size())
        tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
        tinted.fill(Qt.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), self.palette().color(QPalette.ButtonText))
        painter.end()
        button.setIcon(QIcon(tinted))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange and hasattr(self, "code_toggle"):
            self._update_icons()
