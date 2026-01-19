## Task 1 - Schema
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

## TASK 2 – Add a search-index helper module

Create a Python module, e.g. `search_index.py`, that exposes functions:

* `init_search_db()` – ensure tables exist.
* `upsert_page(path: str, mtime: int, content: str) -> None`
* `delete_page(path: str) -> None`
* `search_pages(query: str, limit: int = 50) -> list[dict]`

Implementation details:

`upsert_page`:

* In a transaction:

  * Insert or update `pages` row by `path`:

    * If path exists, update `mtime`.
    * Else insert.
  * Get `id` for that path.
  * Insert or update `pages_fts` row:

    * `INSERT INTO pages_fts(rowid, content) VALUES (?, ?)`
    * If rowid already exists, use `INSERT OR REPLACE` or a separate `UPDATE`.

`delete_page`:

* Lookup `id` by `path` from `pages`.
* Delete row from `pages` and `pages_fts` with that `id`.

`search_pages`:

* Use `MATCH` query over `pages_fts`.
* Join back to `pages` to get `path`.
* Use SQLite snippet/bm25 if available.

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
( )  tag text @tag1 @tag2

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

## TASK 3 – Hook search index into write/delete flows

Find the FastAPI handlers where pages are created/updated/deleted.

For example (pseudo):

* `PUT /pages/{path}` handler:

  * Validates data.
  * Writes the file to disk (path → file).
  * Determine `mtime` (e.g. `int(time.time())` or `os.path.getmtime(filepath)`).
  * Call `search_index.upsert_page(path, mtime, content)`.

* `DELETE /pages/{path}` handler:

  * Deletes the file on disk.
  * Calls `search_index.delete_page(path)`.

Important: server remains the only code that writes to disk, so the DB stays in sync automatically. No filesystem watcher is required.

---

## TASK 4 – Implement `/search` API endpoint

Add a FastAPI route:

```python
GET /search?q=...&limit=...
```

Behavior:

* Validate that `q` is non-empty.
* Call `search_index.search_pages(q, limit)`.
* Call search for a subpage tree
* Return JSON response with an array of objects:

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

## TASK 5 – Wiring & basic error handling

* Ensure `init_search_db()` is called once at app startup (e.g. FastAPI startup event).
* Handle errors gracefully:

  * If `search_pages` fails, return a 500 with a simple error message.
* Log search queries and any DB exceptions for debugging.

---

## TASK 7 - UI Implementation
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

## CONSTRAINTS / STYLE

* Use SQLite’s built-in `sqlite3` 
* Keep the FTS integration minimal and straightforward.
* Do NOT implement incremental re-indexing from disk; indexing is triggered only by API writes/deletes.
* Do NOT use grep or external processes; all search is via SQLite FTS.