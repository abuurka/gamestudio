# SPDX-License-Identifier: GPL-3.0-or-later
"""The Assistant sidebar tab, in the 3D View and the Text Editor.

The draw_* functions take a layout, so the Assistant editor (editors/assistant.py)
draws the same UI in a full-height region.
"""

import os
import textwrap

import bpy

from ..prefs import get_prefs
from . import deps, proposals
from .agent import SESSION
from .ops import install_status
from .proposals import STORE

RISK_ICON = {"low": "CHECKMARK", "medium": "INFO", "high": "ERROR"}
KIND_ICON = {"scene": "SCENE_DATA", "file": "FILE_SCRIPT"}
STATUS_ICON = {"pending": "TIME", "applied": "CHECKMARK", "rejected": "X",
               "failed": "CANCEL", "reverted": "LOOP_BACK"}
ROLE_ICON = {"user": "USER", "assistant": "LIGHT", "tool": "TOOL_SETTINGS", "proposal": "FILE_NEW",
             "system": "INFO", "error": "ERROR"}
ROLE_LABEL = {"user": "You", "assistant": "Assistant", "error": "Error"}
EXAMPLES = ("What does the Player object do?", "Add a coin above the crate",
            "Why does the game log this error?")
BOX_MARGIN = 30  # pixels a box and its padding take from the wrap width
TOOL_CHARS = 90


def draw_wrapped(layout, text: str, width_px: int, icon: str = "NONE") -> None:
    # Region width is in pixels; leave room for panel margins and scale glyph width with the UI.
    ui_scale = bpy.context.preferences.system.ui_scale or 1.0
    chars = max(16, int((width_px - 40) / (7 * ui_scale)))
    indent = "BLANK1" if icon != "NONE" else "NONE"
    col = layout.column(align=True)
    first = True
    for paragraph in text.splitlines() or [""]:
        for line in textwrap.wrap(paragraph, chars) or [""]:
            col.label(text=line, icon=icon if first else indent)
            first = False


def _muted(layout):
    col = layout.column(align=True)
    col.active = False
    return col


def _narrow(context) -> bool:
    ui_scale = bpy.context.preferences.system.ui_scale or 1.0
    return context.region.width < 420 * ui_scale


def _has_credentials(prefs) -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or prefs.api_key)


class _AssistantPanel:
    bl_region_type = "UI"
    bl_category = "Assistant"

    @classmethod
    def poll(cls, context):
        return True


def ready() -> bool:
    """True when the assistant is enabled and its SDK is installed."""
    return bool(get_prefs().assistant_enabled and deps.sdk_available())


def draw_setup(layout, context) -> bool:
    """Enable / install prompts and credential warnings. Returns True when the assistant can chat."""
    prefs = get_prefs()
    width = context.region.width
    if not prefs.assistant_enabled:
        layout.label(text="The AI assistant is off", icon="LIGHT")
        draw_wrapped(_muted(layout), "It is optional and only acts when you ask. Every change it suggests "
                                     "waits for your review.", width)
        row = layout.row()
        row.scale_y = 1.3
        row.operator("gamestudio.ai_enable", icon="CHECKMARK")
        return False
    if not deps.sdk_available():
        layout.label(text="AI support is not installed", icon="ERROR")
        draw_wrapped(_muted(layout), "The assistant needs the Anthropic Python SDK (about 15 MB).", width)
        row = layout.row()
        row.scale_y = 1.3
        row.operator("gamestudio.ai_install_sdk", icon="IMPORT")
        if install_status():
            draw_wrapped(layout, install_status(), width)
        return False
    if not bpy.app.online_access:
        draw_wrapped(layout, "Online access is off (Preferences > System).", width, icon="ERROR")
    if not _has_credentials(prefs):
        draw_wrapped(layout, "Set ANTHROPIC_API_KEY, or add a key in the add-on preferences.",
                     width, icon="KEYINGSET")
    return True


def draw_prompt(layout, context, lines: int = 3) -> None:
    """Role, mode, the prompt box, Send / Stop and the session status."""
    prefs = get_prefs()
    ui = context.window_manager.gs_ai
    row = layout.row(align=True)
    row.prop(ui, "role", text="")
    if _narrow(context):  # the sidebar: the mode buttons get a row of their own
        row.prop(ui, "teach", toggle=True, icon="HELP")
        layout.row(align=True).prop(ui, "mode", expand=True)
    else:
        row.prop(ui, "mode", expand=True)
        row.prop(ui, "teach", toggle=True, icon="HELP")

    if hasattr(layout, "textbox"):
        layout.textbox(ui, "prompt", initial_visible_lines=lines,
                       placeholder="Ask about your scene or code, or describe a change")
    else:
        layout.prop(ui, "prompt", text="")
    row = layout.row(align=True)
    row.scale_y = 1.3
    if SESSION.busy:
        row.operator("gamestudio.ai_cancel", icon="CANCEL")
    else:
        row.operator("gamestudio.ai_send", icon="PLAY")
    row.operator("gamestudio.ai_new_conversation", text="", icon="FILE_NEW")

    status = _muted(layout).row()
    status.label(text=SESSION.status, icon="SORTTIME" if SESSION.busy else "NONE")
    meta = status.row()
    meta.alignment = "RIGHT"
    meta.label(text=f"{prefs.model}  ${SESSION.cost:.3f}")


def draw_main(layout, context) -> None:
    if draw_setup(layout, context):
        draw_prompt(layout, context)


def _clip(text: str, max_chars: int) -> str:
    return text if len(text) < max_chars else text[:max_chars] + " ..."


def draw_message(layout, role: str, text: str, width: int) -> None:
    """One transcript entry: your messages and the assistant's in cards, the rest as quiet notes."""
    if role in ROLE_LABEL:
        box = layout.box()
        head = box.row()
        head.alert = role == "error"
        head.label(text=ROLE_LABEL[role], icon=ROLE_ICON[role])
        draw_wrapped(box, text, width - BOX_MARGIN)
        return
    note = _muted(layout)
    if role == "tool":
        first = text.strip().splitlines()[0] if text.strip() else ""
        note.label(text=_clip(first, TOOL_CHARS), icon=ROLE_ICON["tool"])
    else:
        draw_wrapped(note, text, width, icon=ROLE_ICON.get(role, "DOT"))


def draw_transcript(layout, context, entries: int = 10, max_chars: int = 1500) -> None:
    width = context.region.width
    shown = SESSION.transcript[-entries:]
    if not shown and not SESSION.live_text:
        layout.label(text="Ask anything about your game", icon="LIGHT")
        examples = _muted(layout)
        for example in EXAMPLES:
            examples.label(text=f'"{example}"', icon="BLANK1")
    hidden = len(SESSION.transcript) - len(shown)
    if hidden > 0:
        _muted(layout).label(text=f"{hidden} earlier message{'s' if hidden != 1 else ''} in the transcript",
                             icon="THREE_DOTS")
    for entry in shown:
        draw_message(layout, entry["role"], _clip(entry["text"], max_chars), width)
    if SESSION.live_text:
        box = layout.box()
        box.label(text="Assistant", icon="SORTTIME")
        draw_wrapped(box, SESSION.live_text[-max_chars:], width - BOX_MARGIN)
    layout.separator()
    row = layout.row(align=True)
    row.operator("gamestudio.ai_open_transcript", icon="TEXT")
    row.operator("gamestudio.ai_toggle_lock", text="Lock Selected", icon="LOCKED")


def _stale_reasons(p) -> list[str]:
    try:
        return proposals.stale_reasons(p)
    except proposals.ProposalError:  # no project to check against (yet): Apply reports why
        return []


def draw_proposal(layout, p, width: int) -> None:
    """A proposal card: what it changes, how risky it is, and Apply / Reject while it is pending."""
    box = layout.box()
    inner = width - BOX_MARGIN
    box.label(text=p.title, icon=KIND_ICON.get(p.kind, "DOT"))
    meta = _muted(box).row()
    kind = "File change" if p.kind == "file" else "Scene change"
    meta.label(text=f"{p.id}  {kind}{'  from MCP' if p.source == 'mcp' else ''}",
               icon="LINKED" if p.source == "mcp" else "BLANK1")
    chip = meta.row()
    chip.alignment = "RIGHT"
    chip.label(text=p.status.capitalize(), icon=STATUS_ICON.get(p.status, "DOT"))

    lines = p.summary_lines()
    col = box.column(align=True)
    for line in lines[:6]:
        draw_wrapped(col, line, inner, icon="DOT")
    if len(lines) > 6:
        _muted(col).label(text=f"... and {len(lines) - 6} more", icon="BLANK1")
    if p.risk != "low":
        warn = box.column()
        warn.alert = p.risk == "high"
        draw_wrapped(warn, f"{p.risk.capitalize()} risk: {'; '.join(p.risk_reasons)}", inner,
                     icon=RISK_ICON.get(p.risk, "ERROR"))

    if p.status == "pending":
        stale = _stale_reasons(p)
        if stale:
            warn = box.column()
            warn.alert = True
            draw_wrapped(warn, "Out of date: " + "; ".join(stale[:2]), inner, icon="TIME")
        row = box.row(align=True)
        row.scale_y = 1.3
        apply_op = "gamestudio.ai_apply" if p.kind == "scene" else "gamestudio.ai_apply_file"
        row.operator(apply_op, text="Apply", icon="CHECKMARK").proposal_id = p.id
        row.operator("gamestudio.ai_reject", text="Reject", icon="X").proposal_id = p.id
        row = box.row(align=True)
        row.operator("gamestudio.ai_preview", icon="HIDE_OFF").proposal_id = p.id
        row.operator("gamestudio.ai_details", icon="QUESTION").proposal_id = p.id
        row.operator("gamestudio.ai_explain", icon="HELP").proposal_id = p.id
    elif p.kind == "file" and p.status == "applied":
        box.operator("gamestudio.ai_revert", icon="LOOP_BACK").proposal_id = p.id
    if p.message and p.status != "pending":
        draw_wrapped(_muted(box), p.message, inner)


def draw_proposals(layout, context, limit: int = 12) -> None:
    ui = context.window_manager.gs_ai
    width = context.region.width
    items = STORE.all()
    shown = [p for p in reversed(items) if p.status == "pending"
             or ui.show_resolved or (p.kind == "file" and p.status == "applied")]
    if not shown:
        _muted(layout).label(text="Nothing to review. Changes the assistant suggests appear here.",
                             icon="CHECKMARK")
    for p in shown[:limit]:
        draw_proposal(layout, p, width)
    row = layout.row(align=True)
    row.prop(ui, "show_resolved")
    row.operator("gamestudio.ai_clear_resolved", text="", icon="TRASH")


def _draw_main(self, context):
    draw_main(self.layout, context)


def _draw_transcript(self, context):
    draw_transcript(self.layout, context)


def _draw_proposals(self, context):
    draw_proposals(self.layout, context)


def _make_panels(space: str, suffix: str):
    main = type(f"GS_PT_ai_main_{suffix}", (_AssistantPanel, bpy.types.Panel), {
        "bl_space_type": space, "bl_label": "AI Assistant", "draw": _draw_main,
        "bl_idname": f"GS_PT_ai_main_{suffix}"})
    transcript = type(f"GS_PT_ai_transcript_{suffix}", (_AssistantPanel, bpy.types.Panel), {
        "bl_space_type": space, "bl_label": "Conversation", "draw": _draw_transcript,
        "bl_idname": f"GS_PT_ai_transcript_{suffix}", "bl_parent_id": main.bl_idname,
        "poll": classmethod(lambda cls, ctx: ready())})
    props = type(f"GS_PT_ai_proposals_{suffix}", (_AssistantPanel, bpy.types.Panel), {
        "bl_space_type": space, "bl_label": "Proposals", "draw": _draw_proposals,
        "bl_idname": f"GS_PT_ai_proposals_{suffix}", "bl_parent_id": main.bl_idname,
        "poll": classmethod(lambda cls, ctx: ready())})
    return (main, transcript, props)


CLASSES = _make_panels("VIEW_3D", "view3d") + _make_panels("TEXT_EDITOR", "text")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
