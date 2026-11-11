from pathlib import Path

import pytest

from sp.app.obsidian_import import plan_import


def write(root, name, content=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def vaults(tmp_path):
    source, destination = tmp_path / "obsidian", tmp_path / "stillpoint"
    source.mkdir()
    destination.mkdir()
    return source, destination


def test_mapping_forward_links_and_aliases(vaults):
    source, dest = vaults
    write(source, "MainNote.md", "[[New Folder/New Note|Pretty]] [Other](New%20Folder/New%20Note.md#Section)")
    write(source, "New Folder/New Note.MD", "[[MainNote.md]] [[../MainNote#Intro]]")
    plan = plan_import(source, "/Imported", dest)
    assert [p.dest_path for p in plan.pages] == [
        "/Imported/MainNote/MainNote.md", "/Imported/New Folder/New Note/New Note.md"]
    assert plan.pages[0].content == (
        "[:Imported:New_Folder:New_Note|Pretty] [:Imported:New_Folder:New_Note#Section|Other]")
    assert plan.pages[1].content == "[:Imported:MainNote|MainNote.md] [:Imported:MainNote#Intro|../MainNote#Intro]"
    assert not plan.warnings
    assert not list(dest.iterdir())


def test_attachment_lookup_labels_dedup_and_shared_copies(vaults):
    source, dest = vaults
    write(source, "Pasted image.png", "image")
    write(source, "Documents/report.pdf", "pdf")
    write(source, "Folder/Note.md", (
        "![[Pasted image.png]] ![Alt text](../Pasted%20image.png) "
        "[Download](Documents/report.pdf) [[Documents/report.pdf|PDF]]"))
    write(source, "Root.md", "![[Pasted image.png]]")
    plan = plan_import(source, "/", dest)
    nested = plan.pages[1] if plan.pages[1].rel_stem == "Folder/Note" else plan.pages[0]
    assert nested.content == (
        "![Pasted image.png](./Pasted_image.png) ![Alt text](./Pasted_image.png) "
        "[Download](./report.pdf) [PDF](./report.pdf)")
    assert len(nested.attachments) == 2
    assert plan.attachment_count == 3
    assert not plan.warnings


def test_missing_ambiguous_and_skipped_targets_are_preserved(vaults):
    source, dest = vaults
    text = "[[Note]] [[Missing|Alias]] ![[missing.png]] [[Existing]]"
    write(source, "Main.md", text)
    write(source, "A/Note.md")
    write(source, "B/Note.md")
    write(source, "Existing.md")
    original = write(dest, "Existing/Existing.txt", "leave alone")
    plan = plan_import(source, "/", dest)
    assert next(p for p in plan.pages if p.rel_stem == "Main").content == text
    assert len(plan.skipped) == 1
    assert len(plan.warnings) == 4
    assert original.read_text() == "leave alone"


def test_explicit_paths_disambiguate_duplicate_basenames(vaults):
    source, dest = vaults
    write(source, "A/Note.md")
    write(source, "B/Note.md", "[[Note]] [[A/Note]] [Other](../A/Note.md)")
    plan = plan_import(source, "/", dest)
    assert plan.pages[1].content == "[:B:Note|Note] [:A:Note|A/Note] [:A:Note|Other]"
    assert not plan.warnings


def test_attachment_name_collisions_include_existing_files_and_page(vaults):
    source, dest = vaults
    write(source, "One/a.png", "one")
    write(source, "Two/a.png", "two")
    write(source, "Files/Note.md", "this is a note, not an attachment")
    write(source, "Note.md", "![[One/a.png]] ![[Two/a.png]] [again](One/a.png)")
    write(dest, "Note/a.png", "existing attachment")
    plan = plan_import(source, "/", dest)
    page = next(p for p in plan.pages if p.rel_stem == "Note")
    assert [a.name for a in page.attachments] == ["a-2.png", "a-3.png"]
    assert page.content == "![a.png](./a-2.png) ![a.png](./a-3.png) [again](./a-2.png)"
    assert (dest / "Note/a.png").read_text() == "existing attachment"


def test_protect_code_frontmatter_external_urls_and_unsupported_features(vaults):
    source, dest = vaults
    text = (
        '---\naliases: "[[Other]]"\n---\n'
        '`[[Other]]` ``[x](Other.md)``\n'
        '[[Other]] ![[Other]] [[Other#^block]] [[Other^block]]\n'
        '[Web](https://example.org/a.md) ![Remote](https://example.org/a.png)\n'
        '```md\n[[Other]]\n```\n'
        '~~~\n![[Other]]\n~~~\n'
        '```\n[[Other]]\n')
    write(source, "Main.md", text)
    write(source, "Other.md")
    plan = plan_import(source, "/", dest)
    assert plan.pages[0].content == text.replace(
        '[[Other]] ![[Other]] [[Other#^block]]', '[:Other|Other] ![[Other]] [[Other#^block]]')
    assert len(plan.warnings) == 3


def test_exclusions_symlinks_and_escape(vaults, tmp_path):
    source, dest = vaults
    for directory in (".obsidian", ".git", ".trash"):
        write(source, directory + "/Hidden.md")
    external = write(tmp_path, "outside.md")
    (source / "link.md").symlink_to(external)
    (source / "linked").symlink_to(tmp_path, target_is_directory=True)
    write(source, "Main.md", "[outside](../outside.md) [[.obsidian/Hidden]]")
    plan = plan_import(source, "/", dest)
    assert len(plan.pages) == 1
    assert len(plan.warnings) == 2
    assert not plan.pages[0].attachments
    with pytest.raises(ValueError, match="overlap"):
        plan_import(source, "/", source / "output")
    with pytest.raises(ValueError, match="cannot contain"):
        plan_import(source, "/../escape", dest)


def test_conflicting_normalized_page_names_are_all_skipped(vaults):
    source, dest = vaults
    write(source, "A B.md")
    write(source, "A_B.md")
    plan = plan_import(source, "/", dest)
    assert not plan.pages
    assert len(plan.skipped) == 2


def test_destination_blockers_and_symlinks(vaults, tmp_path):
    source, dest = vaults
    write(source, "Folder/Note.md")
    write(dest, "Folder", "file blocks directory")
    assert len(plan_import(source, "/", dest).skipped) == 1
    (dest / "Escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        plan_import(source, "/Escape", dest)


def test_empty_folder_and_invalid_source(vaults):
    source, dest = vaults
    assert not plan_import(source, "/", dest).pages
    with pytest.raises(ValueError, match="folder"):
        plan_import(write(source, "Note.md"), "/", dest)


def test_sample_vault(vaults):
    _, dest = vaults
    source = Path(__file__).parents[1] / "dev-assets/specs/obsidian-sample/Sample Vault"
    plan = plan_import(source, "/", dest)
    assert len(plan.pages) == 2
    assert plan.attachment_count == 2
    nested = next(p for p in plan.pages if p.rel_stem == "New Folder/New Note")
    assert "./Pasted_image_20251216232514.png" in nested.content
    assert nested.content.endswith('```bash\necho "Hello, Obsidian"\n```\n```')
    assert any("Internal Note" in w for w in plan.warnings)


def test_normalized_parent_collisions_and_attachment_child_folder(vaults):
    source, dest = vaults
    write(source, "A B/One.md")
    write(source, "A_B/Two.md")
    write(source, "Parent.md", "[[Files/Child]]")
    write(source, "Parent/Child.md")
    write(source, "Files/Child", "extensionless attachment")
    plan = plan_import(source, "/", dest)
    assert len(plan.skipped) == 2
    parent = next(p for p in plan.pages if p.rel_stem == "Parent")
    assert parent.attachments[0].name == "Child-2"
    assert parent.content == "[Child](./Child-2)"


def test_markdown_parentheses_titles_and_inline_code(vaults):
    source, dest = vaults
    write(source, "files/photo (1).png", "image")
    write(source, "Main.md", (
        '![A](files/photo%20(1).png "Caption")\n'
        '`![[files/photo (1).png]]`\n'
        '[[files/photo (1).png|File label]]'))
    plan = plan_import(source, "/", dest)
    assert plan.pages[0].content == (
        '![A](./photo__1_.png "Caption")\n'
        '`![[files/photo (1).png]]`\n'
        '[File label](./photo__1_.png)')
    assert plan.attachment_count == 1


def test_sizing_aliases_remain_intact_with_warning(vaults):
    source, dest = vaults
    write(source, "image.png", "image")
    text = "![[image.png|300x200]]"
    write(source, "Main.md", text)
    plan = plan_import(source, "/", dest)
    assert plan.pages[0].content == text
    assert len(plan.warnings) == 1
    assert not plan.attachment_count


def test_legacy_page_filename_is_not_used_for_attachment(vaults):
    source, dest = vaults
    write(source, "Note.md", "[Text](Note.txt)")
    write(source, "Note.txt", "plain text attachment")
    page = plan_import(source, "/", dest).pages[0]
    assert page.attachments[0].name == "Note-2.txt"
    assert page.content == "[Text](./Note-2.txt)"


def test_width_only_alias_translates_to_stillpoint_sizing(vaults):
    source, dest = vaults
    write(source, "image.png", "image")
    write(source, "Note.md", "![[image.png|300]]")
    plan = plan_import(source, "/", dest)
    assert plan.pages[0].content == "![image.png](./image.png){width=300}"
    assert plan.attachment_count == 1
    assert not plan.warnings
