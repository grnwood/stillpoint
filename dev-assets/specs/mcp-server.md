Great question — this is exactly the right moment to be intentional about MCP scope so it doesn’t turn into a grab-bag of “random tools” later.

Below is a **clean, opinionated MCP tool plan for StillPoint**, aligned with how you’re actually using it (markdown pages, attachments, graph, RAG, local LLMs, automation). I’ll group these by **capability tier**, starting minimal and growing into “agent-useful but still safe”.

---

## 1️⃣ Core Filesystem Primitives (MVP)

You already have most of these — this is the **non-negotiable base**.

### 📄 File operations

* `read_file(path)`
* `write_file(path, content)`
* `append_file(path, content)`
* `delete_file(path)`
* `rename_file(old_path, new_path)`
* `copy_file(src, dst)`
* `move_file(src, dst)`

Why:

* Enables refactors, templating, AI rewrites, and batch edits
* Lets an agent *safely manipulate knowledge artifacts*

---

### 📁 Folder operations

* `create_folder(path)`
* `delete_folder(path)`
* `list_folder(path, recursive=false)`
* `folder_tree(path, depth=?)`

Why:

* Needed for structural reorgs
* Critical for visualizations + planning tools

---

## 2️⃣ StillPoint-Aware Knowledge Tools (High Value)

This is where StillPoint stops being “a file MCP” and becomes **domain-aware**.

### 🧠 Page semantics

* `list_pages()`
* `read_page(page_id | page_path)`
* `write_page(page_id | page_path, content)`
* `rename_page(old, new)`
* `page_exists(name)`

Optional but powerful:

* `get_page_metadata(page)`
  (created, modified, tags, backlinks count, word count)

---

### 🔗 Link & graph introspection

These unlock **agentic navigation**.

* `get_page_links(page)`
* `get_backlinks(page)`
* `find_pages_linking_to(page)`
* `graph_neighbors(page, depth=1)`

This enables:

* “Summarize everything related to X”
* “Find orphan pages”
* “Refactor this section of my notes”

---

## 3️⃣ Search & Retrieval Tools (Agent Gold)

These are **huge leverage** for LLMs.

### 🔍 Text search

* `search_text(query, path?, regex=false)`
* `search_pages(query)`
* `search_headings(query)`

---

### 🧲 Embedding / RAG hooks

Even if backed by SQLite/Chroma:

* `semantic_search(query, k=5)`
* `embed_page(page)`
* `reindex_embeddings(scope=page|folder|all)`

This lets agents:

* Answer questions *without reading everything*
* Stay fast + token-efficient

---

## 4️⃣ Markdown-Aware Editing Tools (Very StillPoint)

Instead of raw `write_file`, give the agent **surgical tools**.

* `insert_under_heading(page, heading, content)`
* `replace_section(page, heading, content)`
* `list_headings(page)`
* `extract_section(page, heading)`
* `summarize_section(page, heading)`

This prevents:

* Whole-file overwrites
* Accidental markdown corruption

---

## 5️⃣ Attachments & Assets

StillPoint has attachments — MCP should understand that.

* `list_attachments(page)`
* `add_attachment(page, file_path)`
* `remove_attachment(page, attachment_name)`
* `open_attachment(path)` *(read-only)*

Optional:

* `get_attachment_metadata(path)`
  (type, size, last used)

---

## 6️⃣ Workspace & Context Tools (Agent Control)

These are subtle but **very powerful**.

* `get_workspace_root()`
* `get_active_page()`
* `set_active_page(page)`
* `get_selected_text()` *(if editor-integrated)*
* `replace_selected_text(content)`

This enables:

> “Rewrite this paragraph in a more concise tone.”

---

## 7️⃣ Automation & Batch Operations

Where MCP starts to feel *agentic*.

* `apply_template(page, template_name)`
* `bulk_rename(pattern, replacement)`
* `bulk_update(frontmatter | headings)`
* `generate_index_page(folder)`
* `cleanup_orphan_pages()`

---

## 8️⃣ Guardrails (Highly Recommended)

These aren’t tools — they’re **policies baked into the MCP server**.

* **Workspace sandboxing**

  * No paths outside StillPoint root
* **Dry-run mode**

  * `preview_changes=true`
* **Operation limits**

  * Max files per request
* **Read-only mode toggle**

---

## 9️⃣ What I Would *Not* Add (Yet)

Avoid early:

* Arbitrary shell execution
* Network access
* Git commands (until later)
* OS-level process control

Those belong in **separate MCP servers**.

---

## 🔟 Suggested MCP Tool Categories (for LM Studio / Agents)

Name them clearly:

```
filesystem.*
pages.*
search.*
graph.*
markdown.*
attachments.*
workspace.*
automation.*
```

LLMs respond *much better* when tools are semantically grouped.

---

## 🧭 Minimal “Perfect v1” Tool Set

If you want a **tight, disciplined v1**, I’d ship:

**Must-have**

* read/write file
* list/create folders
* read/write page
* search text
* get backlinks
* list headings
* insert under heading

**Then grow from there.**

---

If you want, next I can:

* Propose an **MCP schema JSON** for these tools
* Help you **split this into multiple MCP servers**
* Design a **“StillPoint Agent” prompt** that uses these tools correctly
* Map tools → concrete StillPoint Python functions

This is a really solid direction for StillPoint — you’re building the right abstraction layer.
