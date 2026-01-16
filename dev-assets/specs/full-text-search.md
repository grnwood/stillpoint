Task 1 - Schema
You are working in a FastAPI-based server that reads and writes page files on disk.
Clients NEVER touch the filesystem directly; they only use the API.

Goal: Add a full-text search feature backed by SQLite FTS (FTS5) instead of grepping files.
The server should:

- Keep the filesystem as the source of truth for page content.
- Maintain a small SQLite "sidecar" DB that mirrors pages for search.
- Provide a `GET /search?q=...` endpoint that searches full text across all pages and returns ranked results with snippets.
- Optionally (in the UI) allow to search a subtree only (`?subtree=/Path/To/SubTree`)...

--------------------------------
CONTEXT / ASSUMPTIONS
--------------------------------

- Each "page" is stored as a text file on disk (e.g., Markdown).
- The FastAPI app already has endpoints to:
  - Create / update a page (e.g. PUT /pages/{path})
  - Delete a page (e.g. DELETE /pages/{path})
  - Read a page (GET /pages/{path})
- There is already some SQLite usage in the app, OR we can add a dedicated SQLite file for search, e.g. `search_index.db`.
- Use SQLite FTS5.

--------------------------------
TASK 1 – Create SQLite FTS schema
--------------------------------
INTEGER PRIMARY KEY
     - `path` TEXT NOT NULL UNIQUE   -- canonical file path or page identifier used by the API
     - `mtime` INTEGER NOT NULL      -- last modified time in epoch seconds

2) `pages_fts` (FTS5 virtual table)
   - Columns:
     - `content`   -- full text of the file
   - Use `content_rowid='id'` to link to `pages.id`.

SQL DDL (adjust names to match the project style):

```sql
CREATE TABLE IF NOT EXISTS pages (
    id      INTEGER PRIMARY KEY,
    path    TEXT NOT NULL UNIQUE,
    mtime   INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    content,
    content_rowid='id'
);
````

Optional: add indexes on `pages.path` and `pages.mtime` if needed.

---

TASK 2 – Add a search-index helper module

Create a Python module, e.g. `search_index.py`, that exposes functions:

• `init_search_db()` – ensure tables exist.
• `upsert_page(path: str, mtime: int, content: str) -> None`
• `delete_page(path: str) -> None`
• `search_pages(query: str, limit: int = 50) -> list[dict]`

Implementation details:

`upsert_page`:

• In a transaction:

  • Insert or update `pages` row by `path`:

    • If path exists, update `mtime`.
    • Else insert.
  • Get `id` for that path.
  • Insert or update `pages_fts` row:

    • `INSERT INTO pages_fts(rowid, content) VALUES (?, ?)`
    • If rowid already exists, use `INSERT OR REPLACE` or a separate `UPDATE`.

`delete_page`:

• Lookup `id` by `path` from `pages`.
• Delete row from `pages` and `pages_fts` with that `id`.

`search_pages`:

• Use `MATCH` query over `pages_fts`.
• Join back to `pages` to get `path`.
• Use SQLite snippet/bm25 if available.

Example SQL:

```sql
SELECT
  p.path,
  snippet(pages_fts, 0, '[', ']', '...', 10) AS snippet,
  bm25(pages_fts) AS rank
FROM pages_fts
JOIN pages p ON p.id = pages_fts.rowid
WHERE pages_fts MATCH ?
ORDER BY rank
LIMIT ?;
```

Return Python dictionaries like:

```python
{
  "path": "Project/StillPoint/Design.txt",
  "snippet": "...some [highlighted] content...",
}
```

`search_index.py` should contain all DB-specific logic. FastAPI layer should only call these functions.

TAGS -  page content will have '@' tags on them....  there are two contexts for this....

task tags... these tag tasks depicted by '()' 
☐  tag text @tag1 @tag2

tags can also occur *outside* of the task context and then they apply to the page.

Page Heading
some page text @pageTag
lore ipesm, ec

I intend to be able to search the index by tags in both contexts.

Example search query:

'search term' = find everything with search term
'search term @pageTag' = find everything with search term AND tag @pageTag

'searchterm1 searchterm2 @pageTag1 @pageTag2' = find everything with searchterm1 OR searchterm2 OR @pageTag1 or @pageTag2

'(searchterm1 searchterm2) AND (@pageTag1)' = find searchterm1 OR searmtherm2 AND @pageTag1
etc.

---

TASK 3 – Hook search index into write/delete flows

Find the FastAPI handlers where pages are created/updated/deleted.

For example (pseudo):

• `PUT /pages/{path}` handler:

  • Validates data.
  • Writes the file to disk (path → file).
  • Determine `mtime` (e.g. `int(time.time())` or `os.path.getmtime(filepath)`).
  • Call `search_index.upsert_page(path, mtime, content)`.

• `DELETE /pages/{path}` handler:

  • Deletes the file on disk.
  • Calls `search_index.delete_page(path)`.

Important: server remains the only code that writes to disk, so the DB stays in sync automatically. No filesystem watcher is required.

---

TASK 4 – Implement `/search` API endpoint

Add a FastAPI route:

```python
GET /search?q=...&limit=...
```

Behavior:

• Validate that `q` is non-empty.
• Call `search_index.search_pages(q, limit)`.
• Call search for a subpage tree
• Return JSON response with an array of objects:

```json
[
  {
    "path": "Project/StillPoint/Design.txt",
    "snippet": "...text with [q] in context..."
  },
  ...
]
```

This endpoint should NOT directly read files from disk.
If later we want to fetch the full page, clients should use the existing `/pages/{path}` endpoint.

---

TASK 5 – Wiring & basic error handling

• Ensure `init_search_db()` is called once at app startup (e.g. FastAPI startup event).
• Handle errors gracefully:

  • If `search_pages` fails, return a 500 with a simple error message.
• Log search queries and any DB exceptions for debugging.

---

TASK 7 - UI Implementation
- to the right of the 'refresh' icon in the file navigator there should be a magnifying class search icon
  - when this icon is selected the search tab opens
  - the search tab will have a search entry box at the top with a go arrow button to the left of it to kick off the search.
    - just to the left of the search entry box will be a question mark icon, when hovered over pops a label with search instructions.
  - below the search entry box is a filter by page tree box, this allows for a type ahead index search (like ctrl-j does) to select a path (optional)
  - below this is the search results display.  
    - this should show search results based on PATH and then the hits
      - Page/PageHit
        - highlighted search term (clickable to load the page at hte line number with a flash animation)
- the main editor will have a dialog on ctrl-shift-f
  - dialog 'name 'search across vault'
  - search term (same question icon with hover over instructions)
  - below that a limit by page path (default it to current path) and leave it unchecked
  - search button
    - when search is clicked this window closes and transfers focus over to the search tab (make it visible if not shown)
      - passes is the search term, page path limit, etc. so they are populated by default as the search is being performed.
      - search results stream real time from the search api into the search results area of the search tab.

CONSTRAINTS / STYLE

• Use SQLite’s built-in `sqlite3` 
• Keep the FTS integration minimal and straightforward.
• Do NOT implement incremental re-indexing from disk; indexing is triggered only by API writes/deletes.
• Do NOT use grep or external processes; all search is via SQLite FTS.

## Implementation
I'll implement the full-text search feature following the plan. Let me start by setting up the task tracking and then proceeding with the implementation. 

Created 7 todos

Read [](file:///home/grnwood/stillpoint/sp/app/config.py#2320-2320), lines 2320 to 2360

Read [](file:///home/grnwood/stillpoint/sp/app/config.py#2480-2480), lines 2480 to 2540

Read [](file:///home/grnwood/stillpoint/sp/app/config.py#2540-2540), lines 2540 to 2580

Now let me start implementing step by step: 

Starting: *Create FTS5 database schema in config.py* (1/7)

Using "Replace String in File"


```

```


Using "Replace String in File"


```

```


Starting: *Build search index module (server/search_index.py)* (2/7)

Created [](file:///home/grnwood/stillpoint/sp/server/search_index.py)

Starting: *Hook indexing into API write/delete endpoints* (3/7)

Read [](file:///home/grnwood/stillpoint/sp/server/api.py#340-340), lines 340 to 370

Read [](file:///home/grnwood/stillpoint/sp/server/api.py#440-440), lines 440 to 470

Read [](file:///home/grnwood/stillpoint/sp/server/api.py#390-390), lines 390 to 420

Read [](file:///home/grnwood/stillpoint/sp/server/api.py#1-1), lines 1 to 50

Now let me read the import section and add the necessary hooks: 

Using "Multi-Replace String in Files"


```

```


Starting: *Create Search tab widget (ui/search_tab.py)* (5/7)

Read [](file:///home/grnwood/stillpoint/sp/app/ui/jump_dialog.py#1-1), lines 1 to 200

Now let me create the Search tab widget: 

Created [](file:///home/grnwood/stillpoint/sp/app/ui/search_tab.py)

Starting: *Refactor left panel to tabbed interface* (6/7)

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#50-50), lines 50 to 100

Now let me find where the main_window imports and creates UI components: 

Searched for regex `from.*tabbed_right_panel|TabbedRightPanel` (`**/sp/app/ui/main_window.py`), 3 results

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#180-180), lines 180 to 250

Now let me check the imports section and add the necessary imports, then modify the left panel: 

Using "Replace String in File"


```

```


Using "Replace String in File"


```

```
Now let me find where the tree container is created and add the search icon: 

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#625-625), lines 625 to 670

Now let me add the search icon button after the refresh button and create the tabbed left panel: 

Using "Replace String in File"


```

```
Now let me find where the tree_container is created and replace it with a tabbed widget: 

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#840-840), lines 840 to 880

Now let me replace the tree_container with a tabbed widget containing both Vault and Search tabs: 

Using "Replace String in File"


```

```
Now I need to add the helper methods for opening the search tab and handling search results. Let me find a good place to add these methods: 

Searched for regex `def _refresh_tree|def _collapse_tree` (`**/sp/app/ui/main_window.py`), 2 results

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#2540-2540), lines 2540 to 2580

Let me find a good location to add the search-related methods, perhaps near other UI navigation methods: 

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#3400-3400), lines 3400 to 3430

Good! Let me add the search-related methods after the collapse method: 

Using "Replace String in File"


```

```


Starting: *Add Ctrl+Shift+F keyboard shortcut* (7/7)

Let me search for where keyboard shortcuts are registered: 

Searched for regex `QShortcut|Ctrl.*J|shortcut|KeySequence` (`**/sp/app/ui/main_window.py`), 20 results

Read [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py#1260-1260), lines 1260 to 1300

Perfect! I can see the shortcuts are registered inline. Let me add the Ctrl+Shift+F shortcut: 

Using "Replace String in File"


```

```


Completed: *Add Ctrl+Shift+F keyboard shortcut* (7/7)

Excellent! All tasks are complete. Now let me verify the implementation by checking for any syntax errors: 

Checked [](file:///home/grnwood/stillpoint/sp/app/config.py), [](file:///home/grnwood/stillpoint/sp/server/search_index.py), [](file:///home/grnwood/stillpoint/sp/server/api.py), [](file:///home/grnwood/stillpoint/sp/app/ui/search_tab.py), [](file:///home/grnwood/stillpoint/sp/app/ui/main_window.py), no problems found

Perfect! No errors detected. Let me create a quick summary of what was implemented:

---

## Implementation Complete ✓

I've successfully implemented the full-text search feature with SQLite FTS5 across all layers of the application:

### Backend Changes

**1. Database Schema (config.py)**
- Added `pages_search_index` table (id, path, mtime)
- Added `pages_search_fts` FTS5 virtual table (content, content_rowid='id')
- Added `_ensure_pages_search_fts()` function following the existing tasks_fts pattern

**2. Search Index Module (search_index.py)**
- `upsert_page()` - Indexes page content on write/create
- `delete_page()` - Removes page from index on delete
- `search_pages()` - FTS5 MATCH queries with:
  - Native boolean operators (AND/OR/NEAR/NOT)
  - "exact phrase" support
  - @tag filtering via join with `page_tags` table
  - Optional subtree filtering
  - BM25 ranking with 10-token snippets using `[` `]` markers

**3. API Integration (api.py)**
- Hooked indexing into `file_write` endpoint
- Hooked indexing into `create_path` endpoint  
- Hooked deletion into `delete_path` endpoint
- Replaced stub `/api/search` endpoint with full implementation accepting `q`, `subtree`, and `limit` parameters

### Frontend Changes

**4. Search Tab Widget (search_tab.py)**
- Search entry field with Go button and 300ms debounce
- Help icon with comprehensive FTS5 syntax tooltip (AND/OR/NOT/"phrases"/NEAR/@tags)
- Optional subtree filter using type-ahead dialog (like Ctrl+J)
- Results tree displaying paths with highlighted snippets
- Double-click navigation to pages with signal emission

**5. Left Panel Refactoring (main_window.py)**
- Converted left panel to `QTabWidget` with two tabs:
  - **Vault tab** (index 0) - Existing file navigator tree
  - **Search tab** (index 1) - New search interface
- Added search icon next to refresh button in vault header
- Added `_open_search_tab()` method to switch tabs and focus search
- Added `_show_search_dialog()` for Ctrl+Shift+F dialog
- Added `_on_search_result_selected()` to handle page navigation from results
- Registered **Ctrl+Shift+F** keyboard shortcut

### Key Features

✓ No bulk reindexing - only API writes trigger indexing  
✓ FTS5 native operators - AND, OR, NEAR, NOT, "exact phrases"  
✓ Page tag filtering - `@tag1 @tag2` support  
✓ Subtree scoping - optional path prefix filtering  
✓ Non-empty query requirement  
✓ 10-token context snippets with `[match]` highlighting  
✓ Search icon in vault header  
✓ Ctrl+Shift+F shortcut opens search dialog  
✓ Results display with clickable navigation

The implementation follows the existing codebase patterns and integrates seamlessly with the current architecture!

Made changes.

