# Documentation maintenance

The user manual lives in `docs/manual/README.md` and the ten numbered chapter files. `docs/README.md` is the entry point for all documentation. The root README links to both the Markdown manual and the offline HTML edition.

## Rebuild and validate

From the repository root:

```sh
python -m pip install -r tools/manual-requirements.txt
python tools/build_manual.py
python tools/build_manual.py --check
```

The builder uses Markdown source, local images and inline styles. It does not fetch fonts, scripts or images from the network. The result, `docs/manual.html`, contains the whole manual and its image data, so it can be copied as a standalone reading artifact. Links to full technical references need the surrounding repository.

`--check` is read-only. It validates local links and Markdown anchors, JSON examples, alternative text, the image inventory, basic privacy patterns and whether the HTML matches its sources. This is a documentation check, not a test of the add-on's runtime behavior.

## Screenshot provenance and privacy

The manual reuses real repository screenshots of the demonstration project, templates, shader gallery and procedural examples. It does not use generated mockups of the interface. Editor screenshots may predate small layout changes, so verify current instructions against the implementation and caption differences where they affect a task.

Every included image was visually reviewed, and its SHA-256 digest is recorded in `screenshots.json`. The build rejects a changed or new image until it is reviewed and its inventory entry is updated. Image metadata is also checked; the approved PNGs contain only ordinary color and resolution metadata or no metadata.

The launcher and Scene Project captures that expose an absolute local project path are intentionally not referenced by the manual. A Blender valley render was also excluded because its PNG text metadata includes a local source path. The selected images show neutral example content. Example object IDs and loopback addresses are application data, not user identities or access credentials.

Before adding or replacing an image:

1. Use a neutral demonstration project and an isolated application profile.
2. Capture only relevant application content. Avoid desktop notifications, unrelated windows, file pickers, account panels and recent-project lists.
3. Inspect the full image at readable resolution, including titles, status bars, console messages and expanded properties.
4. Check for names, contact details, account identifiers, personal directory paths, credentials and session data.
5. Inspect PNG/EXIF metadata as well as visible content. Do not rely on blur or a text scan to establish privacy.
6. If the image contains private details, recapture from a clean context or omit it.
7. Add a descriptive caption and alternative text, then record the reviewed file's repository-relative path and digest in the inventory.
8. Rebuild, open the HTML and inspect the relevant chapter at desktop and narrow widths.

The existing `tools/screenshots.py` can capture the extension UI with a scratch profile and copied demo project. Its output is not automatically safe to publish: project paths can still appear in panels. Review each image before adding it to the approved inventory.

The text checks flag common absolute paths, email addresses, credential-shaped values and the current local account name without printing that name. They supplement human review rather than proving the absence of every possible identifier.

## Keep instructions accurate

Use these implementation sources when changing related chapters:

| Topic | Primary repository source |
|---|---|
| Version, minimum Blender and declared platforms | `blender/gamestudio/blender_manifest.toml` |
| Menus and shortcuts | `blender/gamestudio/menus.py`, `keymap.py`, `ui_topbar.py` |
| Preferences | `blender/gamestudio/prefs.py` |
| New Project and templates | `blender/gamestudio/projects.py`, `project_templates/`, `docs/TEMPLATES.md` |
| Components and systems | `runtime/packages/engine/API.md`, `src/schema/`, `blender/gamestudio/script_templates/` |
| UI authoring | `blender/gamestudio/gameui/`, `docs/GAME-UI.md` |
| Agent integration | `docs/MCP.md`, `blender/gamestudio/bridge/`, `claude_code.py`, `codex.py` |
| Build controls | `blender/gamestudio/editors/project.py`, project template `package.json` |

Keep implementation-specific requirements tied to the documented extension version. Do not turn a manifest's platform list into a claim that every platform has been tested. Do not copy historical test counts into new validation results.

## Review the reading artifact

Open `docs/manual.html` directly in a browser. Check internal chapter links, section links, readable tables, loaded images and narrow-screen navigation. Use Print to review page breaks when the content changes significantly. The source Markdown remains the editable version; regenerate the HTML rather than editing it by hand.
