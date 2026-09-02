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


def test_print_token_resolves_its_captured_vault_without_request_context(tmp_path: Path) -> None:
    from sp.server import api

    token = api._create_token(
        {"sub": "print-test", "scope": "print", "vault_root": str(tmp_path)},
        api.timedelta(minutes=1),
    )

    assert api._get_print_vault_root(token) == tmp_path.resolve()
