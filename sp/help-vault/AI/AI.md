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
