# SPDX-License-Identifier: GPL-3.0-or-later
"""The Assistant editor (GAMESTUDIO_ASSISTANT): the AI chat in a full-height region.

It draws with assistant/ui.py's functions, sized for an editor: a taller
transcript and a bigger prompt box than the sidebar tab.
"""

from .. import host
from ..assistant import ui as assistant_ui
from ..assistant.agent import SESSION
from . import common

panel = host.editors.panel

PROMPT_LINES = 6
MAX_ENTRIES = 60
MAX_CHARS = 6000


def _ready(context) -> bool:
    return assistant_ui.ready()


def draw_conversation(layout, context) -> None:
    if not assistant_ui.draw_setup(layout, context):
        return
    entries = common.visible_rows(context, minimum=10, maximum=MAX_ENTRIES, row_px=60.0)
    assistant_ui.draw_transcript(layout, context, entries=entries, max_chars=MAX_CHARS)


def draw_prompt(layout, context) -> None:
    assistant_ui.draw_prompt(layout, context, lines=PROMPT_LINES)


def draw_proposals(layout, context) -> None:
    assistant_ui.draw_proposals(layout, context, limit=30)


def draw_header(layout, context) -> None:
    layout.separator_spacer()
    if assistant_ui.ready():
        pending = len(assistant_ui.STORE.pending())
        if pending:
            layout.label(text=f"{pending} to review", icon="TIME")
        layout.label(text=SESSION.status, icon="SORTTIME" if SESSION.busy else "NONE")
        layout.operator("gamestudio.ai_new_conversation", text="", icon="FILE_NEW")


class AssistantEditor:
    idname = "GAMESTUDIO_ASSISTANT"
    label = "Assistant"
    icon = "LIGHT"
    description = "Chat with the AI assistant; review and apply its proposals"
    # On stock Blender the Assistant sidebar tab (assistant/ui.py) already is this UI.
    sidebar_fallback = False
    panels = [
        panel("conversation", "Conversation", draw_conversation),
        panel("proposals", "Proposals", draw_proposals, poll=_ready),
        panel("prompt", "Ask", draw_prompt, poll=_ready),
    ]
    header_draw = staticmethod(draw_header)


def register():
    host.editors.register(AssistantEditor)


def unregister():
    host.editors.unregister(AssistantEditor)
