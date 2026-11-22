from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def test_load_print_template_falls_back_to_builtin_dir(tmp_path: Path, monkeypatch) -> None:
    from sp.server import api

    bundled = tmp_path / "bundled_templates"
    bundled.mkdir(parents=True, exist_ok=True)
    (bundled / "print.html").write_text(
        "<html><head><title>{{ title }}</title></head><body>{{ body_html }}</body></html>",
        encoding="utf-8",
    )
    (bundled / "print.css").write_text("body { color: black; }", encoding="utf-8")

    broken_env = Environment(
        loader=FileSystemLoader(tmp_path / "missing_templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    monkeypatch.setattr(api, "_PRINT_TEMPLATES", broken_env)
    monkeypatch.setattr(api, "_builtin_print_templates_dir", lambda: bundled)

    template = api._load_print_template(tmp_path)
    rendered = template.render(title="Print Test", body_html="ok")
    css = api._load_print_css(tmp_path)

    assert "Print Test" in rendered
    assert "ok" in rendered
    assert "color: black" in css


def test_print_markdown_rendering_markers_api_and_webserver(tmp_path: Path) -> None:
    from sp.server import api
    from sp.webserver.server import WebServer

    markdown_text = "\n".join(
        [
            "- dashed line",
            "* bullet line",
            "[ ] standalone todo",
            "[x] standalone done",
            "- [ ] dashed todo",
            "* [x] bulleted done",
            "==important==",
        ]
    )

    api_html = api._render_markdown_html(markdown_text)
    web_html = WebServer(str(tmp_path))._render_markdown(markdown_text, "")

    for rendered in (api_html, web_html):
        assert "- dashed line" in rendered
        assert "<li>bullet line" in rendered
        assert "[ ]" not in rendered
        assert "[x]" not in rendered
        assert rendered.count("md-checkbox--unchecked") >= 2
        assert rendered.count("md-checkbox--checked") >= 2
        assert "<mark>important</mark>" in rendered


def test_print_css_contains_checkbox_and_underline_styles() -> None:
    from sp.server import api

    server_css = api._load_print_css(Path.cwd())
    web_css = (Path(__file__).resolve().parents[1] / "sp" / "webserver" / "static" / "print.css").read_text(
        encoding="utf-8"
    )

    for css in (server_css, web_css):
        assert ".md-checkbox" in css
        assert "md-checkbox--checked::after" in css
        assert "mark" in css
        assert "text-decoration: underline" in css



def test_transient_print_preview_rewrites_only_local_relative_images(
        tmp_path: Path) -> None:
    import time
    from sp.server import api

    image = tmp_path / "diagram image.png"
    image.write_bytes(b"image")
    preview_id = "preview-test"
    api._PRINT_PREVIEWS[preview_id] = {
        "expires_at": time.time() + 60,
        "title": "Outside page",
        "content": "![diagram](diagram image.png)",
        "path_label": str(tmp_path / "outside.md"),
        "source_directory": tmp_path,
    }
    try:
        rendered = api._rewrite_preview_image_src(
            '<img src="diagram image.png"><img src="https://example.com/a.png">',
            preview_id,
            "print-token",
        )
    finally:
        api._PRINT_PREVIEWS.pop(preview_id, None)

    assert (
        '/print-preview/preview-test/asset/diagram%20image.png?token=print-token'
        in rendered
    )
    assert 'src="https://example.com/a.png"' in rendered


def test_nested_bullets_keep_hierarchy_in_both_print_renderers(tmp_path: Path) -> None:
    from xml.etree import ElementTree as ET
    from sp.server import api
    from sp.webserver.server import WebServer

    text = (
        "Big things from yesterday:\n"
        "* **Review context**\n"
        "  * Each review gets its own data\n"
        "    * Add `fetchData()`\n"
        "      SOQL the required fields.\n"
        "* **Queueable work**\n"
        "  * Otherwise `localWork()`\n"
    )
    for rendered in (api._render_markdown_html(text),
                     WebServer(str(tmp_path))._render_markdown(text, "")):
        root = ET.fromstring(f"<div>{rendered}</div>")
        assert len(root.findall("./ul/li")) == 2
        assert len(root.findall("./ul/li/ul/li")) == 2
        assert len(root.findall("./ul/li/ul/li/ul/li")) == 1
        assert root.find("./ul/li/strong").text == "Review context"
        assert root.find("./ul/li/ul/li/ul/li/code").text == "fetchData()"
        assert "SOQL the required fields." in "".join(root.itertext())
        assert not root.findall(".//pre")


def test_print_bold_matches_editor_with_spaces_inside_markers(tmp_path: Path) -> None:
    from sp.server import api
    from sp.webserver.server import WebServer

    text = (
        "* **No dependencies causing loops... **\n"
        "  * Mitigated with runLast\n\n"
        "** leading space** and **ordinary** and ***both***\n\n"
        "`**literal **`\n\n"
        "```text\n**literal **\n```\n\n"
        r"\*\*escaped **"
    )
    for rendered in (api._render_markdown_html(text),
                     WebServer(str(tmp_path))._render_markdown(text, "")):
        assert "<strong>No dependencies causing loops... </strong>" in rendered
        assert "<strong> leading space</strong>" in rendered
        assert "<strong>ordinary</strong>" in rendered
        assert "<strong><em>both</em></strong>" in rendered
        assert "<code>**literal **</code>" in rendered
        assert '<code class="language-text">**literal **' in rendered
        assert "<p>**escaped **</p>" in rendered


def test_html_renderers_preserve_tabbed_prose_and_inline_formatting(tmp_path: Path) -> None:
    from xml.etree import ElementTree as ET
    from sp.markdown_html import render_markdown_html
    from sp.webserver.server import WebServer

    text = (
        "Runs on create.\n"
        "\tif `initial`:\n"
        "\t\t**Evaluate decisions**\n"
        "\telse:\n"
        "\t\tLoad records\n\n"
        "Next paragraph\n"
        "  Loop:\n"
        "    `evaluate()`\n\n"
        "```text\n\tcode stays tabbed\n```\n\n"
        "    standalone code"
    )
    renderers = [render_markdown_html(text),
                 render_markdown_html(text, source_anchors=True),
                 WebServer(str(tmp_path))._render_markdown(text, "")]
    for rendered in renderers:
        root = ET.fromstring(f"<div>{rendered}</div>")
        assert "\u00a0" * 4 + "if " in "".join(root.itertext())
        assert "\u00a0" * 8 + "Evaluate decisions" in "".join(root.itertext())
        assert "\u00a0" * 2 + "Loop:" in "".join(root.itertext())
        assert "\u00a0" * 4 + "evaluate()" in "".join(root.itertext())
        assert root.find("./p/strong").text == "Evaluate decisions"
        assert len(root.findall("./pre")) == 2
        assert "    code stays tabbed" in rendered
        assert "standalone code" in "".join(root.findall("./pre/code")[1].itertext())
