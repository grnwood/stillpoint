"""
StillPoint Web Server - Core server implementation.

Serves the vault as a navigable HTML site with markdown rendering,
attachment serving, and print/PDF support.
"""

import logging
import os
import posixpath
import re
import socket
import ssl
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from flask import Flask, render_template, send_file, abort, request, redirect
from markupsafe import Markup

logger = logging.getLogger(__name__)


class WebServer:
    """Web server for serving StillPoint vault as HTML."""

    def __init__(self, vault_root: str, config=None):
        """
        Initialize web server.

        Args:
            vault_root: Path to the vault root directory
            config: Optional StillPoint config object for markdown rendering
        """
        self.vault_root = Path(vault_root).resolve()
        self.config = config
        self.app = Flask(
            __name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"),
        )
        self.server_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.host = "127.0.0.1"
        self.port = 0
        self.actual_port = 0
        self.use_ssl = False
        self.ssl_context: Optional[ssl.SSLContext] = None

        self._setup_routes()
        self._setup_template_filters()
        self._check_ssl_certs()

    def _check_ssl_certs(self):
        """Check for SSL certificates and configure if available."""
        cert_dir = Path(__file__).parent
        cert_file = cert_dir / "cert.pem"
        key_file = cert_dir / "key.pem"

        if cert_file.exists() and key_file.exists():
            try:
                self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                self.ssl_context.load_cert_chain(str(cert_file), str(key_file))
                self.use_ssl = True
                logger.info("SSL certificates found - HTTPS will be enabled")
            except Exception as e:
                logger.warning(f"SSL certificates found but could not be loaded: {e}")
                self.use_ssl = False
        else:
            self.use_ssl = False

    def _setup_template_filters(self):
        """Setup Jinja2 template filters."""

        @self.app.template_filter("safe_markdown")
        def safe_markdown(text: str) -> Markup:
            """Render markdown to HTML safely."""
            current_page_path = ""
            try:
                current_page_path = (request.view_args or {}).get("page_path", "") if request else ""
            except Exception:
                current_page_path = ""
            if self.config:
                # Use StillPoint's markdown renderer
                html = self._render_markdown(text, current_page_path)
            else:
                # Fallback to simple rendering
                import markdown
                normalized = self._normalize_markdown_lists(text)
                normalized = self._rewrite_wiki_style_links(normalized)
                normalized = self._rewrite_markdown_image_links(normalized, current_page_path)
                html = markdown.markdown(normalized, extensions=["fenced_code", "tables"])
            return Markup(html)

    def _render_markdown(self, text: str, page_path: str = "") -> str:
        """
        Render markdown using StillPoint's renderer.

        Args:
            text: Markdown text to render

        Returns:
            Rendered HTML string
        """
        # TODO: Integrate with StillPoint's markdown renderer
        # For now, use basic markdown
        text = self._rewrite_task_and_dash_markers(text)
        text = self._normalize_markdown_lists(text)
        text = self._rewrite_highlight(text)
        text = self._rewrite_strikethrough(text)
        text = self._rewrite_wiki_style_links(text)
        text = self._rewrite_markdown_image_links(text, page_path)
        import markdown
        html = markdown.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
        return self._rewrite_task_checkboxes(html)

    @staticmethod
    def _rewrite_task_and_dash_markers(text: str) -> str:
        """Preserve '-' lines as dashed text and render checkbox markers consistently."""
        lines = text.splitlines()
        normalized: list[str] = []
        in_fence = False
        fence_marker = ""
        checkbox_re = re.compile(r"^(?P<indent>[ \t]*)(?:(?:[-+*]|\d+\.)[ \t]+)?\[(?P<state>[ xX])\][ \t]+(?P<rest>.*)$")
        paren_checkbox_re = re.compile(r"^(?P<indent>[ \t]*)\((?P<state>[xX*]?)\)[ \t]*(?P<rest>.*)$")
        dash_re = re.compile(r"^(?P<indent>[ \t]*)-[ \t]+(?P<rest>.*)$")

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                normalized.append(line)
                continue

            if in_fence:
                normalized.append(line)
                continue

            checkbox_match = checkbox_re.match(line)
            if checkbox_match:
                state = (checkbox_match.group("state") or " ").lower()
                checked = state == "x"
                cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
                indent = checkbox_match.group("indent") or ""
                rest = checkbox_match.group("rest") or ""
                normalized.append(f'{indent}<span class="{cls}" aria-hidden="true"></span>{rest}')
                continue

            paren_checkbox_match = paren_checkbox_re.match(line)
            if paren_checkbox_match:
                state = (paren_checkbox_match.group("state") or " ").lower()
                checked = state in {"x", "*"}
                cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
                indent = paren_checkbox_match.group("indent") or ""
                rest = paren_checkbox_match.group("rest") or ""
                normalized.append(f'{indent}<span class="{cls}" aria-hidden="true"></span>{rest}')
                continue

            dash_match = dash_re.match(line)
            if dash_match:
                indent = dash_match.group("indent") or ""
                rest = dash_match.group("rest") or ""
                normalized.append(f"{indent}\\- {rest}")
                continue

            normalized.append(line)

        return "\n".join(normalized)

    @staticmethod
    def _rewrite_wiki_style_links(text: str) -> str:
        """Convert [link|label] external links into markdown links."""
        if not text:
            return text
        pattern = re.compile(r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]*)\]")

        def convert_line(line: str) -> str:
            # Skip replacements inside inline code spans.
            parts = line.split("`")
            for idx in range(0, len(parts), 2):
                def repl(match: re.Match[str]) -> str:
                    link = (match.group("link") or "").strip()
                    label = (match.group("label") or "").strip()
                    if not link:
                        return match.group(0)
                    lower = link.lower()
                    if lower.startswith("http://") or lower.startswith("https://"):
                        display = label if label else link
                        return f"[{display}]({link})"
                    return match.group(0)

                parts[idx] = pattern.sub(repl, parts[idx])
            return "`".join(parts)

        lines = text.splitlines()
        out: list[str] = []
        in_fence = False
        fence_marker = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                out.append(line)
                continue
            if in_fence:
                out.append(line)
                continue
            out.append(convert_line(line))
        return "\n".join(out)

    @staticmethod
    def _is_external_or_anchor(link: str) -> bool:
        lower = (link or "").strip().lower()
        return (
            lower.startswith("http://")
            or lower.startswith("https://")
            or lower.startswith("data:")
            or lower.startswith("/attachments/")
            or lower.startswith("#")
        )

    def _rewrite_markdown_image_links(self, text: str, page_path: str) -> str:
        """Rewrite markdown image links to /attachments/... URLs relative to current page."""
        if not text:
            return text

        page_rel = (page_path or "").strip().lstrip("/")
        base_dir = posixpath.dirname(page_rel) if page_rel else ""
        pattern = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)]+)\)(?P<width>\{width=\d+\})?")

        def repl(match: re.Match[str]) -> str:
            alt = match.group("alt") or ""
            raw_path = (match.group("path") or "").strip().replace("\\", "/")
            width = match.group("width") or ""
            if not raw_path or self._is_external_or_anchor(raw_path):
                return match.group(0)
            if raw_path.startswith("/"):
                rel = raw_path.lstrip("/")
            else:
                rel = posixpath.normpath(posixpath.join(base_dir, raw_path))
            rel = rel.lstrip("/")
            if not rel or rel.startswith("../"):
                return match.group(0)
            attachment_url = f"/attachments/{rel}"
            if width:
                m = re.match(r"\{width=(\d+)\}", width)
                if m:
                    return f'<img alt="{alt}" src="{attachment_url}" width="{m.group(1)}">'
            return f"![{alt}]({attachment_url})"

        return pattern.sub(repl, text)

    @staticmethod
    def _normalize_markdown_lists(text: str) -> str:
        """Insert blank lines before list blocks when missing (improves list parsing)."""
        def _is_list_line(value: str) -> bool:
            trimmed = value.lstrip()
            if trimmed.startswith(("* ", "- ", "+ ")):
                return True
            digits = ""
            for ch in trimmed:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            return bool(digits) and trimmed[len(digits):].startswith(". ")

        def _indent_cols(value: str) -> int:
            cols = 0
            for ch in value:
                if ch == " ":
                    cols += 1
                elif ch == "\t":
                    cols += 4
                else:
                    break
            return cols

        def _strip_indent(value: str, cols: int) -> str:
            if cols <= 0:
                return value
            remaining = cols
            idx = 0
            while idx < len(value) and remaining > 0:
                ch = value[idx]
                if ch == " ":
                    remaining -= 1
                elif ch == "\t":
                    remaining -= 4
                else:
                    break
                idx += 1
            return value[idx:]

        lines = text.splitlines()
        normalized: list[str] = []
        in_fence = False
        fence_marker = ""
        deindent_active = False
        deindent_cols = 0

        for idx, line in enumerate(lines):
            stripped = line.strip()

            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                normalized.append(line)
                continue

            if in_fence:
                normalized.append(line)
                continue

            if stripped == "":
                normalized.append(line)
                continue

            if not deindent_active and _is_list_line(line):
                indent = _indent_cols(line)
                if indent >= 4:
                    deindent_active = True
                    deindent_cols = 4

            if _is_list_line(line):
                if normalized:
                    prev = normalized[-1].strip()
                    if prev and not _is_list_line(prev):
                        normalized.append("")
                if deindent_active:
                    normalized.append(_strip_indent(line, deindent_cols))
                    continue
            else:
                if deindent_active:
                    deindent_active = False
                    deindent_cols = 0

            normalized.append(line)

        return "\n".join(normalized)

    @staticmethod
    def _rewrite_strikethrough(text: str) -> str:
        """Convert ~~text~~ to <del>text</del> outside code fences/inline code."""
        lines = text.splitlines()
        normalized: list[str] = []
        in_fence = False
        fence_marker = ""

        def _replace_inline(value: str) -> str:
            parts = value.split("`")
            for idx in range(0, len(parts), 2):
                parts[idx] = re.sub(r"~~(.*?)~~", r"<del>\1</del>", parts[idx])
            return "`".join(parts)

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                normalized.append(line)
                continue

            if in_fence:
                normalized.append(line)
                continue

            normalized.append(_replace_inline(line))

        return "\n".join(normalized)

    @staticmethod
    def _rewrite_highlight(text: str) -> str:
        """Convert ==text== to <mark>text</mark> outside code fences/inline code."""
        lines = text.splitlines()
        normalized: list[str] = []
        in_fence = False
        fence_marker = ""

        def _replace_inline(value: str) -> str:
            parts = value.split("`")
            for idx in range(0, len(parts), 2):
                parts[idx] = re.sub(r"==(.+?)==", r"<mark>\1</mark>", parts[idx])
            return "`".join(parts)

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                normalized.append(line)
                continue

            if in_fence:
                normalized.append(line)
                continue

            normalized.append(_replace_inline(line))

        return "\n".join(normalized)

    @staticmethod
    def _rewrite_task_checkboxes(html_text: str) -> str:
        """Render markdown task markers as stylable checkbox spans."""
        pattern = re.compile(r"(<li>\s*)\[(?P<state>[ xX])\]\s+", re.IGNORECASE)

        def _replace(match: re.Match[str]) -> str:
            state = (match.group("state") or " ").lower()
            checked = state == "x"
            cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
            return f'{match.group(1)}<span class="{cls}" aria-hidden="true"></span>'

        return pattern.sub(_replace, html_text)

    def _setup_routes(self):
        """Setup Flask routes."""

        @self.app.route("/")
        def index():
            """Serve vault root or home page."""
            # Check for configured home page (try both .md and .txt)
            for ext in [".md", ".txt"]:
                home_file = self.vault_root / f"Home{ext}"
                if home_file.exists():
                    return self._render_page(f"Home{ext}")
                
                # Also check Home/Home.*
                home_file = self.vault_root / "Home" / f"Home{ext}"
                if home_file.exists():
                    return self._render_page(f"Home/Home{ext}")
            
            # Otherwise show directory listing
            return self._render_directory("")

        @self.app.route("/wiki/<path:page_path>")
        def wiki_page(page_path: str):
            """Render a markdown page."""
            # Normalize path - add extension if missing
            page_path = unquote(page_path)
            if not page_path.endswith(".md") and not page_path.endswith(".txt"):
                # Try both extensions
                for ext in [".md", ".txt"]:
                    test_path = self.vault_root / (page_path + ext)
                    if test_path.exists():
                        page_path += ext
                        break
                else:
                    # Default to .md if neither exists
                    page_path += ".md"
            
            return self._render_page(page_path)

        @self.app.route("/browse/")
        @self.app.route("/browse/<path:dir_path>")
        def browse_directory(dir_path: str = ""):
            """Browse directory contents."""
            dir_path = unquote(dir_path)
            return self._render_directory(dir_path)

        @self.app.route("/attachments/<path:file_path>")
        def serve_attachment(file_path: str):
            """Serve attachment files."""
            file_path = unquote(file_path)
            full_path = self.vault_root / file_path
            
            if not full_path.exists() or not full_path.is_file():
                abort(404)
            
            # Security check - ensure file is within vault
            try:
                full_path.resolve().relative_to(self.vault_root)
            except ValueError:
                abort(403)
            
            return send_file(str(full_path))

        @self.app.route("/static/<path:filename>")
        def serve_static(filename: str):
            """Serve static assets (CSS, JS, etc.)."""
            static_dir = Path(__file__).parent / "static"
            file_path = static_dir / filename
            
            if not file_path.exists():
                abort(404)
            
            return send_file(str(file_path))

    def _render_page(self, page_path: str) -> str:
        """
        Render a markdown page.

        Args:
            page_path: Relative path to the markdown file

        Returns:
            Rendered HTML
        """
        full_path = self.vault_root / page_path
        
        if not full_path.exists() or not full_path.is_file():
            abort(404)
        
        # Security check
        try:
            full_path.resolve().relative_to(self.vault_root)
        except ValueError:
            abort(403)
        
        # Read markdown content
        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Error reading file {full_path}: {e}")
            abort(500)
        
        # Get metadata
        title = full_path.stem
        nav_tree = self._build_navigation_tree(page_path)
        
        # Check for print mode
        print_mode = request.args.get("mode") == "print"
        auto_print = request.args.get("autoPrint") == "1"
        
        # Get list of attachments in same directory
        attachments = []
        page_dir = full_path.parent
        if page_dir.exists():
            for item in page_dir.iterdir():
                if item.is_file() and item.suffix not in [".md", ".txt"]:
                    rel_path = item.relative_to(self.vault_root)
                    attachments.append({
                        "name": item.name,
                        "path": f"/attachments/{rel_path}"
                    })
        
        return render_template(
            "page.html",
            title=title,
            content=content,
            page_path=page_path,
            nav_tree=nav_tree,
            current_page_path=page_path,
            attachments=attachments,
            print_mode=print_mode,
            auto_print=auto_print,
        )

    def _render_directory(self, dir_path: str) -> str:
        """
        Render directory listing.

        Args:
            dir_path: Relative path to directory

        Returns:
            Rendered HTML
        """
        full_path = self.vault_root / dir_path if dir_path else self.vault_root
        
        if not full_path.exists() or not full_path.is_dir():
            abort(404)
        
        # Security check
        try:
            full_path.resolve().relative_to(self.vault_root)
        except ValueError:
            abort(403)
        
        # Get directory contents
        items = []
        try:
            for item in sorted(full_path.iterdir()):
                rel_path = item.relative_to(self.vault_root)
                if item.is_dir():
                    items.append({
                        "name": item.name,
                        "type": "dir",
                        "url": f"/browse/{rel_path}"
                    })
                elif item.suffix in [".md", ".txt"]:
                    items.append({
                        "name": item.name,
                        "type": "page",
                        "url": f"/wiki/{rel_path.with_suffix('')}"
                    })
        except Exception as e:
            logger.error(f"Error listing directory {full_path}: {e}")
            abort(500)
        
        title = dir_path if dir_path else "Vault Root"
        nav_tree = self._build_navigation_tree()

        return render_template(
            "index.html",
            title=title,
            dir_path=dir_path,
            items=items,
            nav_tree=nav_tree,
            current_page_path=None,
        )

    def _page_file_for_directory(self, directory: Path) -> Optional[Path]:
        """Return canonical page file for a page-directory, preferring .md over .txt."""
        if not directory or not directory.exists() or not directory.is_dir():
            return None
        name = directory.name
        for suffix in (".md", ".txt"):
            candidate = directory / f"{name}{suffix}"
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _build_navigation_tree(self, current_page_path: str = "") -> list[dict]:
        """Build a folder/page tree similar to app file navigation."""
        current_rel = (current_page_path or "").strip().lstrip("/")
        order_map = {}
        if self.config and hasattr(self.config, "fetch_display_order_map"):
            try:
                order_map = self.config.fetch_display_order_map() or {}
            except Exception:
                order_map = {}

        def build_for_dir(abs_dir: Path, rel_dir: str) -> list[dict]:
            nodes: list[dict] = []
            try:
                entries = list(abs_dir.iterdir())
            except Exception:
                return nodes

            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    child_rel = posixpath.join(rel_dir, entry.name) if rel_dir else entry.name
                    page_file = self._page_file_for_directory(entry)
                    children = build_for_dir(entry, child_rel)
                    if not page_file and not children:
                        continue
                    page_rel = ""
                    url = f"/browse/{child_rel}" if child_rel else "/browse/"
                    if page_file:
                        page_rel = page_file.relative_to(self.vault_root).as_posix()
                        url = f"/wiki/{Path(page_rel).with_suffix('').as_posix()}"
                    active = bool(page_rel and page_rel == current_rel)
                    expanded = bool(current_rel and (current_rel.startswith(child_rel + "/") or active))
                    open_path = f"/{page_rel}" if page_rel else ""
                    nodes.append(
                        {
                            "name": entry.name,
                            "url": url,
                            "page_rel": page_rel,
                            "open_path": open_path,
                            "children": children,
                            "active": active,
                            "expanded": expanded,
                        }
                    )
                    continue
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in (".md", ".txt"):
                    continue
                # Skip folder-page files; represented by directory nodes above.
                if entry.stem == abs_dir.name and abs_dir != self.vault_root:
                    continue
                file_rel = entry.relative_to(self.vault_root).as_posix()
                nodes.append(
                    {
                        "name": entry.stem,
                        "url": f"/wiki/{Path(file_rel).with_suffix('').as_posix()}",
                        "page_rel": file_rel,
                        "open_path": f"/{file_rel}",
                        "children": [],
                        "active": file_rel == current_rel,
                        "expanded": False,
                    }
                )
            nodes.sort(
                key=lambda node: (
                    order_map.get(node.get("open_path")) if node.get("open_path") in order_map else float("inf"),
                    (node.get("name") or "").lower(),
                )
            )

            return nodes

        return build_for_dir(self.vault_root, "")

    def _find_free_port(self) -> int:
        """Find a free port on the system."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def start(self, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        """
        Start the web server.

        Args:
            host: Host to bind to (default: 127.0.0.1)
            port: Port to bind to (0 = auto-pick)

        Returns:
            Tuple of (actual_host, actual_port)
        """
        if self.is_running:
            logger.warning("Server already running")
            return self.host, self.actual_port

        self.host = host
        self.port = port if port > 0 else self._find_free_port()

        # Warning for non-localhost binding
        if host not in ("127.0.0.1", "localhost"):
            logger.warning(
                "⚠️  WARNING: You are exposing your vault over the network! "
                f"Server accessible at: {host}:{self.port}"
            )

        # Start server in background thread
        def run_server():
            try:
                self.app.run(
                    host=self.host,
                    port=self.port,
                    ssl_context=self.ssl_context,
                    debug=False,
                    use_reloader=False,
                )
            except Exception as e:
                logger.error(f"Server error: {e}")
                self.is_running = False

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.is_running = True
        self.actual_port = self.port

        protocol = "https" if self.use_ssl else "http"
        url = f"{protocol}://{self.host}:{self.actual_port}/"
        logger.info(f"Web server started: {url}")

        return self.host, self.actual_port

    def stop(self):
        """Stop the web server."""
        if not self.is_running:
            return

        # Flask's development server doesn't support graceful shutdown
        # In production, you'd want to use a proper WSGI server
        self.is_running = False
        logger.info("Web server stopped")

    def get_url(self) -> Optional[str]:
        """Get the server URL if running."""
        if not self.is_running:
            return None
        protocol = "https" if self.use_ssl else "http"
        return f"{protocol}://{self.host}:{self.actual_port}/"
