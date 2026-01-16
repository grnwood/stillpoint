#!/usr/bin/env python3
"""
Test script to verify dash behavior fix in MarkdownEditor.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sp.app.ui.markdown_editor import MarkdownEditor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

def test_dash_behavior():
    """Test that dash-prefixed lines don't trigger bullet mode."""
    app = QApplication(sys.argv)
    
    widget = QWidget()
    layout = QVBoxLayout()
    
    label = QLabel("""
Test Instructions:
1. Type "- this is a dash line" and press Enter
2. The new line should NOT have a bullet (•)
3. Type "* this is a star line" and press Enter  
4. The new line SHOULD have a bullet (•)
5. Type "+ this is a plus line" and press Enter
6. The new line SHOULD have a bullet (•)
    """)
    layout.addWidget(label)
    
    editor = MarkdownEditor()
    layout.addWidget(editor)
    
    widget.setLayout(layout)
    widget.resize(600, 400)
    widget.show()
    
    return app.exec()

if __name__ == "__main__":
    test_dash_behavior()