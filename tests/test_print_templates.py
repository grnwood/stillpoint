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
