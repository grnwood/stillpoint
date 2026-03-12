# StillPoint Vault Workspace Guide

This folder uses StillPoint's folder-backed page structure.

## Page Layout

Pages are represented by folders with one markdown file whose name matches the folder.

Prefer breaking broad subjects into subpages when that is clearer than writing one large monolithic page.

Example topic hierarchy:

```text
Topic/Topic.md
Topic/SubTopic/SubTopic.md
Topic/SubTopic/notes.png
```

Use subpages when:

- a topic has multiple distinct sections that should stand on their own
- a subject is likely to grow over time
- attachments naturally belong to one subsection rather than the whole parent topic

Prefer one larger page only when the content is short and tightly related.

Valid examples:

```text
Ideas/Ideas.md
Projects/Alpha/Alpha.md
Meetings/Weekly/Weekly.md
Topic/Topic.md
Topic/SubTopic/SubTopic.md
```

Root-level shorthand is also acceptable:

```text
SprintReview.md
```

That should be treated as a page named `SprintReview`.

## Page Naming Rules

- Inside a folder, the markdown filename must match the folder name.
- Use UTF-8 markdown for page files.
- Prefer clear, human-readable names.
- Do not include quote characters in file or folder names.
- Do not generate shell-escaped path names.
- Spaces are allowed, but the actual filesystem name should be literal text, not quoted text.
- Prefer letters, numbers, spaces, hyphens, and underscores in names.
- Do not use `index.md`.
- Do not create arbitrary markdown files inside a folder with a different basename.

Valid:

```text
Clients/Acme/Acme.md
Docker Page/Docker Page.md
Project-Alpha/Project-Alpha.md
```

Invalid:

```text
Clients/Acme/notes.md
Clients/index.md
scratch/output.md
'Docker Page'/'Docker Page'.md
"Docker Page"/"Docker Page".md
Docker\ Page/Docker\ Page.md
```

## Attachments

Attachments belong beside their page file.

Valid:

```text
Clients/Acme/Acme.md
Clients/Acme/logo.png
Clients/Acme/meeting-notes.pdf
```

Guidance:

- Put images, PDFs, CSVs, and generated artifacts in the same folder as the owning page.
- Keep attachments near the page they describe.
- Avoid loose attachments in folders that do not clearly belong to a single page.

## Linking

StillPoint page links should use colon links based on the page path relative to the vault root.

Rules:

- Prefer explicit StillPoint links in the form `[:Page|Label]` or `[:Parent:Child|Label]`.
- Build the colon path from the folder-backed page path, not from a raw markdown filename.
- Treat the vault root as the starting point for links.
- Drop the `.md` suffix in links.
- For a folder-backed page like `Topic/SubTopic/SubTopic.md`, link to it as `[:Topic:SubTopic|SubTopic]`.
- For a root-level shorthand page like `DailyPlan.md`, link to it as `[:DailyPlan|DailyPlan]`.
- Use normal markdown links only for external URLs.
- Do not write links to local filesystem paths like `Topic/SubTopic/SubTopic.md`.
- Do not emit bare colon links when a visible label is intended.

Valid page link examples:

```text
[:Ideas|Ideas]
[:Projects:Alpha|Alpha]
[:Docker:Compose|Compose]
[:Poems:Night_Window|Night_Window]
```

Invalid page link examples:

```text
[::Poems:Night Window]
[:Poems:Night Window]
[Poems:Night Window]
[Ideas](Ideas/Ideas.md)
[Alpha](Projects/Alpha/Alpha.md)
[Compose](Docker/Compose/Compose.md)
[DailyPlan](DailyPlan.md)
```

When linking from one page to another, think in terms of the page's location in the vault tree:

- `Clients/Acme/Acme.md` becomes `[:Clients:Acme|Acme]`
- `Topic/SubTopic/SubTopic.md` becomes `[:Topic:SubTopic|SubTopic]`
- `SprintReview.md` becomes `[:SprintReview|SprintReview]`

## Recommended Content Creation Pattern

When creating new content:

1. Create a folder for the page if needed.
2. Create `<FolderName>/<FolderName>.md`.
3. Put related attachments in that same folder.
4. If the topic is broad, split it into child folders and child pages instead of one oversized file.
5. Prefer additive changes over large destructive rewrites.

Good examples:

```text
Research/PaperNotes/PaperNotes.md
Research/PaperNotes/source.pdf

Ideas/Ideas.md
Ideas/sketch.png

Docker/Docker.md
Docker/Compose/Compose.md
Docker/Compose/install-notes.txt

DailyPlan.md
```

## What To Avoid

- `index.md`
- mismatched page filenames
- writing markdown into arbitrary helper folders
- scattering attachments without a clear owning page

If unsure, use this pattern:

```text
Topic/Topic.md
Topic/attachment.ext
```

If the topic becomes large, expand it like this:

```text
Topic/Topic.md
Topic/SubTopic/SubTopic.md
Topic/SubTopic/attachment.ext
```

## StillPoint-Specific Rules

- Treat the folder as the page identity, not just the markdown file.
- If renaming a page, rename the folder and the matching markdown file together.
- Do not create stray markdown files inside a page folder. If content deserves its own page, create a child folder-backed page.
- Prefer updating the existing page for a topic instead of creating near-duplicate alternates unless explicitly asked.
- When a page becomes too large, split it into subpages and leave useful links from the parent page.
- When creating a new page, add links from relevant parent or related pages when that improves navigation.
- Use UTF-8 text for markdown content.
- Do not write absolute filesystem paths into page content.
- When referring to attachments from markdown, use paths relative to the current page folder.
- Use StillPoint colon links for page-to-page links and normal markdown links only for external URLs.
- Do not create, modify, or depend on `.stillpoint/` contents.
- Do not write app metadata, lock files, database files, sync state, or other internal files.
- Do not invent journal or date-based structures unless explicitly asked to create them.
- If creating tasks, use the task syntax already present in the vault instead of inventing a new format.
- Prefer additive, minimally destructive edits unless the user explicitly asks for broad rewrites or deletions.
