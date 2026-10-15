# {{name}}: conventions for the AI assistant

Scenes are authored in GameStudio (`scenes/*.blend`); gameplay is TypeScript in `src/`.

**Before using any engine API, read `node_modules/@gamestudio/engine/API.md`.** It is the complete API; never invent functions, fields or options it does not list.

| Path | Contents |
|---|---|
| `src/components/*.ts` | `defineComponent(...)` only: data, no logic. Import from `@gamestudio/engine/schema`. |
| `src/systems/*.ts` | One `defineSystem(...)` per file, default export. Behaviour lives here. Import from `@gamestudio/engine`. |
| `src/shaders/*.ts` | Game shaders: one `defineShader(...)` per file, for Blender materials that move or react in the game. Never look materials up by name in code. |
| `src/main.ts` | Entry point. Rarely needs edits. |
| `gamestudio.json` | Project config. |
| `.gamestudio/` | Generated. Never edit by hand. |

Naming: components are PascalCase nouns in kebab-case files; systems end in `System`.
