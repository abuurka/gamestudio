@AGENTS.md

## Claude Code

- `.claude/settings.json` turns on the `gamestudio` server from `.mcp.json` and pre-approves the tools that inspect, build, play and look, and `npm run typecheck`, `build` and `registry`. The rest, such as `run_python`, `delete_objects` and `restore_checkpoint`, ask first. Personal permissions go in `.claude/settings.local.json`.
- Each skill is also a slash command, such as `/new-game-from-brief` or `/playtest-and-fix`.
