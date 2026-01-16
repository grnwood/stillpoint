"""
StillPoint Web Server - Core server implementation.

Serves the vault as a navigable HTML site with markdown rendering,
attachment serving, and print/PDF support.
"""

import logging
import os
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
            if self.config:
                # Use StillPoint's markdown renderer
                html = self._render_markdown(text)
            else:
                # Fallback to simple rendering
                import markdown
                html = markdown.markdown(text, extensions=["fenced_code", "tables"])
            return Markup(html)

    def _render_markdown(self, text: str) -> str:
        """
        Render markdown using StillPoint's renderer.

        Args:
            text: Markdown text to render

        Returns:
            Rendered HTML string
        """
        # TODO: Integrate with StillPoint's markdown renderer
        # For now, use basic markdown
        import markdown
        return markdown.markdown(text, extensions=["fenced_code", "tables", "nl2br"])

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
        
        return render_template(
            "index.html",
            title=title,
            dir_path=dir_path,
            items=items,
        )

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
