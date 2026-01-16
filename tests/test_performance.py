#!/usr/bin/env python3
"""Test script to diagnose setPlainText performance issues."""

import os
import time
from pathlib import Path

# Set environment variables for testing
os.environ["ZIMX_INCREMENTAL_LOAD"] = "0"  # Default: use setPlainText
# os.environ["ZIMX_DISABLE_HIGHLIGHTER_LOAD"] = "1"  # Uncomment to test without highlighter

# Add stillpoint to path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication
from sp.app.ui.markdown_editor import MarkdownEditor

def test_large_document():
    """Test performance with a large document."""
    app = QApplication([])
    
    # Create a large test document (similar size to user's slow document)
    lines = []
    for i in range(100):
        lines.append(f"# Heading {i}")
        lines.append(f"This is content for section {i}. " * 10)  # ~500 chars per section
        lines.append("")
        lines.append(f"* Bullet point {i}.1")
        lines.append(f"* Bullet point {i}.2 with [a link](Page{i}:SubPage) inside")
        lines.append("")
    
    content = "\n".join(lines)
    print(f"Test document: {len(lines)} lines, {len(content)} characters")
    
    editor = MarkdownEditor()
    editor.show()
    
    # Test performance
    print("\nTesting setPlainText performance...")
    start_time = time.perf_counter()
    editor.set_markdown(content)
    end_time = time.perf_counter()
    
    duration_ms = (end_time - start_time) * 1000
    print(f"Total time: {duration_ms:.1f}ms")
    
    if duration_ms > 1000:
        print("⚠️  Performance issue detected!")
        print("Try setting environment variables:")
        print("  ZIMX_INCREMENTAL_LOAD=1")
        print("  ZIMX_DISABLE_HIGHLIGHTER_LOAD=1")
    else:
        print("✅ Performance looks good!")
    
    app.quit()

if __name__ == "__main__":
    test_large_document()