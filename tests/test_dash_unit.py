#!/usr/bin/env python3
"""
Unit test for dash behavior fix in MarkdownEditor.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from sp.app.ui.markdown_editor import MarkdownEditor

def test_is_bullet_line():
    """Test _is_bullet_line method excludes dashes but includes other bullet markers."""
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    editor = MarkdownEditor()
    
    # Test cases: (input, expected_is_bullet, expected_indent, expected_content)
    test_cases = [
        # Dash lines should NOT be bullets
        ("- simple dash", False, "", ""),
        ("  - indented dash", False, "", ""),
        ("\t- tab indented dash", False, "", ""),
        ("    - four space dash", False, "", ""),
        
        # Asterisk lines should be bullets
        ("* simple asterisk", True, "", "simple asterisk"),
        ("  * indented asterisk", True, "  ", "indented asterisk"),
        ("\t* tab indented asterisk", True, "\t", "tab indented asterisk"),
        
        # Bullet symbol lines should be bullets
        ("• simple bullet", True, "", "simple bullet"),
        ("  • indented bullet", True, "  ", "indented bullet"),
        
        # Plus lines should NOT be bullets (like dashes)
        ("+ simple plus", False, "", ""),
        ("  + indented plus", False, "", ""),
        
        # Non-bullet lines
        ("regular text", False, "", ""),
        ("", False, "", ""),
        ("   ", False, "", ""),
        ("# heading", False, "", ""),
        ("*bold text*", False, "", ""),  # No space after *
        ("-hyphenated-word", False, "", ""),  # No space after -
    ]
    
    print("Testing _is_bullet_line method:")
    all_passed = True
    
    for input_text, expected_is_bullet, expected_indent, expected_content in test_cases:
        is_bullet, indent, content = editor._is_bullet_line(input_text)
        
        if (is_bullet == expected_is_bullet and 
            indent == expected_indent and 
            content == expected_content):
            status = "✓ PASS"
        else:
            status = "✗ FAIL"
            all_passed = False
        
        print(f"{status}: {input_text!r}")
        print(f"    Expected: is_bullet={expected_is_bullet}, indent={expected_indent!r}, content={expected_content!r}")
        print(f"    Got:      is_bullet={is_bullet}, indent={indent!r}, content={content!r}")
        print()
    
    if all_passed:
        print("🎉 All tests passed!")
        return True
    else:
        print("❌ Some tests failed!")
        return False

if __name__ == "__main__":
    success = test_is_bullet_line()
    sys.exit(0 if success else 1)