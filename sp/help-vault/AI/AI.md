# AI

## What AI Does in StillPoint
AI in StillPoint helps you think and write faster inside your vault.
- Ask questions about a page or folder.
- Summarize notes into clean takeaways.
- Turn rough notes into outlines or drafts.
- Use agents to search, read, and write pages for you.

## Turn AI On
Go to `Edit -> Preferences -> AI Chats and Agents`.
- **Enable AI Chats**: turns on chat and AI actions.
- **Enable AI chat in Folder Navigator**: shows chat while browsing a folder outside the vault. Restart Folder Navigator after changing it.
- **Manage Servers**: add/edit model endpoints.
- **Default Server** and **Default Model**: set your normal chat target.
- **Enable AI Agents in chat**: allows multi-step tool use (agent loops).

## Local LLMs vs Public APIs
You can use either, or both.

### Local LLM (LM Studio and OpenAI-compatible local servers)
Best when you want privacy, offline/local processing, or no per-token cloud billing.
- Typical local endpoint: `[http://localhost:1234`|] (LM Studio).
- StillPoint includes local-friendly defaults.
- Usually no public API key is needed.
![paste_image_001](./paste_image_001.png)
[https://lmstudio.ai/|]

![paste_image_002](./paste_image_002.png)
[https://docs.ollama.com/quickstart|]


### Public/Hosted LLM (API key services)
Best when you want stronger models without running them locally.
- Add a server with your provider base URL.
- Add your API key (or custom auth header if your provider requires it).
- Use **Verify** and **Refresh Models** to confirm setup.

## Server Setup (Simple)
In **Manage Servers**, each server profile defines:
- **Name**: how it appears in StillPoint.
- **Base URL**: model API endpoint.
- **Auth Mode / keys / headers**: how requests are authorized.
- **Models Path / Chat Path**: API routes (defaults usually work for OpenAI-compatible APIs).
- **Default Model**: model selected first for that server.

If your server verifies successfully and models load, you are ready to chat.

## Using AI Chats
- Open the AI chat tab and ask normal questions.
- Use page-specific chat for page-focused work.
- Use global chat for broader vault-level discussion.
- AI actions are available from the command bar (`Ctrl+Shift+P`) under AI commands.

## Adding Vault Context
- **+ Page** attaches the page currently open in the editor to this chat. A new chat opened from a page attaches that page automatically.
- `@ ` adds another page, `# ` adds a page folder, and `! ` adds an attachment. The context label shows what is attached; click it to remove an item.
- StillPoint reads selected context when you send a message. It includes unsaved changes from the current editor page. It no longer builds a local vector index for chat context.
- A page is sent whole when it fits. Long pages and folders have a size limit, and the chat request says when content is shortened. With agent tools enabled, folder context gives the agent a file list so it can read relevant pages through the vault API.
- Selected context is sent to your configured AI model server, whether that server runs locally or remotely. Review the attached items before sending sensitive content to a remote provider.
- For PNG, JPEG, and WebP attachments, StillPoint sends the image itself when the selected chat model accepts image input. If the server rejects image input, it retries with extracted OCR text. Other image formats use OCR text only. An image with no readable text cannot be described by a text-only model.
- A request can include up to four vision images and 20 MiB of encoded image data. If the selection exceeds either limit, remove images from chat context before sending.
- The Tasks AI chat reads the current task list directly; generating a task summary no longer has to build a vector index first.

## Chat in Folder Navigator
- When enabled in Preferences, Folder Navigator shows chat beside the file editor and uses the same configured model servers.
- `@ ` selects a file, `# ` selects a folder of readable files, and `! ` selects an image. You can also right-click a file or folder and choose **Add to AI Chat Context**. Images inside a selected folder are included only when you add them explicitly.
- The picker uses that folder's filename catalog. File contents are read when you send, including unsaved text in an open editor. Long content is shortened to fit the request.
- Wait for the folder index to finish before sending `# ` folder context. If indexing stops at a partial result, use **Continue Full Index** or attach individual files.
- Chats are saved separately for each folder under `~/.stillpoint/folder-chats/`. External files and image bytes are not copied into that chat database. Agent tools remain unavailable in Folder Navigator because vault tools operate on vault paths.
- To keep a chat in a vault, right-click it in the chat list and choose **Promote to Vault Chat**. Select a local vault. StillPoint copies the transcript and snapshots of the selected files into the vault. The promoted chat uses those copies as its context; the original folder chat remains available.

## What Agent Loops Are
An agent loop is a multi-step AI run:
1. The model plans the next step.
2. It calls a tool (for example search/read/write/task helpers).
3. It reads the tool result.
4. It repeats until it can return a final answer.

This is how the assistant can do real vault work, not just single-response text.

## Agent Loops in Your Vault
When agents are enabled, chat can use tools like:
- vault search/read to gather context
- vault write/append to create or update pages
- task/date helpers for planning workflows

On first use, StillPoint asks for vault-level approval before tools are allowed.

Practical prompts:
- "Search my vault for release notes and create a summary page."
- "Find open tasks tagged @work and write a weekly plan page."
- "Open today journal context and draft tomorrow priorities."

## Safety and Control
- Review generated writes before relying on them.
- Keep agents enabled only when you want tool-based automation.
- If needed, disable AI Chats or AI Agents in Preferences.
- For sensitive data, prefer local models and local endpoints.

## Friendly Starting Path
1. Enable AI Chats.
2. Add LM Studio first (quick local success path).
3. Verify server and pick a default model.
4. Try normal chat prompts.
5. Enable AI Agents and try one small write task in a test page.
