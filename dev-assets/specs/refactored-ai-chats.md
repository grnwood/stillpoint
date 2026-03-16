
# Refactor AI Chats

Currently the AI chat panel shadows the vault root into the chat panel.
This is buggy because it causes additional full scans of the vault root, and I do not want that.

This has also shown that the "chat per vault page" model no longer makes sense now that I can use CLI and TUI AI tools outside of the vault to do page work.

## Refactor to remove

- The concept of an AI chat being tied to a file nav page.
- The concept of "global" vs "page level" chat in the AI chat window.
- Any vault or file-level scanning as part of AI panel initialization.
- Any logic that determines whether the current AI chat matches the file nav chat.
- The CTA related to matching the current file nav chat.

## Refactor to change

- Remove "global chat" concept and messaging. Chat is just chat.
- The AI toggle panel (binoculars) flyout should show AI chat folders/chats only, not the file nav hierarchy of page chats.
- The flyout should allow:
  - Creating a new folder.
  - Creating a new chat in a folder.
  - Renaming a folder.
  - Deleting a folder.
  - Renaming a chat.
- Folder/chat ordering can remain simple. Creation order is fine; no special ordering logic is required.
- Change the `+` and `-` font sizer controls to use the same `A+` and `A-` controls used in the pop page editor.
- Add a `New Chat` option at the top of the chat panel.
- The chat window should allow selecting a previous chat or creating a new one.
  - A new chat starts with no context attached.
  - A new chat can be named `New Chat` initially and can be renamed or auto-titled later.
  - The first assistant reply should trigger auto-title generation using the default model with a prompt like `summarize this chat in five or six words`.
  - If a chat is manually renamed, it is no longer eligible for title auto-summarizing.
- Attached things should only be visible in chat if the user explicitly added them to context.
- The `!`, `@`, and `#` context widgets should use the main app file-nav index to surface options (same general source used for things like `Ctrl-J` jump).
  - AI should reuse that existing main-app index rather than building or scanning its own.

## Data handling

- Existing old chats do not need migration.
- Old page/global chat data can be discarded as part of this refactor.

## Keep

- All chat window operations for messaging, streaming, and response handling.
- Reset chat.
- Condense chat.
- Chat model and server configuration panel.

## End goal

- The AI chat panel is independent of the file nav and vault hierarchy.
- The AI chat panel maintains its own chat folder hierarchy.
- The AI chat panel can bring any page, page tree, or attachment into its context when the user adds it.

## Non-goals

- The AI chat panel should not automatically mirror the vault structure.
- The AI chat panel should not automatically attach current page context to a new chat.
- The AI chat panel should not do its own vault scanning or indexing on init.
 
