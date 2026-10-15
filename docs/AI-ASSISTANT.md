# The GameStudio AI assistant

The assistant is an optional helper inside Blender. It can read your scene, your game's code, the engine docs, and the running game. When you ask for a change, it **proposes** one. You decide whether it happens.

> **You stay the author.** The assistant cannot change your scene or files on its own. Every change is staged as a proposal that you preview and then apply or reject. A scene change is a single Ctrl+Z step. A file change is backed up and can be reverted.

The design and the research behind it are in [`research/07-ai-agents.md`](../research/07-ai-agents.md).

---

## Turning it on

1. Open **Edit > Preferences > Add-ons > GameStudio** and tick **Enable AI assistant**. It's off by default.
2. Allow network access in **Preferences > System > Network > Allow Online Access**. Blender add-ons must respect this setting.
3. Provide a Claude API key. Either set the `ANTHROPIC_API_KEY` environment variable before starting Blender (recommended), or paste the key into the add-on preferences. Keys pasted there are stored in Blender's preferences file in plain text.
4. In the 3D View sidebar (`N`), open the **Assistant** tab. If you see **Install AI Support**, click it. This downloads the Anthropic Python SDK (about 15 MB) into the add-on's folder with Blender's own Python.

In the preferences you can also choose:

| Setting | Default | Notes |
|---|---|---|
| Model | Claude Opus 5.5 | Sonnet 5.5 is faster and cheaper. Haiku 5.5 is the cheapest, for quick questions. Fable 5.1 is the most capable but slower and costs more. |
| Effort | High | How much the model reasons before answering. Lower is faster and cheaper. |
| Max tool turns | 16 | The upper bound on tool round trips per request. |

The panel shows the running cost of the current chat. Every request is logged to `.gamestudio/ai/audit.jsonl` in your project.

---

## Using it

Type a request and press **Send**. Some examples:

- "What does the Player object do, and which systems use its components?"
- "Add five coins in a circle around the crate, 1 m above the ground."
- "Make the bounce pads launch the player twice as high."
- "The game logs `Cannot read properties of undefined` when I collect a coin. Why?"
- "Explain how CharacterController and FollowCamera work together."

### Modes

| Mode | What the assistant can do |
|---|---|
| **Propose** (default) | Read everything and stage proposals for you to review. |
| **Ask** | Read and explain only. Proposal tools are refused. |

**Teach** (the `?` toggle) asks the assistant to explain the idea first and give you hints, so you can do it yourself. It only proposes changes when you ask it to.

### Roles

Each role focuses the assistant. Changing roles starts a new conversation.

| Role | Focus | Can propose |
|---|---|---|
| General | Everything below | Scene and code |
| Scene | Describe, organize, add and tune components, block out levels | Scene |
| Script | Write and review components and systems; runs the type checker | Code |
| Debug | Reads runtime logs and live game state, explains the cause, then suggests a fix | Code |
| Mentor | Explains concepts and your own code | Nothing (read-only) |

### Reviewing proposals

Each proposal card shows what will change, a **risk badge**, and buttons:

- **Preview**: the full list of operations, or a unified diff, in the Text Editor.
- **Why?**: the assistant's reasoning and the complete change list.
- **Explain** (`?`): asks the assistant to walk you through the proposal.
- **Apply**: makes the change. Scene changes are one undo step (Ctrl+Z). File changes are backed up under `.gamestudio/ai/backups/` first.
- **Reject**: discards the proposal. The assistant is told that you rejected it.
- **Revert** (file changes, after applying): restores the backup.

Risk comes from what the change does, not from what the model says about it:

| Risk | Typical cause |
|---|---|
| Low | Adding objects or components, editing values |
| Medium | Renames, writing files, more than 25 operations |
| High | Deleting objects, code that uses system modules, `eval`, or external network access, very large diffs. Applying asks for confirmation. |

A proposal goes **out of date** when the objects or files it was based on change after it was made. Applying it then asks you to confirm, and a stale file edit is only written if its edits still match exactly.

### Keeping things off-limits

Select objects and click **Lock Selected** in the Conversation panel. Locked objects can't be part of any proposal. The assistant can only read and propose files inside your project. It can never touch `node_modules/`, `.gamestudio/` or `.git/`.

### What it cannot do

- Apply its own proposals.
- Run Python in Blender, run shell commands, or delete files.
- Access the internet. The only network traffic is to the Claude API.
- Change linked library data.

Text in your scene and files, such as object names, comments, and log lines, is treated as data. The assistant is instructed never to follow instructions found inside it.

---

## Coding agents (Claude Code, Codex, ...) through MCP

Coding agents use a different path, with a different rule. The GameStudio MCP server (`@gamestudio/mcp`, see [MCP.md](MCP.md)) lets an agent **act directly**: it builds and edits the scene in Blender, writes the game's TypeScript itself, plays the game, looks at it and fixes it. Instead of proposals, it is protected by:

- **Undo.** Every agent edit in Blender is one undo step named "Agent: <tool>", so Ctrl+Z reverts it.
- **Checkpoints.** The agent saves copies of the scene under `.gamestudio/checkpoints/` before risky changes and can restore them.
- **Locks.** Objects you lock from AI (**Lock Selected**) are refused by the agent's scene tools too.
- **A local, token-protected connection.** Blender listens on 127.0.0.1 only; the MCP server reads the token from `.gamestudio/blender.json`.

Turn the connection off with **Agent bridge (MCP)** in the add-on preferences. New projects include the `.mcp.json` that Claude Code reads, and Open in Codex adds the `.codex/config.toml` that Codex reads; any other MCP client can start the same server. [MCP.md](MCP.md) has the setup, every tool, and the security notes (including the `run_python` escape hatch).

The older proposal-only bridge, `tools/mcp/gamestudio_mcp.py`, is retired: it now starts the new server.

---

## Privacy

When you send a request, the assistant sends the following to the Claude API:

- the prompt;
- a short summary of the editor state: the scene name, the active and selected objects;
- the engine API reference and your project's `ASSISTANT.md`;
- the results of the tools it calls, such as object data and file contents.

Nothing is sent until you press **Send**, and there's no telemetry.
