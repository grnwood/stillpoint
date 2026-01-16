"""Web Server Control Dialog."""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QGroupBox,
    QFormLayout,
    QSpinBox,
)

from sp.webserver import WebServer

logger = logging.getLogger(__name__)


class WebServerDialog(QDialog):
    """Dialog for controlling the web server."""

    def __init__(self, vault_root: str, config=None, parent=None):
        """
        Initialize the web server dialog.

        Args:
            vault_root: Path to vault root
            config: StillPoint config object
            parent: Parent widget
        """
        super().__init__(parent)
        self.vault_root = vault_root
        self.config = config
        self.web_server: WebServer | None = None

        self.setWindowTitle("StillPoint Web Server")
        self.setMinimumWidth(500)

        self._init_ui()

    def _init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout(self)

        # Configuration group
        config_group = QGroupBox("Server Configuration")
        config_layout = QFormLayout()

        # Host input
        self.host_input = QLineEdit("127.0.0.1")
        self.host_input.setPlaceholderText("127.0.0.1")
        config_layout.addRow("Host:", self.host_input)

        # Port input
        self.port_input = QSpinBox()
        self.port_input.setRange(0, 65535)
        self.port_input.setValue(0)
        self.port_input.setSpecialValueText("Auto")
        config_layout.addRow("Port:", self.port_input)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # Control buttons
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("Start Server")
        self.start_button.clicked.connect(self._start_server)
        button_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Server")
        self.stop_button.clicked.connect(self._stop_server)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)

        layout.addLayout(button_layout)

        # Status display
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()

        self.status_label = QLabel("Server is Stopped")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def _start_server(self):
        """Start the web server."""
        try:
            host = self.host_input.text().strip() or "127.0.0.1"
            port = self.port_input.value()

            # Create web server if needed
            if not self.web_server:
                self.web_server = WebServer(self.vault_root, self.config)

            # Start server
            actual_host, actual_port = self.web_server.start(host, port)

            # Update UI
            protocol = "https" if self.web_server.use_ssl else "http"
            url = f"{protocol}://{actual_host}:{actual_port}/"

            self.status_label.setText(f'Server is running on <a href="{url}">{url}</a>')
            self.status_label.setOpenExternalLinks(True)
            self.status_label.setTextFormat(Qt.RichText)

            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.host_input.setEnabled(False)
            self.port_input.setEnabled(False)

            logger.info(f"Web server started: {url}")

        except Exception as e:
            self.status_label.setText(f"Error starting server: {e}")
            logger.error(f"Failed to start web server: {e}", exc_info=True)

    def _stop_server(self):
        """Stop the web server."""
        try:
            if self.web_server:
                self.web_server.stop()

            # Update UI
            self.status_label.setText("Server is Stopped")
            self.status_label.setTextFormat(Qt.PlainText)

            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.host_input.setEnabled(True)
            self.port_input.setEnabled(True)

            logger.info("Web server stopped")

        except Exception as e:
            self.status_label.setText(f"Error stopping server: {e}")
            logger.error(f"Failed to stop web server: {e}", exc_info=True)

    def closeEvent(self, event):
        """Handle dialog close - stop server if running."""
        if self.web_server and self.web_server.is_running:
            self._stop_server()
        super().closeEvent(event)
