# StillPoint Web Server

The StillPoint web server allows you to serve your vault as a navigable HTML site with full markdown rendering, attachment serving, and print/PDF export support.

## Features

- **Markdown Rendering**: Server-side markdown to HTML conversion
- **Wiki Links**: Navigate between pages using wiki-style links
- **Attachment Support**: Serve images, PDFs, and other attachments
- **Print/PDF Export**: Use browser's built-in print functionality (Ctrl+P / Save as PDF)
- **Clean Print Styles**: Optimized CSS for paper and PDF output
- **Directory Browsing**: Browse vault folder structure
- **Optional HTTPS**: Support for TLS/SSL with certificates
- **Safe Defaults**: Localhost-only binding by default

## Usage

### Command Line

Start the web server from the command line:

```bash
# Basic usage (auto-pick port, localhost only)
python -m sp.app.main --webserver --vault /path/to/vault

# Specify host and port
python -m sp.app.main --webserver 127.0.0.1:8000 --vault /path/to/vault

# Allow network access (WARNING: exposes vault)
python -m sp.app.main --webserver 0.0.0.0:8000 --vault /path/to/vault

# Auto-pick free port
python -m sp.app.main --webserver localhost:0 --vault /path/to/vault
```

### GUI

From within StillPoint:

1. Open your vault
2. Go to **Tools** → **Start Web Server**
3. Configure host and port (default: 127.0.0.1, auto port)
4. Click **Start Server**
5. Click the URL link to open in browser
6. Click **Stop Server** when done

## Routes

- `/` - Vault homepage (or root directory listing)
- `/wiki/<page_path>` - Render a markdown page
- `/browse/<dir_path>` - Browse directory contents
- `/attachments/<file_path>` - Serve attachment files
- `/static/<asset>` - Serve CSS/JS assets

## Print Mode

For clean printing:

- `/wiki/<page>?mode=print` - Print-optimized view
- `/wiki/<page>?mode=print&autoPrint=1` - Auto-open print dialog

Or simply use Ctrl+P / Cmd+P from any page.

## HTTPS Support (Optional)

To enable HTTPS, place certificate files in `sp/webserver/`:

- `cert.pem` - SSL certificate
- `key.pem` - Private key
- `ca.pem` - Certificate authority (optional)

The server will automatically detect and use these files.

## Security

- **Default**: Binds to `127.0.0.1` (localhost only)
- **Network Binding**: Requires explicit configuration
- **Warning**: Non-localhost binding logs a prominent warning
- **Path Validation**: All file access is validated against vault root

## Technical Details

- **Framework**: Flask + Jinja2
- **Styling**: Pico.css (semantic HTML framework)
- **Threading**: Runs in background thread (GUI mode)
- **File Support**: Both `.md` and `.txt` files
- **Extensions**: Automatically tries common extensions

## Architecture

```
sp/webserver/
├── __init__.py           # Module exports
├── server.py             # Flask server implementation
├── templates/            # Jinja2 templates
│   ├── base.html         # Base layout
│   ├── page.html         # Page rendering
│   └── index.html        # Directory listing
└── static/               # CSS/JS assets
    ├── stillpoint.css          # Custom styles
    ├── print.css         # Print styles
    └── vendor/
        └── pico.min.css  # Pico CSS framework
```

## Dependencies

- Flask >= 3.0
- Jinja2 >= 3.1
- MarkupSafe (via Flask)
- Werkzeug (via Flask)

## Limitations

- Development server (Flask's built-in)
- Not suitable for production deployment
- For production, use a WSGI server like gunicorn or waitress

## Future Enhancements

- [ ] Production WSGI server option
- [ ] Authentication support
- [ ] Search functionality
- [ ] Live reload on file changes
- [ ] Custom themes
- [ ] Export entire site as static HTML
