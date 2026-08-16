# Tasks

## What Are Tasks?
![paste_image_001](./paste_image_001.png)

Tasks are checkboxes inside your notes.
- Keep planning and execution in the same place.
- No separate task app required.
- Every task stays connected to its original page context.

## Task Syntax
Fast entry:
- Type `()` then space to create a new task.
- Type `(x)` then space to create a completed task.
- Press `F12` on a task line to toggle done/undone.

Task line options:
- `@tag` for task categories (example: `@work`, `@email`)
- `>YYYY-MM-DD` for start date
- `<YYYY-MM-DD` for due date
- `!`, `!!`, `!!!` for priority emphasis

Example:
```markdown
# Project Tasks

- [ ] Research competitors @work !! >2026-02-10 <2026-02-14
- [x] Set up kickoff meeting @team
- [ ] Write report @writing ! <2026-02-20
```

- Use `Ctrl+D` to insert dates quickly from a date dialog.
- The date input supports natural language like `today`, `tomorrow`, and `next week`.

## Task Panel
![paste_image_002](./paste_image_002.png)

The Tasks panel shows all tasks from your vault.
- Activate a task to jump straight to the page and line.
- Double-click on the priority/checkbox column to toggle completion.
- Click or double-click date columns to update start/due dates quickly.
- Use the search box to filter tasks.
- While editing, use `Ctrl+\` to jump focus to Tasks quickly.
- Fast keyboard flow: `Ctrl+\` -> type search text -> arrow keys -> `Enter` to jump and focus editor.
- Use `Shift+Enter` to jump but keep focus in the task list.
- The same `Enter` / `Shift+Enter` behavior applies in both the main Tasks panel and the Calendar tab's task list.
- Open the Tasks panel in its own window (`View -> Open Task Panel Window`) to keep it on a second monitor.

## Keyboard Actions

When focus is in the task list:

- `Space`: complete or reopen selected tasks.
- `E` or `F2`: edit task text, status, priority, dates, tags, or destination.
- `D` / `S`: open the due/start date menu.
- `[` / `]`: move the due date one day; hold `Shift` to move one week.
- `P`: cycle priority.
- `T`: edit tags.
- `M`: open the full task editor with **Move/File to** focused.
- `Delete`: delete after confirmation.
- `Ctrl+Z`: safely undo the last Tasks-panel change when the affected pages have not changed.
- `?`: show the contextual shortcut reference.

The task editor opens beside the selected row and shows page locations with colon paths. Long task names wrap for readability, and the focused input has a vault-accent border. `Up` / `Down` cycle through fields. In vi mode, Task text begins in `NAV` with a vault-accent block cursor showing its position: use `h/j/k/l` or `w/b` to move, and `i` or `a` to enter `INSERT`. `Ctrl+Shift+J` / `Ctrl+Shift+K` cycle editor fields except while a tag, select, or **Move/File to** dropdown is active, when they navigate its choices. If the focused field has changed, the first `Esc` restores its value from when the editor opened and leaves the editor open; press `Esc` again to cancel the editor. In an unchanged Task text field, `Esc` leaves vi insert mode before it cancels the editor. `Ctrl+Enter` saves and advances, and `Ctrl+L` focuses the destination field. Date fields accept ISO dates, words such as `tomorrow` and `fri`, relative values such as `+3d`, and `clear`, or can be filled from the calendar button. Tags autocomplete from known vault-wide tags, and **Move/File to** searches the full page index even when Tasks is filtered. In the `D` and `S` date-option menus, vi mode supports `j/k` and `Ctrl+Shift+J/K`, with `Enter` to apply and `Esc` to cancel.

In the main Markdown editor, vi navigation mode shows a pencil button when the mouse is over a task. Select it to open the same Task Editor for that line, or press `e` to edit the task on the cursor line. The button is hidden in vi insert mode.

## Process Quick Captures

Choose **Tools > Process Quick Captures…** from any page to work through captures in the main editor. StillPoint opens each source page, highlights the complete capture, and keeps keyboard focus in a narrow processor immediately to the right of the editor. At the screen edge it uses the rightmost available position. Closing it restores your original page and cursor.

The default **Active sources** scope includes the configured capture page and journal pages within one week of the selected Calendar date. Other scopes cover only the configured page, only that Calendar range, or every Quick Capture page in the vault. The processor reports captures outside a bounded scope.

- Press `M` to focus the inline Move page picker. It searches the complete page index and moves the timestamp, body, attachments, and trailing horizontal rule together without opening another dialog.
- `Up` / `Down` navigate captures; vi mode also supports `j/k`.
- In the page picker, vi mode uses `Ctrl+Shift+J/K` to navigate suggestions and `Enter` to select one. Suggestions are never selected automatically, and `Esc` clears the search.
- `Ctrl+Z` safely undoes the most recent processing action.

There is no processed marker and no Keep, Delete, or Make Task action: a capture remains pending until it is moved.

## Filtering Tasks
- Type keywords to find specific tasks.
- Use tags like `@work` or `@personal`.
- Type `/@ ` on a task line to insert a task tag from the picker.
- Use start/due dates to narrow time-based work.

Example filters:
- `@work` - show work-related tasks
- `meeting` - show tasks mentioning meetings

## Task Date Formats
Use date markers directly in task lines:
- `>YYYY-MM-DD` = starts on/after date
- `<YYYY-MM-DD` = due by date
You can include both on the same task line.

Example:
```markdown
- [ ] Draft proposal >2026-02-10 <2026-02-14
```

## Actionable Tasks
The 'Show tasks you can act on now' toggle hides:
- Completed tasks
- Tasks with unfinished subtasks
- Tasks marked as non-actionable (like @wait)

## Organizing Tasks
- Group related tasks under headings.
- Use `@tags` for task categories (page tags use `#tags`).
- Create separate pages for big projects.

Example nested tags:
```markdown
# Launch Plan

Tags: #launch #marketing

- [ ] Write announcement @writing
  - [ ] Review copy @review
  - [ ] Schedule post @social
- [ ] Collect feedback @research
```

## Tips
- Keep tasks near the notes they relate to.
- Use the Task Panel daily to review and reschedule.
- Review your task list daily.
- Use dates for time-sensitive items.
- Keep priorities simple (`!`, `!!`, `!!!`) so high-signal items stand out.
- Print tasks when you need a clean review or shareable summary.
