"""Compact, theme-aware identity headers for StillPoint utility panels."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from .theme import chrome_colors, theme_value


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
        self.setFixedHeight(int(theme_value("ui.utility_header.height_px", 30)))
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
        colors = chrome_colors(self, self._accent_color)
        radius = int(theme_value("ui.chrome.radius_px", 4))
        self.setStyleSheet(
            "QFrame#utilityPanelHeader {"
            f"background: {colors['alternate']}; border-bottom: 1px solid {colors['border']};"
            "}"
            "QLabel#utilityPanelTitle {"
            f"color: {colors['accent']}; font-size: 10px; font-weight: 700;"
            "}"
            "QLabel#utilityPanelDetail {"
            f"background: {colors['base']}; color: {colors['text']}; "
            f"border: 1px solid {colors['border']}; border-radius: {radius}px; "
            "padding: 1px 6px; font-size: 10px;"
            "}"
        )


class CompactToolbarIdentity(QFrame):
    """Small application/vault marker designed to live inside a toolbar."""

    breadcrumbActivated = Signal(object)

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
        self.setFixedHeight(int(theme_value("ui.toolbar_identity.height_px", 28)))
        self._accent_color = accent_color
        self.identity_layout = QHBoxLayout(self)
        self.identity_layout.setContentsMargins(7, 2, 7, 2)
        self.identity_layout.setSpacing(5)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setVisible(False)
        self.identity_layout.addWidget(self.icon_label)
        self.title_label = QLabel(title.upper(), self)
        self.title_label.setObjectName("compactToolbarIdentityTitle")
        self.identity_layout.addWidget(self.title_label)
        self.detail_label = QLabel(self)
        self.detail_label.setObjectName("compactToolbarIdentityDetail")
        self.identity_layout.addWidget(self.detail_label)
        self._breadcrumb_widgets: list[QWidget] = []
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
        self.detail_label.setVisible(bool(text) and not self._breadcrumb_widgets)

    def clear_breadcrumb(self) -> None:
        for widget in self._breadcrumb_widgets:
            self.identity_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._breadcrumb_widgets.clear()
        self.detail_label.setVisible(bool(self.detail_label.text()))

    def set_breadcrumb(
        self,
        items: list[tuple[str, object, str]],
        *,
        max_segments: int = 4,
    ) -> None:
        """Show a compact clickable location path after the identity title.

        Each item is ``(label, target, tooltip)``. Long paths retain the root
        and the two nearest segments with a quiet ellipsis between them.
        """
        self.clear_breadcrumb()
        normalized = [
            (str(label or "").strip(), target, str(tooltip or "").strip())
            for label, target, tooltip in items
            if str(label or "").strip()
        ]
        if not normalized:
            return
        limit = max(2, int(max_segments))
        display: list[tuple[str, object, str] | None]
        if len(normalized) > limit:
            display = [normalized[0], None, *normalized[-(limit - 2):]]
        else:
            display = list(normalized)
        self.detail_label.hide()
        for item in display:
            separator = QLabel("›", self)
            separator.setObjectName("compactBreadcrumbSeparator")
            self.identity_layout.addWidget(separator)
            self._breadcrumb_widgets.append(separator)
            if item is None:
                ellipsis = QLabel("…", self)
                ellipsis.setObjectName("compactBreadcrumbEllipsis")
                self.identity_layout.addWidget(ellipsis)
                self._breadcrumb_widgets.append(ellipsis)
                continue
            label, target, tooltip = item
            button = QToolButton(self)
            button.setProperty("breadcrumbSegment", True)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tooltip or label)
            button.setAccessibleName(f"Open {label}")
            button.setText(
                button.fontMetrics().elidedText(
                    label,
                    Qt.TextElideMode.ElideMiddle,
                    int(theme_value("ui.breadcrumb.segment_max_width_px", 120)),
                )
            )
            button.clicked.connect(
                lambda checked=False, value=target: self.breadcrumbActivated.emit(value)
            )
            self.identity_layout.addWidget(button)
            self._breadcrumb_widgets.append(button)
        self.apply_theme()

    def set_accent_color(self, color: Optional[str]) -> None:
        self._accent_color = (color or "").strip() or None
        self.apply_theme()

    def apply_theme(self) -> None:
        colors = chrome_colors(self, self._accent_color)
        self.setStyleSheet(
            "QFrame#compactToolbarIdentity {"
            f"background: transparent; border-left: 2px solid {colors['accent']};"
            "}"
            "QLabel#compactToolbarIdentityTitle {"
            f"color: {colors['accent']}; font-size: 10px; font-weight: 700;"
            "}"
            "QLabel#compactToolbarIdentityDetail {"
            f"color: {colors['text']}; font-size: 10px;"
            "}"
            "QLabel#compactBreadcrumbSeparator, QLabel#compactBreadcrumbEllipsis {"
            f"color: {colors['muted']}; font-size: 11px;"
            "}"
            "QToolButton[breadcrumbSegment=\"true\"] {"
            f"color: {colors['text']}; background: transparent; border: 0; "
            "padding: 1px 3px; font-size: 10px;"
            "}"
            "QToolButton[breadcrumbSegment=\"true\"]:hover {"
            f"background: {colors['hover']}; border-radius: "
            f"{int(theme_value('ui.chrome.radius_px', 4))}px;"
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
