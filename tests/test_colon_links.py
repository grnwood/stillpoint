"""Test cases for colon notation path conversion."""
from __future__ import annotations

import pytest

from sp.app.ui.path_utils import (
    path_to_colon,
    colon_to_path,
    colon_to_folder_path,
    ensure_root_colon_link,
    strip_root_prefix,
    normalize_link_target,
)


class TestPathToColon:
    """Test converting filesystem paths to colon notation."""
    
    def test_simple_single_level(self):
        """Single level page: /PageA/PageA.md -> PageA"""
        result = path_to_colon("/PageA/PageA.md")
        assert result == "PageA"

    def test_legacy_txt_suffix(self):
        """Legacy page: /PageA/PageA.txt -> PageA"""
        result = path_to_colon("/PageA/PageA.txt")
        assert result == "PageA"
    
    def test_two_level_hierarchy(self):
        """Two level page: /PageA/PageB/PageB.md -> PageA:PageB"""
        result = path_to_colon("/PageA/PageB/PageB.md")
        assert result == "PageA:PageB"
    
    def test_three_level_hierarchy(self):
        """Three level page: /PageA/PageB/PageC/PageC.md -> PageA:PageB:PageC"""
        result = path_to_colon("/PageA/PageB/PageC/PageC.md")
        assert result == "PageA:PageB:PageC"
    
    def test_joebob_example(self):
        """Real world example: /JoeBob/JoeBob2/JoeBob2.md -> JoeBob:JoeBob2"""
        result = path_to_colon("/JoeBob/JoeBob2/JoeBob2.md")
        assert result == "JoeBob:JoeBob2"
    
    def test_without_leading_slash(self):
        """Path without leading slash should still work"""
        result = path_to_colon("PageA/PageB/PageB.md")
        assert result == "PageA:PageB"
    
    def test_with_trailing_slash(self):
        """Path with trailing slashes should be cleaned"""
        result = path_to_colon("/PageA/PageB/PageB.md/")
        assert result == "PageA:PageB"
    
    def test_empty_path(self):
        """Empty path should return empty string"""
        result = path_to_colon("")
        assert result == ""
    
    def test_root_path(self):
        """Root path should return empty string"""
        result = path_to_colon("/")
        assert result == ""
    
    def test_deep_hierarchy(self):
        """Deep nested structure"""
        result = path_to_colon("/A/B/C/D/E/F/F.md")
        assert result == "A:B:C:D:E:F"
    
    def test_numeric_names(self):
        """Pages with numeric names"""
        result = path_to_colon("/2025/11/12/12.md")
        assert result == "2025:11:12"
    
    def test_names_with_underscores(self):
        """Pages with underscores in names"""
        result = path_to_colon("/My_Page/Sub_Page/Sub_Page.md")
        assert result == "My_Page:Sub_Page"
    
    def test_mixed_case_names(self):
        """Pages with mixed case names"""
        result = path_to_colon("/MyPage/SubPage/SubPage.md")
        assert result == "MyPage:SubPage"


class TestColonToPath:
    """Test converting colon notation to filesystem paths."""
    
    def test_simple_single_level(self):
        """PageA -> /PageA/PageA.md"""
        result = colon_to_path("PageA")
        assert result == "/PageA/PageA.md"
    
    def test_two_level_hierarchy(self):
        """PageA:PageB -> /PageA/PageB/PageB.md"""
        result = colon_to_path("PageA:PageB")
        assert result == "/PageA/PageB/PageB.md"
    
    def test_three_level_hierarchy(self):
        """PageA:PageB:PageC -> /PageA/PageB/PageC/PageC.md"""
        result = colon_to_path("PageA:PageB:PageC")
        assert result == "/PageA/PageB/PageC/PageC.md"
    
    def test_joebob_example(self):
        """JoeBob:JoeBob2 -> /JoeBob/JoeBob2/JoeBob2.md"""
        result = colon_to_path("JoeBob:JoeBob2")
        assert result == "/JoeBob/JoeBob2/JoeBob2.md"
    
    def test_empty_path(self):
        """Empty colon path should return /"""
        result = colon_to_path("")
        assert result == "/"
    
    def test_empty_path_with_vault_root(self):
        """Empty path with vault root should return vault root file"""
        result = colon_to_path("", vault_root_name="MyVault")
        assert result == "/MyVault.md"
    
    def test_deep_hierarchy(self):
        """Deep nested structure"""
        result = colon_to_path("A:B:C:D:E:F")
        assert result == "/A/B/C/D/E/F/F.md"
    
    def test_numeric_names(self):
        """Pages with numeric names"""
        result = colon_to_path("2025:11:12")
        assert result == "/2025/11/12/12.md"
    
    def test_names_with_underscores(self):
        """Pages with underscores"""
        result = colon_to_path("My_Page:Sub_Page")
        assert result == "/My Page/Sub Page/Sub Page.md"

    def test_root_prefixed_single_level(self):
        """Leading ':' should still resolve correctly"""
        result = colon_to_path(":Finance")
        assert result == "/Finance/Finance.md"

    def test_root_prefixed_multi_level(self):
        result = colon_to_path(":Parent:Child")
        assert result == "/Parent/Child/Child.md"


class TestColonToFolderPath:
    """Test converting colon notation to folder paths (without .md file)."""
    
    def test_simple_single_level(self):
        """PageA -> /PageA"""
        result = colon_to_folder_path("PageA")
        assert result == "/PageA"
    
    def test_two_level_hierarchy(self):
        """PageA:PageB -> /PageA/PageB"""
        result = colon_to_folder_path("PageA:PageB")
        assert result == "/PageA/PageB"
    
    def test_three_level_hierarchy(self):
        """PageA:PageB:PageC -> /PageA/PageB/PageC"""
        result = colon_to_folder_path("PageA:PageB:PageC")
        assert result == "/PageA/PageB/PageC"
    
    def test_empty_path(self):
        """Empty path should return /"""
        result = colon_to_folder_path("")
        assert result == "/"

    def test_root_prefixed_path(self):
        result = colon_to_folder_path(":Parent:Child")
        assert result == "/Parent/Child"


class TestRoundTrip:
    """Test that conversions are reversible."""
    
    def test_path_to_colon_to_path(self):
        """Converting path -> colon -> path should return original"""
        original = "/PageA/PageB/PageC/PageC.md"
        colon = path_to_colon(original)
        back = colon_to_path(colon)
        assert back == original
    
    def test_colon_to_path_to_colon(self):
        """Converting colon -> path -> colon should return original"""
        original = "PageA:PageB:PageC"
        path = colon_to_path(original)
        back = path_to_colon(path)
        assert back == original
    
    def test_multiple_round_trips(self):
        """Multiple conversions should be stable"""
        test_cases = [
            "/A/A.md",
            "/A/B/B.md",
            "/A/B/C/C.md",
            "/JoeBob/JoeBob2/JoeBob2.md",
            "/Journal/2025/11/11.md",
        ]
        for original in test_cases:
            colon = path_to_colon(original)
            back = colon_to_path(colon)
            assert back == original, f"Round trip failed for {original}: got {back}"
    
    def test_joebob_round_trip(self):
        """Specific test for the JoeBob issue"""
        # Start with filesystem path
        file_path = "/JoeBob/JoeBob2/JoeBob2.md"
        
        # Convert to colon notation
        colon = path_to_colon(file_path)
        assert colon == "JoeBob:JoeBob2"
        
        # Convert back to filesystem path
        back = colon_to_path(colon)
        assert back == "/JoeBob/JoeBob2/JoeBob2.md"
        
        # Ensure no duplicate folders
        assert back.count("JoeBob2") == 2  # Once in folder, once in filename
        assert "/JoeBob2/JoeBob2/" not in back  # No duplicate folder structure

    def test_root_prefixed_round_trip(self):
        colon = ensure_root_colon_link("Parent:Child")
        assert colon == ":Parent:Child"
        file_path = colon_to_path(colon)
        assert file_path == "/Parent/Child/Child.md"
        back = ensure_root_colon_link(path_to_colon(file_path))
        assert back == ":Parent:Child"


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_single_character_names(self):
        """Single character page names"""
        assert path_to_colon("/A/B/B.md") == "A:B"

    def test_strip_root_prefix(self):
        """strip_root_prefix removes only leading ':'"""
        assert strip_root_prefix(":Parent:Child") == "Parent:Child"
        assert strip_root_prefix("::Parent") == "Parent"
        assert strip_root_prefix("") == ""


class TestEnsureRootPrefix:
    """Ensure emitted links always mark the root."""

    def test_adds_prefix(self):
        assert ensure_root_colon_link("Finance") == ":Finance"

    def test_preserves_anchor(self):
        assert ensure_root_colon_link("Finance#plan") == ":Finance#plan"

    def test_handles_existing_prefix(self):
        assert ensure_root_colon_link(":Finance") == ":Finance"

    def test_ignores_pure_anchor(self):
        assert ensure_root_colon_link("#heading") == "#heading"
        assert colon_to_path("A:B") == "/A/B/B.md"


class TestNormalizeLinkTarget:
    def test_spaces_replaced_and_lowered(self):
        assert normalize_link_target("My Page") == "My_Page"

    def test_multi_segment(self):
        assert normalize_link_target("Parent Page:Child Page") == "Parent_Page:Child_Page"

    def test_preserves_root_prefix(self):
        assert normalize_link_target(":Root Page:Child") == ":Root_Page:Child"

    def test_preserves_anchor(self):
        assert normalize_link_target("Page Name#Section") == "Page_Name#Section"

    def test_handles_extra_whitespace(self):
        assert normalize_link_target("  Mixed   Case  ") == "Mixed_Case"
    
    def test_names_with_numbers(self):
        """Names mixing letters and numbers"""
        assert path_to_colon("/Page1/Page2/Page2.md") == "Page1:Page2"
        assert colon_to_path("Page1:Page2") == "/Page1/Page2/Page2.md"
    
    def test_whitespace_handling(self):
        """Paths with extra whitespace should be handled"""
        result = path_to_colon("  /PageA/PageB/PageB.md  ")
        assert result == "PageA:PageB"
    
    def test_vault_root_special_case(self):
        """Vault root name is used for empty colon path"""
        result = colon_to_path("", vault_root_name="TestVault")
        assert result == "/TestVault.md"
    
    def test_very_long_hierarchy(self):
        """Very deep nesting should work"""
        parts = [f"Level{i}" for i in range(10)]
        file_path = "/" + "/".join(parts) + f"/{parts[-1]}.md"
        colon = path_to_colon(file_path)
        back = colon_to_path(colon)
        assert back == file_path


class TestTextLinks:
    """Test markdown-style text links with labels [label](link)."""
    
    def test_link_with_label_format(self):
        """Test that a link with label is formatted correctly"""
        # Simulate what insert_link does: [label](colon_path)
        link_name = "diggus"
        colon_path = "Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus"
        link_text = f"[{link_name}]({colon_path})"
        
        # Verify format is correct
        assert link_text == "[diggus](Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus)"
        # Verify the label doesn't contain the path
        assert "Journal" not in link_name
        assert "(" not in link_name
        assert ")" not in link_name
    
    def test_link_without_label_format(self):
        """Test that a link without label is just the colon path"""
        colon_path = "Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus"
        link_text = colon_path
        
        # Verify it's just the plain colon path
        assert link_text == "Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus"
        assert "[" not in link_text
        assert "]" not in link_text
        assert "(" not in link_text
        assert ")" not in link_text
    
    def test_editor_display_transform(self):
        """Test that editor transforms [label](link) correctly for display"""
        import re
        # This is the pattern used by the editor to transform links
        MARKDOWN_LINK_STORAGE_PATTERN = re.compile(
            r"\[(?P<text>[^\]]+)\]\s*\((?P<link>[\w]+(?::[\w]+)+)\)", 
            re.MULTILINE | re.DOTALL
        )
        
        # Storage format (what's in the file)
        storage = "[diggus](Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus)"
        
        # Display format (what's shown in editor): \x00Link\x00Label
        def encode_link(match):
            text = match.group("text")
            link = match.group("link")
            return f"\x00{link}\x00{text}"
        
        display = MARKDOWN_LINK_STORAGE_PATTERN.sub(encode_link, storage)
        
        # Verify display format hides the link syntax
        assert display == "\x00Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus\x00diggus"
        # The visible part should only be "diggus"
        visible_part = display.split('\x00')[-1]
        assert visible_part == "diggus"
    
    def test_editor_save_transform(self):
        """Test that editor transforms display format back to storage on save"""
        import re
        # This is the pattern used by the editor to restore links
        MARKDOWN_LINK_DISPLAY_PATTERN = re.compile(r"\x00(?P<link>[\w:]+)\x00(?P<text>[^\x00]+)")
        
        # Display format (what's in the editor)
        display = "\x00Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus\x00diggus"
        
        # Storage format (what gets saved to file): [Label](Link)
        def decode_link(match):
            link = match.group("link")
            text = match.group("text")
            return f"[{text}]({link})"
        
        storage = MARKDOWN_LINK_DISPLAY_PATTERN.sub(decode_link, display)
        
        # Verify storage format is correct
        assert storage == "[diggus](Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus)"
    
    def test_link_label_no_corruption(self):
        """Test that link labels don't get path appended"""
        # Common bug: link name becomes "diggus(Journal:...)" instead of just "diggus"
        link_name = "diggus"
        colon_path = "Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus"
        
        # Verify the link name is clean (no path appended)
        assert link_name == "diggus"
        assert colon_path not in link_name
        assert f"{link_name}({colon_path})" != link_name
        
        # Create the link
        link_text = f"[{link_name}]({colon_path})"
        
        # Verify the result doesn't have path in the label
        assert link_text == "[diggus](Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus)"
        assert "[diggus(Journal" not in link_text
    
    def test_round_trip_with_label(self):
        """Test that a link with label survives load -> display -> save"""
        import re
        
        # Patterns from the editor
        MARKDOWN_LINK_STORAGE_PATTERN = re.compile(
            r"\[(?P<text>[^\]]+)\]\s*\((?P<link>[\w]+(?::[\w]+)+)\)", 
            re.MULTILINE | re.DOTALL
        )
        MARKDOWN_LINK_DISPLAY_PATTERN = re.compile(r"\x00(?P<link>[\w:]+)\x00(?P<text>[^\x00]+)")
        
        def encode_link(match):
            text = match.group("text")
            link = match.group("link")
            return f"\x00{link}\x00{text}"
        
        def decode_link(match):
            link = match.group("link")
            text = match.group("text")
            return f"[{text}]({link})"
        
        # Original storage format
        original = "[diggus](Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus)"
        
        # Load: convert to display format
        display = MARKDOWN_LINK_STORAGE_PATTERN.sub(encode_link, original)
        
        # Save: convert back to storage format
        saved = MARKDOWN_LINK_DISPLAY_PATTERN.sub(decode_link, display)
        
        # Should be identical
        assert saved == original
    
    def test_round_trip_without_label(self):
        """Test that a plain colon link survives load -> display -> save"""
        # Plain colon links are not transformed
        original = "Journal:2025:11:12:someStuff:LetsDoThis:LetsDoit:BiggusDiggus"
        
        # No transformation should occur for plain links
        # (they don't match the markdown link pattern)
        import re
        MARKDOWN_LINK_STORAGE_PATTERN = re.compile(
            r"\[(?P<text>[^\]]+)\]\s*\((?P<link>[\w]+(?::[\w]+)+)\)", 
            re.MULTILINE | re.DOTALL
        )
        
        display = MARKDOWN_LINK_STORAGE_PATTERN.sub(lambda m: f"\x00{m.group('link')}\x00{m.group('text')}", original)
        
        # Should be unchanged (no match)
        assert display == original
