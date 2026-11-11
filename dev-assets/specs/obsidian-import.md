# Obsidian Vault Import

## Purpose

Add **File → Import → Obsidian Vault…**, following the existing Zim import workflow. Import notes from an OS-level Obsidian vault folder into the active StillPoint vault, preserving folder structure, rewriting supported links, and copying referenced attachments into each owning page's folder.

Use `.md` for all imported pages, matching StillPoint's current page format. Do not convert pages to legacy `.txt` files. Keep the source vault unchanged.

The reference vault is [obsidian-sample/Sample Vault](obsidian-sample/Sample%20Vault).

## User workflow

1. Require an active, writable local StillPoint vault. Disable the action in remote mode, consistent with Zim import.
2. Open an OS folder picker for the source Obsidian vault. Version one imports a folder, not individual files.
3. Reuse the existing import destination picker to select a target folder in StillPoint.
4. Build the complete import plan before writing files: page destinations, attachment names, link resolutions, conflicts, and warnings.
5. Show planned page and attachment-copy counts and conflicts before starting. Skip existing destination pages; do not overwrite them or copy attachments into their folders.
6. Show page and attachment-copy progress while keeping the UI responsive.
7. Refresh the vault tree and show imported pages, attachment copies, skipped pages, failed operations, and warnings. Make warning details reviewable, including source note and unresolved target.
8. Offer to reindex using the existing reindex workflow. Do not automatically trigger a full index rebuild during import.

Cancelling source or destination selection must produce no writes. On failure after writing has started, report the partial result accurately and retain successfully imported pages.

## Page discovery and mapping

- Recursively discover Markdown notes, including case-insensitive `.md` extensions.
- Exclude `.obsidian`, `.git`, and `.trash` directories, including their attachments.
- Preserve original folder and note names, including spaces and case.
- Place the source vault's contents directly under the selected destination, without an extra source-vault-name folder.
- Each note becomes a page directory containing a same-named `.md` file. Add exactly one page directory, not two.

For a root destination:

| Obsidian source | StillPoint destination |
| --- | --- |
| `MainNote.md` | `MainNote/MainNote.md` |
| `New Folder/New Note.md` | `New Folder/New Note/New Note.md` |

For destination `/Imported`, the second example becomes `/Imported/New Folder/New Note/New Note.md`.

Build a source-to-destination map before converting content so forward references and cross-folder links resolve correctly. Detect destination conflicts, including case or name normalization collisions. Skip conflicting source notes and report them rather than silently choosing a winner.

An existing StillPoint page in either `.md` or legacy `.txt` format is an existing-page conflict. Skip and report it. Preserve links to skipped/conflicting notes and warn rather than silently pointing them to unrelated existing content.

Read and copy only files within the selected source vault; do not follow symlinks outside it. Reject source/destination overlap that could modify source content or cause scanning of import output.

## Content and links

Preserve ordinary Markdown formatting. Rewrite supported references into StillPoint's existing page-link and attachment-link syntax. Include the selected destination prefix in page targets.

Support these page references:

- Wiki links: `[[Internal Note]]`.
- Aliases: `[[Internal Note|Pretty Name]]`.
- Explicit paths, with optional extension: `[[Folder/Note]]`, `[[Folder/Note.md]]`.
- Markdown links: `[Pretty Name](Folder/Note.md)`.
- Note-relative `./` and `../` paths, vault-root paths, and URL-encoded paths such as `%20` for spaces.
- Heading fragments on resolved page links, retaining the fragment and display label. Preserve same-page heading references.

Resolve explicit vault-root and note-relative paths according to the reference form before falling back to a unique vault-wide basename match. Never pick the first matching basename. Preserve missing or ambiguous references unchanged and report warnings.

Resolve `.md` targets as notes before considering attachment handling. Keep page links, images, and ordinary file links distinct. Preserve external URLs unchanged; do not download remote content.

Do not rewrite inline code or fenced code blocks. Use Markdown-aware handling, including unclosed code fences, so example syntax remains intact.

### Obsidian-specific features in version one

Preserve frontmatter, callouts, extended task states, block references, and note transclusions as source text. Full Obsidian rendering parity is outside this version's scope. Warn where navigation or embedding cannot be retained, including block references and note transclusions. Image/file embeds are handled as attachments.

## Attachments

Support wiki attachment references and Markdown image/file links, including:

```markdown
![[Pasted image 20251216232514.png]]
![](002%20-%20Reference/ABC/BoysRoom/pasted_image004.png)
![Alt text](../images/photo.png)
[Download PDF](Documents/report.pdf)
[[Documents/report.pdf|Download PDF]]
```

- Resolve note-relative and vault-root paths, decode URL-encoded paths, and allow a unique vault-wide basename match for shorthand references. Nested notes must be able to reference images stored at the vault root.
- Preserve missing or ambiguous references unchanged and report warnings. Do not rewrite them to nonexistent local filenames.
- Copy referenced attachments alongside the destination page file, following StillPoint's current layout. An image used by `MainNote` is stored at `MainNote/image.png`.
- Rewrite references to the final page-local filename. Preserve image alt text and file-link labels. Non-image files such as PDFs remain file links.
- Assign attachment filenames compatible with StillPoint's literal-path readers: replace whitespace and Markdown/URL delimiter characters with underscores. For example, `Pasted image.png` becomes `Pasted_image.png`. Do not emit `%20` paths that the editor cannot open. Note and folder names remain unchanged.
- Copy shared source attachments into each owning page's folder. Count each page-local copy separately in progress and completion totals.
- Deduplicate repeated references to the same attachment within a page.
- Assign deterministic distinct filenames when different attachments share a basename within one destination page. Avoid collisions with the page filename too. Rewrite every reference to the assigned name.
- Preserve image sizing aliases where supported by StillPoint; otherwise retain the sizing information in source text and warn about unsupported behavior.
- Translate width-only image aliases such as `|300` into StillPoint's `{width=300}` syntax. Preserve two-dimensional sizing as source text with a warning. Retain Markdown image titles and warn that inline editor rendering does not support them.
- Do not copy unreferenced files, remote resources, excluded-directory contents, or files outside the source vault.

## Implementation plan

Complete the partial converter in `sp/app/obsidian_import.py`. Its current behavior is not the specification. In particular:

1. Fix the extra destination directory and build an explicit page/attachment plan with conflicts and warnings.
2. Replace first-match basename lookup with path-aware resolution and ambiguity reporting.
3. Add ordinary Markdown page/file link conversion; preserve unresolved syntax, aliases, and image alt text; protect code from rewriting.
4. Fix nested-to-root attachment lookup and represent assigned attachment filenames explicitly so copied files and rewritten references agree.
5. Wire the UI action, local-mode guards, destination picker, progress, tree refresh, completion report, and optional reindexing.
6. Add focused converter/integration checks and finish with a desktop sample-vault smoke test.

Keep planning/conversion separate from writes so it can be tested without opening the UI. Reuse existing write/attachment APIs and Zim import UI conventions where appropriate. Add the Obsidian action to the same local-mode and tooltip handling as Zim. Keep existing Zim import behavior unchanged.

## Acceptance checks

- Root and nested notes produce the exact `.md` layouts above, including imports below a non-root destination.
- The supplied sample imports its two notes and resolves the root-level image referenced from the nested note outside code. References inside code stay unchanged; missing sample targets produce warnings without losing source text.
- Fixtures cover wiki links, aliases, optional `.md` extensions, Markdown page links, heading fragments, relative paths, and URL encoding.
- Fixtures cover duplicate basenames, conflicting destinations, missing targets, and existing `.md`/`.txt` pages. No existing page is overwritten or ambiguous target chosen silently.
- Attachment checks cover nested-to-root lookup, encoded paths, alt text, PDFs, repeated references, shared attachments, and basename/page-filename collisions.
- External URLs, frontmatter, unsupported Obsidian syntax, inline code, and fenced code are preserved as specified.
- Excluded folders and paths escaping the source vault are not imported. Source files remain unchanged.
- UI checks cover menu availability, cancelling selection, empty sources, planning/read failures, partial write failures, progress, tree refresh, accurate totals, reviewable warnings, and optional reindexing.
- Run relevant existing page-link and attachment checks to catch regressions, then perform the desktop sample-vault smoke test.
