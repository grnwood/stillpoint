# print_to_browser
Created Tuesday 25 November 2025
---

This feature should:

* Copy the entire page.txt, page folder and page attachments to a tmp dir converting each page into a .HTML page from a .TXT page.
  * Write all new links as relative to the root of the folder so they can be navigating via HTML.
* The tmp dir should be deferred to OS level as to how to create
  * No cleanup is needed, the OS can clean tmp folders according to its own schedule
* When complete, launch the main page (PageName.html) using the underlying OS browser. underlying OS browser.

So in zim there is a templating engine that has logic and the ability to cycle through all the pages and write them out with a navigaiton bar, etc.

I suppose we could develop that, however, I only really use this feature exclusively to print what I'm working on when I need to share it with others or export.

What are some of the best html rendering engines or templating engines.  

## Some Ideas
---
Nice, this will be a super handy feature for StillPoint. Let’s lay it out in concrete pieces so you can actually build it.

I’ll assume you want this flow (tweak as needed in your head as you read):

> StillPoint note → user clicks **Print…** → browser opens with a print-friendly HTML view of that note → browser’s native print dialog (or “Save as PDF”).

---

## 1. Two main architectural options

### Option A: **Temp HTML file → default browser**

**Flow:**

1. User hits **File → Print to Browser** (or Ctrl+P).
2. StillPoint:

   * Renders current page markdown → HTML.
   * Wraps it in a simple HTML template with print CSS.
   * Writes to a temp file, e.g. `/tmp/stillpoint-print-<page-id>.html`.
3. Calls `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`.
4. Browser opens that file; a small `<script>` can auto-call `window.print()` if you want.

**Pros:**

* No server dependency (works even if FastAPI backend isn’t running or exposed).
* Simple to debug: you can just open the HTML file manually.
* No “port in use” or firewall nonsense.

**Cons:**

* Harder to support dynamic things (like a query to load children pages by ID).
* If you want shared CSS/theme between app+web+print, you’ll probably duplicate parts of it or embed it inline.

---

### Option B: **FastAPI route → browser**

You already have FastAPI in the mix, so:

**Flow:**

1. User hits **Print to Browser**.
2. StillPoint opens `http://127.0.0.1:8000/print/{page_id}` in browser.
3. FastAPI:

   * Loads the page from SQLite/filesystem.
   * Renders markdown to HTML.
   * Injects metadata (title, date, tags, etc.).
   * Returns a print-styled HTML.
4. Browser shows it and optionally auto-fires `window.print()`.

**Pros:**

* Centralized: all rendering, theming, templates are server-side, reusable for any future “web view” of notes.
* Easy to add params like `?subtree=true` or `?template=in_depth_spec`.
* Can support collection-specific CSS, themes, etc.

**Cons:**

* Requires the backend to be available and same origin (localhost).
* Slightly more plumbing: you have to keep the route stable, etc.

> My gut: For StillPoint, Option B fits your current architecture better (since you’ve already got FastAPI and RAG). But Option A is a dead-simple MVP you could knock out fast.

---

## 2. What the **print HTML** should look like

Regardless of A or B, conceptually you’ll want:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{{PageName}} – StillPoint</title>
  <style>
    /* Screen */
    body { font-family: system-ui, sans-serif; margin: 1rem; }
    .page-title { font-size: 1.6rem; font-weight: 600; margin-bottom: 0.25rem; }
    .meta { font-size: 0.75rem; color: #666; margin-bottom: 1rem; }

    /* Print overrides */
    @media print {
      body { margin: 0.75in; }
      a[href^="http"]:after { content: " (" attr(href) ")"; font-size: 0.7em; }
      .no-print { display: none !important; }
      pre, code { font-size: 0.8em; }
      h1, h2, h3 { page-break-after: avoid; }
      .page-break { page-break-before: always; }
    }
  </style>
  <script>
    // Optional: auto open print dialog when loaded
    window.addEventListener('load', () => {
      const auto = new URLSearchParams(location.search).get('autoPrint');
      if (auto === '1') window.print();
    });
  </script>
</head>
<body>
  <div class="no-print" style="text-align:right;">
    <button onclick="window.print()">Print</button>
  </div>
  <div class="page-title">{{PageName}}</div>
  <div class="meta">
    Created {{DayDateYear}} • {{DOW}}, {{Month}} {{dd}}, {{YYYY}}
    <!-- could also show collection, tags, etc. -->
  </div>
  <article class="content">
    {{RenderedMarkdownHere}}
  </article>
</body>
</html>
```

Key ideas:

* **Separate “meta” header**: page title + created/modified date + maybe path (`Collection / Folder / Page`).
* **Print CSS via `@media print`**: margins, page breaks, code block shrinking, hide “Print” button.
* **Optional `autoPrint` query param** so you can choose whether it auto-pops the dialog.

---

## 3. Integration with your markdown + template system

You already have:

* A **markdown renderer** (Python-side or Qt-side).
* A **template system** for new pages (`{{PageName}}`, `{{DayDateYear}}`, etc.).

For printing, you can:

1. Reuse the **same markdown renderer** you use for preview.
2. Treat printing as a **“view template”**:

   * Have a Jinja2 (or simple string) template like `print_template.html`.
   * Render markdown → `html_body`.
   * Plug into `render_template("print_template.html", body=html_body, page=page_metadata)`.

This also gives you a path later to have **multiple print templates**:

* `spec-print.html` (with SFCC headings, etc.).
* `meeting-notes-print.html` (with table of attendees, action items).
* `bible-memory-print.html` for your verse-memorization stuff.

Browser route could be like:

* `/print/{page_id}?template=default`
* `/print/{page_id}?template=spec`

---

## 4. UX details in the desktop app

In the PySide6 UI:

* **Menu item:**

  * `File → Print… → Print to browser`
  * Possibly later: `Print as PDF (using browser)` which just opens with `autoPrint=1` and a little tooltip telling user to “Save as PDF”.
* **Toolbar icon:** a simple printer icon that defaults to “browser mode.”

**Implementation sketch (Option B):**

```python
from PySide6.QtGui import QAction
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

def create_print_action(self):
    action = QAction("Print to Browser", self)
    action.triggered.connect(self.on_print_to_browser)
    self.file_menu.addAction(action)

def on_print_to_browser(self):
    page_id = self.current_page_id  # however you track it
    url = QUrl(f"http://127.0.0.1:8000/print/{page_id}?autoPrint=1")
    QDesktopServices.openUrl(url)
```

---

## 5. Handling special cases

A few things worth deciding now:

1. **Dark mode vs. print mode**

   * Don’t print dark theme. Always use **light-on-white** for print.
   * Use `@media print` to enforce a white background and dark text.

2. **Images & attachments**

   * Ensure you use **absolute URLs** when serving print HTML via FastAPI.
   * For temp files, either:

     * Embed images as `data:` URLs (more work, but portable), or
     * Rely on files being in a known location with `file://` URLs (can be finicky across browsers).

3. **Multiple pages / subpages**

   * Later enhancement: `include_children=true` to concatenate a page and its children, separated by `<div class="page-break"></div>`.

4. **Headers / footers**

   * Browsers often add their own headers/footers (URL, date, page #).
   * Some users like it, some don’t — you can leave it to the browser.
   * If you want custom ones, you can add a header/footer area in HTML and rely on the print CSS to position it, but browser support is… “quirky”.

---

## 6. A simple dev roadmap

You could implement this in three tiny milestones:

1. **MVP (local file):**

   * Take current page markdown → HTML.
   * Dump into a hard-coded HTML template string with minimal CSS.
   * Write to `/tmp/stillpoint-print-test.html`.
   * Call `QDesktopServices.openUrl` on it.

2. **Server route + template:**

   * Add `/print/{page_id}` route in FastAPI.
   * Move HTML into a Jinja2 template.
   * Open `http://localhost:8000/print/{page_id}?autoPrint=1` instead of local file.

3. **Polish:**

   * Better print CSS, code formatting, page breaks.
   * Optional per-template print styles.
   * Add option for “Include child pages”.

---

If you tell me which way you’re leaning (temp file vs FastAPI route), I can sketch an actual concrete code path (FastAPI endpoint + Qt wiring + markdown render) so you can mostly copy-paste it into StillPoint.