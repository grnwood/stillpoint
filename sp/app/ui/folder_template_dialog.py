"""Dialog for selecting and creating pages from folder templates."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QListWidget,
    QDialogButtonBox,
    QSplitter,
)


class FolderTemplateDialog(QDialog):
    """Dialog to select a folder template and specify target folder name."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New from Folder Template")
        self.resize(700, 500)
        
        self.selected_template_path: Optional[Path] = None
        self.folder_name: str = ""
        
        self._build_ui()
        self._load_templates()
    
    def _build_ui(self) -> None:
        """Build the dialog UI with tree widget and preview."""
        layout = QVBoxLayout(self)
        
        # Instructions
        instructions = QLabel(
            "Choose a folder template to create a multi-page structure.\n"
            "All pages will be created in a new folder with your chosen name."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        
        # Splitter for tree and preview
        splitter = QSplitter(Qt.Horizontal)
        
        # Left side: Category tree
        tree_container = QVBoxLayout()
        tree_label = QLabel("Template Categories:")
        tree_container.addWidget(tree_label)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Folder Templates")
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        tree_container.addWidget(self.tree)
        
        from PySide6.QtWidgets import QWidget
        tree_widget = QWidget()
        tree_widget.setLayout(tree_container)
        splitter.addWidget(tree_widget)
        
        # Right side: Preview list
        preview_container = QVBoxLayout()
        preview_label = QLabel("Will create:")
        preview_container.addWidget(preview_label)
        
        self.preview_list = QListWidget()
        preview_container.addWidget(self.preview_list)
        
        preview_widget = QWidget()
        preview_widget.setLayout(preview_container)
        splitter.addWidget(preview_widget)
        
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        
        # Folder name input
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Folder Name:"))
        self.folder_name_input = QLineEdit()
        self.folder_name_input.setPlaceholderText("e.g., MyProject, NewFeature, ChapterOne")
        self.folder_name_input.textChanged.connect(self._on_name_changed)
        name_layout.addWidget(self.folder_name_input)
        layout.addLayout(name_layout)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        self.ok_button = button_box.button(QDialogButtonBox.Ok)
        self.ok_button.setEnabled(False)
        layout.addWidget(button_box)
    
    def _load_templates(self) -> None:
        """Load folder templates from built-in and user directories."""
        builtin_dir = Path(__file__).parent.parent.parent / "templates" / "folders"
        user_dir = Path.home() / ".stillpoint" / "templates" / "folders"
        
        # Collect all categories and templates
        categories: dict[str, list[tuple[str, Path]]] = {}
        
        for base_dir in (builtin_dir, user_dir):
            if not base_dir.exists():
                continue
            
            # Each subdirectory in folders/ is a category
            for category_dir in sorted(base_dir.iterdir()):
                if not category_dir.is_dir():
                    continue
                
                category_name = category_dir.name
                if category_name not in categories:
                    categories[category_name] = []
                
                # Each subdirectory in the category is a template
                for template_dir in sorted(category_dir.iterdir()):
                    if not template_dir.is_dir():
                        continue
                    
                    # Check if it has any .txt files
                    txt_files = list(template_dir.glob("*.txt"))
                    if txt_files:
                        template_name = template_dir.name
                        # Avoid duplicates (user overrides builtin)
                        if not any(name == template_name for name, _ in categories[category_name]):
                            categories[category_name].append((template_name, template_dir))
        
        # Populate tree widget
        self.tree.clear()
        for category_name in sorted(categories.keys()):
            category_item = QTreeWidgetItem([category_name])
            category_item.setData(0, Qt.UserRole, None)  # Not selectable
            self.tree.addTopLevelItem(category_item)
            
            for template_name, template_path in sorted(categories[category_name]):
                template_item = QTreeWidgetItem([f"📁 {template_name}"])
                template_item.setData(0, Qt.UserRole, template_path)
                category_item.addChild(template_item)
            
            category_item.setExpanded(True)
        
        if self.tree.topLevelItemCount() == 0:
            placeholder = QTreeWidgetItem(["No folder templates found"])
            self.tree.addTopLevelItem(placeholder)
    
    def _on_selection_changed(self) -> None:
        """Update preview when selection changes."""
        self.preview_list.clear()
        
        items = self.tree.selectedItems()
        if not items:
            self.selected_template_path = None
            self._update_ok_button()
            return
        
        item = items[0]
        template_path = item.data(0, Qt.UserRole)
        
        if template_path is None:
            # Selected a category, not a template
            self.selected_template_path = None
            self._update_ok_button()
            return
        
        self.selected_template_path = template_path
        
        # Show preview of files
        txt_files = sorted(template_path.glob("*.txt"))
        for txt_file in txt_files:
            self.preview_list.addItem(f"📄 {txt_file.stem}.txt")
        
        # Auto-suggest folder name from template name if input is empty
        if not self.folder_name_input.text():
            suggested_name = template_path.name.replace("-", " ")
            self.folder_name_input.setPlaceholderText(f"e.g., {suggested_name}")
        
        self._update_ok_button()
    
    def _on_name_changed(self, text: str) -> None:
        """Handle folder name input changes."""
        self._update_ok_button()
    
    def _update_ok_button(self) -> None:
        """Enable OK button only when template selected and name entered."""
        has_template = self.selected_template_path is not None
        has_name = bool(self.folder_name_input.text().strip())
        self.ok_button.setEnabled(has_template and has_name)
    
    def _on_accept(self) -> None:
        """Validate and accept the dialog."""
        if not self.selected_template_path:
            return
        
        folder_name = self.folder_name_input.text().strip()
        if not folder_name:
            return
        
        self.folder_name = folder_name
        self.accept()
    
    def get_template_path(self) -> Optional[Path]:
        """Return the selected template directory path."""
        return self.selected_template_path
    
    def get_folder_name(self) -> str:
        """Return the entered folder name."""
        return self.folder_name
