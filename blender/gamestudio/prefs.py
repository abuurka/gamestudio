# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences shared by the core editor tools and the AI assistant."""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

ADDON_ID = __package__

MODEL_ITEMS = [
    ("claude-opus-5-5", "Claude Opus 5.5", "Default. Strong at code and multi-step edits"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5", "Faster and cheaper for everyday work"),
    ("claude-haiku-5-5", "Claude Haiku 5.5", "Fastest and cheapest; good for quick questions"),
    ("claude-fable-5-1", "Claude Fable 5.1", "Most capable; slower and more expensive"),
]

CODE_EDITOR_ITEMS = [
    ("AUTO", "Automatic", "VS Code if it is installed, else Cursor, else the system default"),
    ("VSCODE", "VS Code", "Open the project folder and the file at the line in Visual Studio Code"),
    ("CURSOR", "Cursor", "Open the project folder and the file at the line in Cursor"),
    ("SYSTEM", "System default", "The application the system opens the file with (no line numbers)"),
    ("CUSTOM", "Custom command", "Your own command line with {file} {line} {column} {project} placeholders"),
]

GAME_DISPLAY_ITEMS = [
    ("EMBEDDED", "Embedded", "Stream the game into a Blender area"),
    ("WINDOW", "Window", "Open the game in its own window"),
]

EFFORT_ITEMS = [
    ("low", "Low", "Quick answers, least thinking"),
    ("medium", "Medium", "Balanced"),
    ("high", "High", "More careful reasoning (recommended for code)"),
    ("xhigh", "Extra High", "Deeper reasoning for hard problems"),
    ("max", "Max", "Maximum reasoning; slowest"),
]


def _launcher_at_startup_changed(self, context):
    # The launcher replaces Blender's splash while this is on, so the two popups never stack.
    context.preferences.view.show_splash = not self.show_launcher_at_startup


def _sidebar_tab_changed(_self, _context):
    from .core.util import tag_redraw

    tag_redraw()  # the Game tab's panels poll the preference


def _scene_view_changed(_self, _context):
    from .core import sceneview

    sceneview.prefs_changed()


class GSPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    # --- Launcher -------------------------------------------------------------
    show_launcher_at_startup: BoolProperty(
        name="Show launcher at startup",
        description=("Open the GameStudio launcher (new project, examples, recent projects) when Blender starts "
                     "without a file, instead of Blender's splash screen"),
        default=False,
        update=_launcher_at_startup_changed,
    )
    welcomed: BoolProperty(
        name="Launcher shown once",
        description="The launcher opened after GameStudio was first enabled",
        default=False,
        options={"HIDDEN"},
    )

    # --- Interface ------------------------------------------------------------
    sidebar_tab: BoolProperty(
        name="Game tab in the 3D View sidebar",
        description=("Also show the Game tab in the 3D View sidebar (N): play controls, the selected object's "
                     "components, the project and the console. Components are always in Object properties"),
        default=False,
        update=_sidebar_tab_changed,
    )

    # --- Runtime / play -----------------------------------------------------
    game_display: EnumProperty(
        name="Game display",
        description=("Where Play shows the game: streamed into the 3D Viewport (it becomes the Game View "
                     "while the game runs), or in its own window. The play buttons in the top bar switch it"),
        items=GAME_DISPLAY_ITEMS,
        default="EMBEDDED",
    )
    game_idle_minutes: IntProperty(
        name="Close idle game after (minutes)",
        description=("Stop keeps the game streamed into Blender running, stopped, so the next Play starts at "
                     "once. After this many minutes without Play it closes, and its browser with it (the next "
                     "Play then takes a few seconds more). 0: keep it until the project changes"),
        default=15,
        min=0,
        max=24 * 60,
    )
    npm_command: StringProperty(
        name="npm command",
        description="Command used to start the project's dev server (runs '<npm> run dev')",
        default="npm",
    )
    auto_setup: BoolProperty(
        name="Set up automatically",
        description=("Download what game projects need when they need it (Node.js, the game packages and, without "
                     "Chrome or Edge, a game browser), without asking first. Off: GameStudio asks once per "
                     "session before it downloads anything"),
        default=False,
        update=lambda _self, _context: _setup_pref_changed(),
    )
    open_game_window: BoolProperty(
        name="Open game window on Play",
        description=("Open the running game in a browser window when Play is pressed (Window display, or when "
                     "the game cannot stream into Blender)"),
        default=True,
    )
    app_window: BoolProperty(
        name="Use app-style window",
        description="Open the game in a chromeless Edge/Chrome app window when one is installed",
        default=True,
    )
    live_geometry: BoolProperty(
        name="Live geometry reload",
        description="Re-export the scene when meshes or materials change while the game is running",
        default=True,
    )
    scene_view_fps: IntProperty(
        name="Scene view frame rate",
        description=("Frames a second the game draws for the Scene view (the Game toggle in a 3D Viewport's "
                     "header). Higher follows the view more smoothly and costs more"),
        default=30,
        min=5,
        max=60,
        update=_scene_view_changed,
    )
    scene_view_animate: BoolProperty(
        name="Animate game shaders in the Scene view",
        description=("Water, foam, swaying grass and other animated game shaders move in the Scene view. Off: "
                     "they hold still until Play"),
        default=True,
        update=_scene_view_changed,
    )

    # --- Code editor --------------------------------------------------------
    code_editor: EnumProperty(
        name="Code editor",
        description="Where scripts, errors and the project open for editing",
        items=CODE_EDITOR_ITEMS,
        default="AUTO",
    )
    code_editor_command: StringProperty(
        name="Command",
        description=("Command that opens a file in your editor. Placeholders: {file} {line} {column} {project}. "
                     "Quote paths that contain spaces"),
        default="",
    )

    # --- AI assistant -------------------------------------------------------
    assistant_enabled: BoolProperty(
        name="Enable AI assistant",
        description="Show the AI assistant panel. Off by default; the assistant only acts when you ask",
        default=False,
    )
    api_key: StringProperty(
        name="Anthropic API key",
        description=(
            "Used only if the ANTHROPIC_API_KEY environment variable is not set. "
            "Stored in Blender's user preferences in plain text"
        ),
        subtype="PASSWORD",
        default="",
    )
    model: EnumProperty(name="Model", items=MODEL_ITEMS, default="claude-opus-5-5")
    effort: EnumProperty(name="Effort", items=EFFORT_ITEMS, default="high")
    max_turns: IntProperty(
        name="Max tool turns",
        description="Upper bound on model/tool round trips per request",
        default=16,
        min=1,
        max=64,
    )

    # --- Agent bridge (MCP) -------------------------------------------------
    bridge_enabled: BoolProperty(
        name="Agent bridge (MCP)",
        description=("Let coding agents such as Claude Code and Codex drive this Blender through the GameStudio MCP "
                     "server, over a token-protected socket on 127.0.0.1. Their edits are undo steps"),
        default=True,
        update=lambda self, _context: _on_bridge_pref(),
    )

    # --- Coding agent (Claude Code, Codex) ----------------------------------
    coding_agent: EnumProperty(
        name="Coding agent",
        description=("The coding agent the launcher, the welcome and the top bar's agent button open. The "
                     "GameStudio menu has Open in Claude Code and Open in Codex either way"),
        items=(("CLAUDE_CODE", "Claude Code", "Anthropic's coding agent (a Claude subscription or Console account)"),
               ("CODEX", "Codex", "OpenAI's coding agent (a ChatGPT account or an OpenAI API key)")),
        default="CLAUDE_CODE",
        update=lambda self, _context: _on_agent_pref(),
    )
    claude_terminal_command: StringProperty(
        name="Terminal command",
        description=("Command that opens a terminal in the project running the coding agent. Placeholders: "
                     "{project} {agent} (the agent's program; {claude} works too) {script} (the launcher script "
                     "GameStudio writes, which starts the agent in the project with GameStudio's Node.js). Quote "
                     "paths that contain spaces. On macOS an app name such as iTerm or Warp also works. Empty: "
                     "Windows Terminal or Command Prompt on Windows, Terminal on macOS, the desktop's terminal on "
                     "Linux"),
        default="",
    )

    def draw(self, context):
        layout = self.layout
        from . import bridge, claude_code, codex, environment, keymap

        row = layout.row()
        row.label(text="GameStudio", icon="WINDOW")
        row.prop(self, "show_launcher_at_startup")
        row.operator("gamestudio.launcher", text="Open Launcher", icon="WINDOW")

        box = _section(layout, "Coding agent", "CONSOLE",
                       "The agent that builds your game: Claude Code or Codex. It runs in a terminal and works in "
                       "this Blender")
        row = box.row()
        row.prop(self, "coding_agent", expand=True)
        claude_code.draw_cli_status(box)
        codex.draw_cli_status(box)
        box.prop(self, "claude_terminal_command")
        if not self.claude_terminal_command.strip():
            label = claude_code.terminal_label()
            _hint(box, f"Empty: opens in {label}" if label else
                  "No terminal found. Example: kitty --directory {project} {agent}")

        box = _section(layout, "Agent bridge", "LINKED",
                       "How Claude Code and Codex reach Blender: 127.0.0.1 only, with a per-session token")
        box.prop(self, "bridge_enabled")
        _hint(box, f"{bridge.status_text()}. Every agent edit is one undo step")

        box = _section(layout, "Setup", "SCRIPT",
                       "Node.js, the game packages and a game browser: GameStudio sets them up the first time")
        environment.draw_node_status(box)
        environment.draw_job(box)
        box.prop(self, "auto_setup")
        _hint(box, f"Everything goes in {environment.folder_label()}")
        box.prop(self, "npm_command")

        box = _section(layout, "Play", "PLAY", "Where Play (F5) shows the game, and how edits reach it")
        row = box.row()
        row.prop(self, "game_display", expand=True)
        col = box.column()
        col.active = self.game_display == "WINDOW"  # Embedded uses them only when it cannot stream
        col.prop(self, "open_game_window")
        col.prop(self, "app_window")
        if self.game_display == "EMBEDDED":
            _hint(box, "Embedded: click the game to play it, Shift+Esc to give Blender the mouse back. "
                       "The window options apply when it cannot stream")
        row = box.row()
        row.active = self.game_display == "EMBEDDED"
        row.prop(self, "game_idle_minutes")
        box.prop(self, "live_geometry")
        row = box.row()
        row.prop(self, "scene_view_fps")
        row.prop(self, "scene_view_animate")

        box = _section(layout, "Code editor", "TEXT", "Where scripts, errors and the project open for editing")
        box.prop(self, "code_editor")
        if self.code_editor == "CUSTOM":
            box.prop(self, "code_editor_command")
        draw_code_editor_status(box, self)

        box = _section(layout, "Interface", "MENU_PANEL",
                       "Components are in Object properties; the project and levels in Scene properties")
        box.prop(self, "sidebar_tab")

        keymap.draw_preferences(layout, context)

        box = _section(layout, "AI assistant (optional)", "LIGHT",
                       "A chat panel in Blender that proposes changes for you to apply. Uses your API key")
        box.prop(self, "assistant_enabled")
        col = box.column()
        col.enabled = self.assistant_enabled
        col.prop(self, "api_key")
        col.prop(self, "model")
        col.prop(self, "effort")
        col.prop(self, "max_turns")
        if not bpy.app.online_access:
            col.label(text="Online access is disabled in Preferences > System.", icon="ERROR")


def _section(layout, title: str, icon: str, explanation: str):
    """A box with a title and a one-line explanation."""
    box = layout.box()
    box.label(text=title, icon=icon)
    _hint(box, explanation)
    return box


def _hint(layout, text: str) -> None:
    row = layout.row()
    row.active = False
    row.label(text=text)


def _on_bridge_pref() -> None:
    from . import bridge

    bridge.apply_preference()


def _on_agent_pref() -> None:
    from . import claude_code

    claude_code.opened(None)  # the bars name the newly chosen agent


def _setup_pref_changed() -> None:
    from . import environment

    environment.invalidate()


def short_program_path(path: str) -> str:
    """A program's path for display: under the home folder as ~\\..., elsewhere only its last two
    parts (no user name in screenshots, and short enough for the preferences)."""
    import os
    import pathlib

    from .claude_code import display_path

    shown = display_path(str(path))
    parts = pathlib.Path(path).parts
    if shown == str(path) and len(parts) > 3:
        shown = os.path.join("…", *parts[-2:])
    return shown


def draw_code_editor_status(layout, prefs) -> None:
    from .core import code_editor

    try:
        target = code_editor.resolve(prefs.code_editor, prefs.code_editor_command)
    except code_editor.CodeEditorError as exc:
        layout.label(text=str(exc), icon="ERROR")
        return
    if target.editor is not None:
        layout.label(text=f"Uses {target.editor.label}: {short_program_path(target.editor.executable)}",
                     icon="CHECKMARK")
    elif target.custom is not None:
        layout.label(text="Example: " + code_editor.EXAMPLE_COMMAND, icon="INFO")
    else:
        text = ("No VS Code or Cursor found: files open in their default application" if prefs.code_editor == "AUTO"
                else "Files open in their default application, without line numbers")
        layout.label(text=text, icon="INFO")


class _DefaultPrefs:
    """Fallback used when the add-on is imported without being enabled (e.g. tests)."""

    show_launcher_at_startup = False
    welcomed = True
    sidebar_tab = False
    npm_command = "npm"
    auto_setup = False
    game_display = "EMBEDDED"
    game_idle_minutes = 15
    open_game_window = True
    app_window = True
    live_geometry = True
    scene_view_fps = 30
    scene_view_animate = True
    code_editor = "AUTO"
    code_editor_command = ""
    assistant_enabled = True
    api_key = ""
    model = "claude-opus-5-5"
    effort = "high"
    max_turns = 16
    bridge_enabled = True
    coding_agent = "CLAUDE_CODE"
    claude_terminal_command = ""


_defaults = _DefaultPrefs()


def get_prefs():
    try:
        return bpy.context.preferences.addons[ADDON_ID].preferences
    except (KeyError, AttributeError):
        return _defaults


def sidebar_enabled() -> bool:
    """Whether the 3D View sidebar has the Game tab (the sidebar_tab preference; off without the prefs)."""
    try:
        return bool(getattr(get_prefs(), "sidebar_tab", False))
    except Exception:  # a panel poll must never raise
        return False


def register():
    bpy.utils.register_class(GSPreferences)


def unregister():
    bpy.utils.unregister_class(GSPreferences)
