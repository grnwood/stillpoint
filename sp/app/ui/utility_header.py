"""Compact, theme-aware identity headers for StillPoint utility panels."""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from .theme import theme_value


class UtilityPanelHeader(QFrame):
    """A restrained heading that identifies a rail utility without wasting space."""

    def __init__(
        self,
        title: str,
        detail: str = "",
        parent: Optional[QWidget] = None,
        *,
        accent_color: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("utilityPanelHeader")
        self.setFixedHeight(30)
        self._accent_color = accent_color
        self.header_layout = QHBoxLayout(self)
        self.header_layout.setContentsMargins(8, 3, 8, 3)
        self.header_layout.setSpacing(6)
        self.title_label = QLabel(title.upper(), self)
        self.title_label.setObjectName("utilityPanelTitle")
        self.header_layout.addWidget(self.title_label)
        self.detail_label = QLabel(self)
        self.detail_label.setObjectName("utilityPanelDetail")
        self.header_layout.addWidget(self.detail_label)
        self.header_layout.addStretch(1)
        self.set_detail(detail)
        self.apply_theme()

    def add_trailing_widget(self, widget: QWidget) -> None:
        self.header_layout.addWidget(widget)

    def set_detail(self, detail: str, tooltip: str = "") -> None:
        text = str(detail or "").strip()
        self.detail_label.setText(text)
        self.detail_label.setToolTip(tooltip or text)
        self.detail_label.setVisible(bool(text))

    def set_accent_color(self, color: Optional[str]) -> None:
        self._accent_color = (color or "").strip() or None
        self.apply_theme()

    def apply_theme(self) -> None:
        palette = QApplication.palette()
        accent = QColor(
            self._accent_color
            or str(theme_value("main_window.utility_header.accent", "#4f8f8b"))
        )
        if not accent.isValid():
            accent = palette.color(QPalette.Highlight)
        alternate = palette.color(QPalette.AlternateBase).name()
        base = palette.color(QPalette.Base).name()
        text = palette.color(QPalette.Text).name()
        border = palette.color(QPalette.Mid).name()
        self.setStyleSheet(
            "QFrame#utilityPanelHeader {"
            f"background: {alternate}; border-bottom: 1px solid {border};"
            "}"
            "QLabel#utilityPanelTitle {"
            f"color: {accent.name()}; font-size: 10px; font-weight: 700;"
            "}"
            "QLabel#utilityPanelDetail {"
            f"background: {base}; color: {text}; border: 1px solid {border};"
            "border-radius: 7px; padding: 1px 6px; font-size: 10px;"
            "}"
        )


class CompactToolbarIdentity(QFrame):
    """Small application/vault marker designed to live inside a toolbar."""

    def __init__(
        self,
        title: str,
        detail: str = "",
        parent: Optional[QWidget] = None,
        *,
        icon: Optional[QIcon] = None,
        accent_color: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("compactToolbarIdentity")
        self.setFixedHeight(28)
        self._accent_color = accent_color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(5)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setVisible(False)
        layout.addWidget(self.icon_label)
        self.title_label = QLabel(title.upper(), self)
        self.title_label.setObjectName("compactToolbarIdentityTitle")
        layout.addWidget(self.title_label)
        self.detail_label = QLabel(self)
        self.detail_label.setObjectName("compactToolbarIdentityDetail")
        layout.addWidget(self.detail_label)
        self.set_icon(icon or QIcon())
        self.set_detail(detail)
        self.apply_theme()

    def set_icon(self, icon: QIcon) -> None:
        visible = not icon.isNull()
        self.icon_label.setVisible(visible)
        if visible:
            self.icon_label.setPixmap(icon.pixmap(18, 18))

    def set_detail(self, detail: str, tooltip: str = "") -> None:
        text = str(detail or "").strip()
        self.detail_label.setText(f"·  {text}" if text else "")
        self.detail_label.setToolTip(tooltip or text)
        self.detail_label.setVisible(bool(text))

    def set_accent_color(self, color: Optional[str]) -> None:
        self._accent_color = (color or "").strip() or None
        self.apply_theme()

    def apply_theme(self) -> None:
        palette = QApplication.palette()
        accent = QColor(
            self._accent_color
            or str(theme_value("main_window.utility_header.accent", "#4f8f8b"))
        )
        if not accent.isValid():
            accent = palette.color(QPalette.Highlight)
        text = palette.color(QPalette.WindowText).name()
        self.setStyleSheet(
            "QFrame#compactToolbarIdentity {"
            f"background: transparent; border-left: 2px solid {accent.name()};"
            "}"
            "QLabel#compactToolbarIdentityTitle {"
            f"color: {accent.name()}; font-size: 10px; font-weight: 700;"
            "}"
            "QLabel#compactToolbarIdentityDetail {"
            f"color: {text}; font-size: 10px;"
            "}"
        )


def install_utility_header(
    panel: QWidget,
    title: str,
    detail: str = "",
    *,
    accent_color: Optional[str] = None,
) -> UtilityPanelHeader:
    """Insert one header into an existing box-layout panel."""
    existing = getattr(panel, "_utility_panel_header", None)
    if isinstance(existing, UtilityPanelHeader):
        existing.title_label.setText(title.upper())
        existing.set_detail(detail)
        existing.set_accent_color(accent_color)
        return existing
    layout = panel.layout()
    if not isinstance(layout, QBoxLayout):
        raise TypeError(f"{type(panel).__name__} does not use a box layout")
    if layout.direction() not in (
        QBoxLayout.Direction.TopToBottom,
        QBoxLayout.Direction.BottomToTop,
    ):
        raise TypeError(f"{type(panel).__name__} does not use a vertical root layout")
    header = UtilityPanelHeader(
        title,
        detail,
        panel,
        accent_color=accent_color,
    )
    layout.insertWidget(0, header)
    panel._utility_panel_header = header
    return header
