from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QKeyEvent, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_REORG_MIME = "application/x-stillpoint-reorg-paths"
_PATH_ROLE = Qt.ItemDataRole.UserRole
_GHOST_ROLE = Qt.ItemDataRole.UserRole + 1
_JOURNAL_DAY_RE = re.compile(r"^/Journal/\d{4}/\d{1,2}/\d{1,2}$", re.IGNORECASE)


class _CandidateList(QListWidget):
    def _build_drag_pixmap(self, paths: list[str]) -> QPixmap:
        first_name = Path(paths[0].rstrip("/")).name if paths else "Candidate"
        label = first_name if len(paths) == 1 else f"{first_name} + {len(paths) - 1} more"
        pixmap = QPixmap(280, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor(self.palette().color(QPalette.ColorRole.Highlight))
        background.setAlpha(235)
        painter.setBrush(background)
        painter.setPen(self.palette().color(QPalette.ColorRole.HighlightedText))
        painter.drawRoundedRect(1, 1, 278, 46, 7, 7)
        painter.drawText(
            pixmap.rect().adjusted(12, 0, -12, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"Dragging: {label}",
        )
        painter.end()
        return pixmap

    def startDrag(self, supported_actions) -> None:  # type: ignore[override]
        paths = [str(item.data(_PATH_ROLE) or "") for item in self.selectedItems()]
        paths = [path for path in paths if path]
        if not paths:
            return
        mime = QMimeData()
        mime.setData(_REORG_MIME, json.dumps(paths).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._build_drag_pixmap(paths))
        drag.setHotSpot(QPoint(18, 18))
        drag.exec(Qt.DropAction.CopyAction)


class _DestinationTree(QTreeWidget):
    pathsDropped = Signal(list, str)
    dragTargetChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDropIndicatorShown(True)
        self._drag_target_item: Optional[QTreeWidgetItem] = None
        self._pre_drag_current: Optional[QTreeWidgetItem] = None
        self._pre_drag_selected: list[QTreeWidgetItem] = []

    @staticmethod
    def _real_drop_target(item: Optional[QTreeWidgetItem]) -> Optional[QTreeWidgetItem]:
        if item is not None and item.data(0, _GHOST_ROLE):
            return item.parent()
        return item

    def _show_drag_target(self, item: Optional[QTreeWidgetItem]) -> None:
        target = self._real_drop_target(item)
        if target is None and self.topLevelItemCount():
            target = self.topLevelItem(0)
        if target is self._drag_target_item:
            return
        self._drag_target_item = target
        if target is None:
            return
        self.clearSelection()
        target.setSelected(True)
        self.setCurrentItem(target)
        self.scrollToItem(target, QAbstractItemView.ScrollHint.EnsureVisible)
        self.dragTargetChanged.emit(str(target.data(0, _PATH_ROLE) or "/"))

    def _finish_drag_target(self, *, restore_previous: bool) -> None:
        self._drag_target_item = None
        if restore_previous:
            self.clearSelection()
            for item in self._pre_drag_selected:
                item.setSelected(True)
            if self._pre_drag_current is not None:
                self.setCurrentItem(self._pre_drag_current)
        self._pre_drag_current = None
        self._pre_drag_selected = []
        self.dragTargetChanged.emit("")

    def startDrag(self, supported_actions) -> None:  # type: ignore[override]
        paths = [
            str(item.data(0, _PATH_ROLE) or "")
            for item in self.selectedItems()
            if not item.data(0, _GHOST_ROLE)
        ]
        paths = [path for path in paths if path and path != "/"]
        if not paths:
            return
        mime = QMimeData()
        mime.setData(_REORG_MIME, json.dumps(paths).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(_REORG_MIME):
            self._pre_drag_current = self.currentItem()
            self._pre_drag_selected = list(self.selectedItems())
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(_REORG_MIME):
            self._show_drag_target(self.itemAt(event.position().toPoint()))
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._finish_drag_target(restore_previous=True)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if not event.mimeData().hasFormat(_REORG_MIME):
            super().dropEvent(event)
            return
        target = self._real_drop_target(self.itemAt(event.position().toPoint()))
        if target is None and self.topLevelItemCount():
            target = self.topLevelItem(0)
        target_path = str(target.data(0, _PATH_ROLE) or "") if target else "/"
        if target is not None:
            self.clearSelection()
            target.setSelected(True)
            self.setCurrentItem(target)
        try:
            paths = json.loads(bytes(event.mimeData().data(_REORG_MIME)).decode("utf-8"))
        except Exception:
            paths = []
        if isinstance(paths, list) and target_path:
            self.pathsDropped.emit([str(path) for path in paths if path], target_path)
            self._finish_drag_target(restore_previous=False)
            event.acceptProposedAction()
            return
        self._finish_drag_target(restore_previous=True)
        event.ignore()


class VaultReorgWindow(QDialog):
    reorganizationCommitted = Signal(dict)

    def __init__(
        self,
        *,
        http_client,
        vault_name: str,
        read_only: bool,
        vault_accent_color: Optional[str] = None,
        before_commit: Optional[Callable[[dict[str, Any]], bool]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Reorganize Vault — {vault_name or 'Vault'}")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(1280, 720)
        self.http = http_client
        self.read_only = bool(read_only)
        self._vault_accent_color = (vault_accent_color or "").strip() or None
        self.before_commit = before_commit
        self._tree_payload: list[dict[str, Any]] = []
        self._tree_version = 0
        self._plan: list[dict[str, Any]] = []
        self._preflight: Optional[dict[str, Any]] = None
        self._updating_table = False
        self._destination_expanded_paths: set[str] = {"/"}
        self._selected_destination_path = "/"
        self._candidate_reference_notes: dict[str, str] = {}
        self._initial_focus_applied = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        header_row = QHBoxLayout()
        header_row.addStretch(1)
        self.help_btn = QPushButton("?  Help")
        self.help_btn.setToolTip("Learn how Vault Reorganization works")
        self.help_btn.clicked.connect(self._show_help)
        header_row.addWidget(self.help_btn)
        root.addLayout(header_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_candidate_pane())
        splitter.addWidget(self._build_destination_pane())
        splitter.addWidget(self._build_plan_pane())
        self._focus_panes = [self.candidate_pane, self.destination_pane, self.plan_pane]
        self._active_focus_pane: Optional[QFrame] = None
        self._setup_focus_panes()
        splitter.setSizes([330, 470, 480])
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.keyboard_hint_label = QLabel("Ctrl+← / Ctrl+→ switch panes")
        self.keyboard_hint_label.setToolTip(
            "Move keyboard focus between Candidates, Destination hierarchy, and Staged changes"
        )
        bottom.addWidget(self.keyboard_hint_label)
        bottom.addSpacing(12)
        self.summary_label = QLabel("No changes staged")
        bottom.addWidget(self.summary_label, 1)
        self.apply_btn = QPushButton("Apply Reorganization")
        self.apply_btn.clicked.connect(self._apply_plan)
        bottom.addWidget(self.apply_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        # QDialog otherwise promotes the first auto-default button (currently
        # Help), causing Enter in a search field to activate it.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._run_search)
        self.search_edit.textChanged.connect(lambda _text: self.search_timer.start())
        self.content_checkbox.toggled.connect(lambda _checked: self._run_search())
        self.journal_checkbox.toggled.connect(lambda _checked: self._run_search())
        self.destination_search_edit.textChanged.connect(lambda _text: self._rebuild_tree_preview())
        self.destination_staged_only.toggled.connect(lambda _checked: self._rebuild_tree_preview())
        self.destination_tree.pathsDropped.connect(self._stage_paths)
        self.destination_tree.dragTargetChanged.connect(self._update_drag_target_hint)
        self._load_tree()
        self._refresh_plan_view()

    def _build_candidate_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("reorgCandidatePane")
        self.candidate_pane = pane
        layout = QVBoxLayout(pane)
        layout.addWidget(QLabel("<b>Candidates</b>"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find pages by title or path…")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        filters = QHBoxLayout()
        self.content_checkbox = QCheckBox("Content matches")
        self.journal_checkbox = QCheckBox("Journal pages only")
        filters.addWidget(self.content_checkbox)
        filters.addWidget(self.journal_checkbox)
        layout.addLayout(filters)
        self.result_label = QLabel("Enter a search term")
        layout.addWidget(self.result_label)
        self.candidate_list = _CandidateList()
        self.candidate_list.setAlternatingRowColors(True)
        self._apply_candidate_list_style()
        self.candidate_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.candidate_list.setDragEnabled(True)
        layout.addWidget(self.candidate_list, 1)
        self.stage_btn = QPushButton("Stage Selected →")
        self.stage_btn.clicked.connect(self._stage_selected_candidates)
        layout.addWidget(self.stage_btn)
        return pane

    def _apply_candidate_list_style(self) -> None:
        """Give multi-line candidate rows visible contrast in every palette."""
        palette = self.candidate_list.palette()
        base = QColor(palette.color(QPalette.ColorRole.Base))
        if not base.isValid():
            base = QColor("#202328")
        alternate = base.darker(106) if base.lightness() >= 128 else base.lighter(130)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
        self._candidate_alternate_color = alternate.name()
        self.candidate_list.setStyleSheet(
            f"""
            QListWidget::item {{ padding: 2px 3px; }}
            QListWidget::item:alternate {{ background-color: {alternate.name()}; }}
            QListWidget::item:selected {{
                background-color: {highlight.name()};
                color: {highlighted_text.name()};
            }}
            """
        )

    def _build_destination_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("reorgDestinationPane")
        self.destination_pane = pane
        layout = QVBoxLayout(pane)
        layout.addWidget(QLabel("<b>Destination hierarchy</b>"))
        self.destination_search_edit = QLineEdit()
        self.destination_search_edit.setPlaceholderText("Filter destinations by name or path…")
        self.destination_search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.destination_search_edit)
        self.destination_staged_only = QCheckBox("Show staged paths only")
        layout.addWidget(self.destination_staged_only)
        self.destination_tree = _DestinationTree()
        self.destination_tree.setHeaderLabel("Complete vault (Journal included)")
        layout.addWidget(self.destination_tree, 1)
        self.destination_hint_label = QLabel("Drag search results or tree pages onto a destination parent.")
        self.destination_hint_label.setWordWrap(True)
        layout.addWidget(self.destination_hint_label)
        return pane

    def _build_plan_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("reorgPlanPane")
        self.plan_pane = pane
        layout = QVBoxLayout(pane)
        layout.addWidget(QLabel("<b>Staged changes</b>"))
        self.plan_error_label = QLabel()
        self.plan_error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.plan_error_label.setWordWrap(True)
        self.plan_error_label.setAccessibleName("Plan validation status")
        self.plan_error_label.setStyleSheet(
            "background-color: #5f2020; color: #ffe4e4; border: 1px solid #a84343; "
            "border-radius: 4px; padding: 6px; font-weight: 600;"
        )
        self.plan_error_label.hide()
        layout.addWidget(self.plan_error_label)
        self.plan_table = QTableWidget(0, 6)
        self.plan_table.setHorizontalHeaderLabels(
            ["Action", "Source", "Destination", "Name / Ref", "Journal", "Status"]
        )
        header = self.plan_table.horizontalHeader()
        header.setMinimumSectionSize(44)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(3, 82)
        header.resizeSection(4, 78)
        header.resizeSection(5, 72)
        header.setStretchLastSection(False)
        self.plan_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.plan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plan_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.plan_table.itemChanged.connect(self._plan_item_changed)
        layout.addWidget(self.plan_table, 1)
        buttons = QHBoxLayout()
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected_rows)
        buttons.addWidget(remove_btn)
        clear_btn = QPushButton("Clear Plan")
        clear_btn.clicked.connect(self._clear_plan)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        self.validate_btn = QPushButton("Validate")
        self.validate_btn.clicked.connect(self._validate_plan)
        buttons.addWidget(self.validate_btn)
        layout.addLayout(buttons)
        return pane

    def _setup_focus_panes(self) -> None:
        for pane in self._focus_panes:
            pane.installEventFilter(self)
            for child in pane.findChildren(QWidget):
                child.installEventFilter(self)
        self.set_vault_accent_color(self._vault_accent_color)

    def set_vault_accent_color(self, color_hex: Optional[str]) -> None:
        """Apply the accent already resolved by the owning vault window."""
        accent = QColor((color_hex or "").strip())
        if not accent.isValid():
            accent = self.palette().color(QPalette.ColorRole.Highlight)
        self._vault_accent_color = accent.name()
        self._focus_accent_color = accent.name()
        if hasattr(self, "_focus_panes"):
            self._apply_focus_pane_styles()
        self._update_staged_ghost_colors()
        if hasattr(self, "help_btn"):
            text_color = "#111111" if accent.lightness() >= 150 else "#ffffff"
            self.help_btn.setStyleSheet(
                f"QPushButton {{ background-color: {accent.name()}; color: {text_color}; "
                f"border: 1px solid {accent.lighter(125).name()}; border-radius: 5px; "
                "padding: 5px 12px; font-weight: 700; }}"
                f"QPushButton:hover {{ background-color: {accent.lighter(115).name()}; }}"
            )

    def _update_staged_ghost_colors(self) -> None:
        tree = getattr(self, "destination_tree", None)
        accent = QColor(getattr(self, "_focus_accent_color", ""))
        if tree is None or not accent.isValid():
            return
        root = tree.invisibleRootItem()
        stack = [root.child(index) for index in range(root.childCount())]
        while stack:
            item = stack.pop()
            if item.data(0, _GHOST_ROLE):
                item.setForeground(0, QBrush(accent))
            stack.extend(item.child(index) for index in range(item.childCount()))

    def _update_drag_target_hint(self, path: str) -> None:
        if path:
            self.destination_hint_label.setText(f"Drop target: {path}")
            self.destination_hint_label.setStyleSheet(
                f"color: {self._focus_accent_color}; font-weight: 700;"
            )
        else:
            self.destination_hint_label.setText(
                "Drag search results or tree pages onto a destination parent."
            )
            self.destination_hint_label.setStyleSheet("")

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "About Vault Reorganization",
            "Vault Reorganization helps you find pages by title, path, or indexed content and rehome "
            "them in a more useful part of your vault.\n\n"
            "Search for an idea or topic, choose a destination, and stage one or more changes. You can "
            "adjust names and destinations before validating the complete plan. Nothing moves until "
            "you select Apply Reorganization.\n\n"
            "Journal history is durable:\n"
            "• A Journal day page stays in the Journal. Reorganizing it adds a reference from the "
            "selected topic page back to that day.\n"
            "• Pages created underneath a Journal day can be rehomed freely. StillPoint preserves the "
            "history by adding or updating a link on the original day page.\n\n"
            "This lets you collect ideas during daily work, then organize them into topical areas "
            "without losing when and where the work began.",
        )

    def _pane_for_widget(self, widget: Optional[QWidget]) -> Optional[QFrame]:
        current = widget
        while current is not None:
            if current in self._focus_panes:
                return current
            current = current.parentWidget()
        return None

    def _sync_focus_pane(self) -> None:
        self._active_focus_pane = self._pane_for_widget(QApplication.focusWidget())
        self._apply_focus_pane_styles()

    def _apply_focus_pane_styles(self) -> None:
        inactive = self.palette().color(QPalette.ColorRole.Mid).name()
        for pane in self._focus_panes:
            border = self._focus_accent_color if pane is self._active_focus_pane else inactive
            pane.setStyleSheet(
                f"QFrame#{pane.objectName()} {{ border: 2px solid {border}; border-radius: 5px; }}"
            )

    def _focus_detail_area(self, widget: QWidget) -> None:
        if widget is self.candidate_list and self.candidate_list.count() and self.candidate_list.currentRow() < 0:
            self.candidate_list.setCurrentRow(0)
        elif widget is self.destination_tree and self.destination_tree.currentItem() is None:
            if self.destination_tree.topLevelItemCount():
                self.destination_tree.setCurrentItem(self.destination_tree.topLevelItem(0))
        elif widget is self.plan_table and self.plan_table.rowCount() and self.plan_table.currentRow() < 0:
            self.plan_table.setCurrentCell(0, 0)
            self.plan_table.selectRow(0)
        widget.setFocus(Qt.FocusReason.TabFocusReason)

    def _cycle_detail_area(self, current: QWidget, direction: int) -> bool:
        detail_areas: list[QWidget] = [
            self.candidate_list,
            self.destination_tree,
            self.plan_table,
        ]
        pane = self._pane_for_widget(current)
        if pane not in self._focus_panes:
            return False
        index = self._focus_panes.index(pane)
        self._focus_detail_area(detail_areas[(index + direction) % len(detail_areas)])
        return True

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if modifiers == Qt.KeyboardModifier.NoModifier:
                if watched is self.search_edit and key == Qt.Key.Key_Down:
                    self._focus_detail_area(self.candidate_list)
                    event.accept()
                    return True
                if (
                    watched is self.candidate_list
                    and key == Qt.Key.Key_Up
                    and self.candidate_list.currentRow() <= 0
                ):
                    self.search_edit.setFocus(Qt.FocusReason.BacktabFocusReason)
                    event.accept()
                    return True
                if watched is self.candidate_list and key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                    self._stage_selected_candidates()
                    event.accept()
                    return True
            elif modifiers == Qt.KeyboardModifier.ControlModifier and isinstance(watched, QWidget):
                if key == Qt.Key.Key_Right and self._cycle_detail_area(watched, 1):
                    event.accept()
                    return True
                if key == Qt.Key.Key_Left and self._cycle_detail_area(watched, -1):
                    event.accept()
                    return True
        if event.type() == QEvent.Type.FocusIn:
            pane = self._pane_for_widget(watched if isinstance(watched, QWidget) else None)
            if pane is not None and pane is not self._active_focus_pane:
                self._active_focus_pane = pane
                self._apply_focus_pane_styles()
        elif event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._sync_focus_pane)
        return super().eventFilter(watched, event)

    def has_staged_changes(self) -> bool:
        return bool(self._plan)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._initial_focus_applied:
            return
        self._initial_focus_applied = True
        QTimer.singleShot(
            0,
            lambda: self.search_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason),
        )

    def _load_tree(self) -> None:
        try:
            response = self.http.get("/api/vault/tree", params={"include_journal": "true"})
            response.raise_for_status()
            payload = response.json()
            self._tree_payload = list(payload.get("tree") or [])
            self._tree_version = int(payload.get("version") or 0)
        except Exception as exc:
            self._tree_payload = []
            self.result_label.setText(f"Unable to load vault hierarchy: {exc}")
        self._rebuild_tree_preview()

    def _rebuild_tree_preview(self) -> None:
        self._capture_destination_tree_state()
        self.destination_tree.clear()
        root_item = QTreeWidgetItem(["Vault root"])
        root_item.setData(0, _PATH_ROLE, "/")
        self.destination_tree.addTopLevelItem(root_item)

        filter_terms = [
            term.casefold()
            for term in self.destination_search_edit.text().split()
            if term.strip()
        ]
        staged_only = self.destination_staged_only.isChecked()
        staged_paths = {
            path
            for op in self._plan
            for path in (op["source_path"], op["destination_parent"])
            if path
        }

        def staged_context(path: str) -> bool:
            if not staged_only:
                return True
            if path == "/":
                return bool(staged_paths)
            prefix = path.rstrip("/") + "/"
            return any(target == path or target.startswith(prefix) for target in staged_paths)

        def make_node(node: dict[str, Any], search_ancestor_matches: bool = False) -> Optional[QTreeWidgetItem]:
            path = str(node.get("path") or "")
            name = str(node.get("name") or Path(path.rstrip("/")).name or path)
            search_text = f"{name} {path}".casefold()
            direct_search_match = not filter_terms or all(term in search_text for term in filter_terms)
            search_matches = search_ancestor_matches or direct_search_match
            children = [
                child
                for child_node in list(node.get("children") or [])
                if (child := make_node(child_node, search_matches)) is not None
            ]
            if not (search_matches and staged_context(path)) and not children:
                return None
            item = QTreeWidgetItem([name])
            item.setData(0, _PATH_ROLE, path)
            item.addChildren(children)
            return item

        def add_nodes(parent: QTreeWidgetItem, nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                path = str(node.get("path") or "")
                if path == "/":
                    add_nodes(parent, list(node.get("children") or []))
                    continue
                item = make_node(node)
                if item is not None:
                    parent.addChild(item)

        add_nodes(root_item, self._tree_payload)
        source_paths = {
            op["source_path"]
            for op in self._plan
            if op.get("operation_type", "move") == "move"
        }

        def walk(item: QTreeWidgetItem) -> None:
            if str(item.data(0, _PATH_ROLE) or "") in source_paths:
                font = item.font(0)
                font.setStrikeOut(True)
                item.setFont(0, font)
            for idx in range(item.childCount()):
                walk(item.child(idx))

        walk(root_item)
        for op in self._plan:
            parent_item = self._find_tree_item(op["destination_parent"])
            if parent_item is None:
                continue
            if op.get("operation_type", "move") == "add_reference":
                ghost_text = f"Journal reference: {op['new_name']}  (staged)"
            else:
                ghost_text = f"{op['new_name']}  (staged)"
            ghost = QTreeWidgetItem([ghost_text])
            ghost.setData(0, _GHOST_ROLE, True)
            ghost.setData(0, _PATH_ROLE, op["destination_parent"])
            font = ghost.font(0)
            font.setItalic(True)
            ghost.setFont(0, font)
            accent = QColor(getattr(self, "_focus_accent_color", ""))
            if accent.isValid():
                ghost.setForeground(0, QBrush(accent))
            parent_item.addChild(ghost)
            parent_item.setExpanded(True)
            self._destination_expanded_paths.add(op["destination_parent"])
        root_item.setExpanded(True)
        for path in self._destination_expanded_paths:
            item = self._find_tree_item(path)
            if item is not None:
                item.setExpanded(True)
        selected = self._find_tree_item(self._selected_destination_path)
        if selected is not None:
            self.destination_tree.setCurrentItem(selected)
            selected.setSelected(True)

    def _capture_destination_tree_state(self) -> None:
        tree = getattr(self, "destination_tree", None)
        if tree is None:
            return
        root = tree.invisibleRootItem()
        stack = [root.child(index) for index in range(root.childCount())]
        while stack:
            item = stack.pop()
            if not item.data(0, _GHOST_ROLE):
                path = str(item.data(0, _PATH_ROLE) or "")
                if path:
                    if item.isExpanded():
                        self._destination_expanded_paths.add(path)
                    else:
                        self._destination_expanded_paths.discard(path)
            stack.extend(item.child(index) for index in range(item.childCount()))
        current = tree.currentItem()
        if current is not None:
            if current.data(0, _GHOST_ROLE):
                current = current.parent()
            if current is not None:
                path = str(current.data(0, _PATH_ROLE) or "")
                if path:
                    self._selected_destination_path = path

    def _find_tree_item(self, path: str) -> Optional[QTreeWidgetItem]:
        iterator = self.destination_tree.invisibleRootItem()
        stack = [iterator.child(index) for index in range(iterator.childCount())]
        while stack:
            item = stack.pop()
            if str(item.data(0, _PATH_ROLE) or "") == path and not item.data(0, _GHOST_ROLE):
                return item
            stack.extend(item.child(index) for index in range(item.childCount()))
        return None

    def _run_search(self) -> None:
        query = self.search_edit.text().strip()
        self.candidate_list.clear()
        self._candidate_reference_notes.clear()
        if not query:
            self.result_label.setText("Enter a search term")
            return
        try:
            response = self.http.get(
                "/api/vault/reorganize/candidates",
                params={
                    "q": query,
                    "include_content": str(self.content_checkbox.isChecked()).lower(),
                    "journal_only": str(self.journal_checkbox.isChecked()).lower(),
                    "limit": 200,
                },
            )
            response.raise_for_status()
            payload = response.json()
            results = list(payload.get("results") or [])
        except Exception as exc:
            self.result_label.setText(f"Search failed: {exc}")
            return
        for result in results:
            title = str(result.get("title") or Path(str(result.get("folder_path") or "")).name)
            path = str(result.get("folder_path") or "")
            suffix = f"\n{result.get('snippet')}" if result.get("match_type") == "content" else ""
            operation_type = str(result.get("operation_type") or "move")
            prefix = "[Journal entry — add reference]\n" if operation_type == "add_reference" else ""
            matched_heading = str(result.get("matched_heading") or "").strip()
            if operation_type == "add_reference" and matched_heading:
                self._candidate_reference_notes[path] = matched_heading
                prefix += f"Matched heading: {matched_heading}\n"
            item = QListWidgetItem(f"{prefix}{title}\n{path}{suffix}")
            item.setData(_PATH_ROLE, path)
            tooltip_parts = [
                f"<b>{html.escape(title)}</b>",
                f"<code>{html.escape(path)}</code>",
            ]
            if matched_heading:
                tooltip_parts.append(f"<b>Matched heading:</b> {html.escape(matched_heading)}")
            snippet = str(result.get("snippet") or "").strip()
            if snippet:
                tooltip_parts.append(
                    "<b>Matching content:</b><br>"
                    + html.escape(snippet[:1200]).replace("\n", "<br>")
                )
            item.setToolTip("<br><br>".join(tooltip_parts))
            self.candidate_list.addItem(item)
        message = f"{len(results)} result(s)"
        if self.content_checkbox.isChecked() and not payload.get("content_index_available"):
            message += " — content index unavailable; showing title/path matches"
        self.result_label.setText(message)

    def _clear_candidate_search(self) -> None:
        self.search_timer.stop()
        self.search_edit.clear()
        # clear() emits textChanged and starts the debounce timer; this reset is
        # immediate and should not leave a redundant callback queued.
        self.search_timer.stop()
        self._run_search()
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self._clear_candidate_search()
            event.accept()
            return
        super().keyPressEvent(event)

    def _stage_selected_candidates(self) -> None:
        target = self.destination_tree.currentItem()
        target_path = str(target.data(0, _PATH_ROLE) or "") if target else ""
        selected = list(self.candidate_list.selectedItems())
        if not selected and self.candidate_list.currentItem() is not None:
            selected = [self.candidate_list.currentItem()]
        paths = [str(item.data(_PATH_ROLE) or "") for item in selected]
        if not target_path:
            QMessageBox.information(self, "Choose Destination", "Select a destination parent first.")
            return
        self._stage_paths(paths, target_path)

    def _stage_paths(self, paths: list[str], destination_parent: str) -> None:
        if self.read_only:
            QMessageBox.information(self, "Read-Only", "Reorganization is disabled while the vault is read-only.")
            return
        changed = False
        by_source = {op["source_path"]: op for op in self._plan}
        for raw_path in paths:
            source = str(raw_path or "").rstrip("/") or "/"
            operation_type = "add_reference" if _JOURNAL_DAY_RE.fullmatch(source) else "move"
            if source == "/":
                continue
            if operation_type == "move" and (
                destination_parent == source or destination_parent.startswith(source + "/")
            ):
                continue
            if operation_type == "add_reference" and destination_parent in {"/", source}:
                QMessageBox.information(
                    self,
                    "Choose Topic Page",
                    "Select an existing topic page as the destination for this Journal reference.",
                )
                continue
            existing = by_source.get(source)
            if existing:
                existing["destination_parent"] = destination_parent
                existing["operation_type"] = operation_type
            else:
                default_name = Path(source).name
                if operation_type == "add_reference":
                    default_name = (
                        self._candidate_reference_notes.get(source)
                        or self.search_edit.text().strip()
                        or default_name
                    )
                operation = {
                    "operation_type": operation_type,
                    "source_path": source,
                    "destination_parent": destination_parent,
                    "new_name": default_name,
                    "journal_reference_action": "none",
                    "status": "Not validated",
                }
                self._plan.append(operation)
                by_source[source] = operation
            changed = True
        if changed:
            self._invalidate_preflight()
            self._refresh_plan_view()

    def _request_operations(self) -> list[dict[str, str]]:
        return [
            {
                "from": op["source_path"],
                "destination_parent": op["destination_parent"],
                "new_name": op["new_name"],
                "operation_type": op.get("operation_type", "move"),
            }
            for op in self._plan
        ]

    def _invalidate_preflight(self) -> None:
        self._preflight = None
        for op in self._plan:
            op["status"] = "Not validated"
            op["journal_reference_action"] = "none"

    def _refresh_plan_view(self) -> None:
        self._updating_table = True
        try:
            self.plan_table.setRowCount(len(self._plan))
            for row, op in enumerate(self._plan):
                values = [
                    "Add reference" if op.get("operation_type", "move") == "add_reference" else "Move",
                    op["source_path"],
                    op["destination_parent"],
                    op["new_name"],
                    {
                        "append": "Will add to # Moved Pages",
                        "rewrite_existing": "Existing link will update",
                        "add_reference": "Will add to # Journal References",
                    }.get(op.get("journal_reference_action"), ""),
                    op.get("status", "Not validated"),
                ]
                tooltip_labels = ["Action", "Source path", "Destination path", "Name / Ref", "Journal", "Status"]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if column != 3:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if value:
                        label = tooltip_labels[column]
                        escaped_value = html.escape(str(value)).replace("\n", "<br>")
                        value_markup = f"<code>{escaped_value}</code>" if column in {1, 2} else escaped_value
                        item.setToolTip(
                            f"<b>{label}</b><br>{value_markup}"
                        )
                    self.plan_table.setItem(row, column, item)
        finally:
            self._updating_table = False
        append_count = sum(op.get("journal_reference_action") == "append" for op in self._plan)
        reference_count = sum(op.get("operation_type", "move") == "add_reference" for op in self._plan)
        summary = f"{len(self._plan)} page(s) staged"
        if append_count:
            summary += f"; {append_count} Journal reference(s) will be added"
        if reference_count:
            summary += f"; {reference_count} Journal entry reference(s) staged"
        has_plan = bool(self._plan)
        validated_ok = bool(self._preflight and self._preflight.get("ok"))
        validation_failed = bool(self._preflight is not None and not self._preflight.get("ok"))
        if self.read_only:
            summary = "Read-only vault — validation and apply are unavailable"
        elif not has_plan:
            summary = "No changes staged — stage a change to enable validation"
        elif validated_ok:
            summary += "; validated and ready to apply"
        elif validation_failed:
            summary += "; resolve validation errors, then validate again"
        else:
            summary += "; validation required"
        self.summary_label.setText(summary)
        self.validate_btn.setEnabled(has_plan and not self.read_only)
        self.apply_btn.setEnabled(has_plan and validated_ok and not self.read_only)
        if not has_plan or self.read_only:
            validate_state = "blocked"
            validate_tip = (
                "Validation is unavailable while the vault is read-only."
                if self.read_only
                else "Stage at least one change before validating."
            )
        elif validated_ok:
            validate_state = "neutral"
            validate_tip = "The plan is valid. You may validate again if the vault changed."
        else:
            validate_state = "ready"
            validate_tip = "Next step: validate the staged changes."
        if self.apply_btn.isEnabled():
            apply_state = "ready"
            apply_tip = "Next step: apply the validated reorganization."
        else:
            apply_state = "blocked"
            apply_tip = (
                "Apply is unavailable while the vault is read-only."
                if self.read_only
                else "Validate the staged plan successfully before applying it."
            )
        self._set_workflow_button_state(self.validate_btn, validate_state, validate_tip)
        self._set_workflow_button_state(self.apply_btn, apply_state, apply_tip)
        self._update_plan_error_label()
        self.stage_btn.setEnabled(not self.read_only)
        self._rebuild_tree_preview()

    def _update_plan_error_label(self) -> None:
        if self._preflight is None or self._preflight.get("ok"):
            self.plan_error_label.clear()
            self.plan_error_label.hide()
            return
        general_errors = [
            str(error.get("message") or "Invalid plan")
            for error in self._preflight.get("errors") or []
            if not isinstance(error.get("row"), int)
        ]
        if general_errors:
            message = "Plan blocked:\n" + "\n".join(f"• {error}" for error in general_errors)
        else:
            message = "Plan blocked — resolve the row errors shown in the Status column."
        self.plan_error_label.setText(message)
        self.plan_error_label.show()

    @staticmethod
    def _set_workflow_button_state(button: QPushButton, state: str, tooltip: str) -> None:
        button.setProperty("workflowState", state)
        button.setToolTip(tooltip)
        if state == "ready":
            button.setStyleSheet(
                "QPushButton { background-color: #1f7a3f; color: white; border: 1px solid #39a85c; "
                "border-radius: 4px; padding: 5px 10px; font-weight: 600; }"
                "QPushButton:hover { background-color: #278f4b; }"
                "QPushButton:pressed { background-color: #176031; }"
            )
        elif state == "blocked":
            button.setStyleSheet(
                "QPushButton, QPushButton:disabled { background-color: #7f2525; color: #f7dddd; "
                "border: 1px solid #a84343; border-radius: 4px; padding: 5px 10px; }"
            )
        else:
            button.setStyleSheet("")

    def _plan_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table or item.column() != 3 or not (0 <= item.row() < len(self._plan)):
            return
        self._plan[item.row()]["new_name"] = item.text().strip()
        self._invalidate_preflight()
        self._refresh_plan_view()

    def _remove_selected_rows(self) -> None:
        rows = sorted({index.row() for index in self.plan_table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._plan):
                self._plan.pop(row)
        if rows:
            self._invalidate_preflight()
            self._refresh_plan_view()

    def _clear_plan(self) -> None:
        self._plan.clear()
        self._invalidate_preflight()
        self._refresh_plan_view()
        self._refresh_workspace_data()

    def _refresh_workspace_data(self) -> None:
        """Reload hierarchy and rerun the active query after structural work."""
        try:
            self.search_timer.stop()
        except Exception:
            pass
        self._load_tree()
        self._run_search()

    def _validate_plan(self) -> bool:
        if not self._plan:
            return False
        try:
            response = self.http.post(
                "/api/vault/reorganize/preflight",
                json={"operations": self._request_operations(), "tree_version": self._tree_version},
            )
            response.raise_for_status()
            self._preflight = response.json()
        except Exception as exc:
            QMessageBox.critical(self, "Validation Failed", str(exc))
            self._preflight = None
            self._refresh_plan_view()
            return False
        validated_version = int(self._preflight.get("tree_version") or self._tree_version)
        tree_version_changed = bool(self._preflight.get("tree_version_changed")) or validated_version != self._tree_version
        self._tree_version = validated_version
        if tree_version_changed:
            self._load_tree()
        errors_by_row: dict[int, list[str]] = {}
        for error in self._preflight.get("errors") or []:
            row = error.get("row")
            if isinstance(row, int):
                errors_by_row.setdefault(row, []).append(str(error.get("message") or "Invalid operation"))
        normalized = self._preflight.get("operations") or []
        for row, op in enumerate(self._plan):
            server_op = normalized[row] if row < len(normalized) else {}
            op["journal_reference_action"] = server_op.get("journal_reference_action", "none")
            row_errors = errors_by_row.get(row, [])
            if row_errors:
                op["status"] = "; ".join(row_errors)
            elif self._preflight.get("ok"):
                op["status"] = "Valid"
            else:
                op["status"] = "Valid — plan blocked elsewhere"
        self._refresh_plan_view()
        return bool(self._preflight.get("ok"))

    def _apply_plan(self) -> None:
        if not self._preflight or not self._preflight.get("ok"):
            if not self._validate_plan():
                return
        mappings = []
        for op in self._preflight.get("operations") or []:
            if op.get("operation_type") == "add_reference":
                mappings.append(
                    f"{op['source_path']}  →  add Journal reference in {op['destination_parent']}"
                )
            else:
                mappings.append(
                    f"{op['source_path']}  →  {op.get('destination_path') or op.get('raw_destination_path')}"
                )
        detail = "Apply these staged changes?\n\n" + "\n".join(mappings)
        if self._preflight.get("journal_append_count"):
            detail += f"\n\n{self._preflight['journal_append_count']} Journal reference(s) will be added."
        if self._preflight.get("journal_reference_count"):
            detail += (
                f"\n\n{self._preflight['journal_reference_count']} Journal day page(s) will remain in place "
                "and be referenced from the selected topic page."
            )
        answer = QMessageBox.question(
            self,
            "Apply Reorganization",
            detail,
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Apply:
            return
        if self.before_commit and not self.before_commit(self._preflight):
            return
        # Saving dirty editors or a just-finished sync can advance the vault
        # version after the confirmation prompt. Refresh the token immediately
        # before commit so this remains a single, stable apply flow.
        if not self._validate_plan():
            return
        progress = QProgressDialog("Applying staged page moves…", "", 0, 0, self)
        progress.setWindowTitle("Reorganizing Vault")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        try:
            response = self.http.post(
                "/api/vault/reorganize/commit",
                json={
                    "operations": self._request_operations(),
                    "tree_version": self._preflight["tree_version"],
                    "plan_token": self._preflight["plan_token"],
                },
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            progress.close()
            QMessageBox.critical(self, "Reorganization Failed", str(exc))
            self._invalidate_preflight()
            self._refresh_plan_view()
            return
        progress.close()
        self.reorganizationCommitted.emit(result)
        self._plan.clear()
        self._preflight = None
        self._refresh_plan_view()
        self._refresh_workspace_data()
        QMessageBox.information(self, "Reorganization Complete", "The staged page moves were applied.")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._plan:
            answer = QMessageBox.question(
                self,
                "Discard Staged Plan?",
                "Discard the staged reorganization plan?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                event.ignore()
                return
        super().closeEvent(event)
