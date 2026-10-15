# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators for the assistant panel.

Apply is the only path from a proposal to a real change, and it only runs from
a click in the UI. Scene proposals are applied inside an operator with the UNDO
flag, so each one is a single Ctrl+Z step.
"""

import threading
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..prefs import ADDON_ID
from . import agent, audit, deps, proposals
from .agent import SESSION, AssistantError
from .proposals import STORE, ProposalError

ROLE_ITEMS = [
    ("general", "General", "Scenes, scripts and debugging"),
    ("scene", "Scene", "Describe, organize and set up the Blender scene"),
    ("script", "Script", "Write and review gameplay code"),
    ("debug", "Debug", "Find the cause of runtime errors and bugs"),
    ("mentor", "Mentor", "Explain and teach; never proposes changes"),
]
MODE_ITEMS = [
    ("propose", "Propose", "The assistant may stage changes for you to review and apply"),
    ("ask", "Ask", "Read-only: answers and explanations, no proposals"),
]


class GSAssistantUI(bpy.types.PropertyGroup):
    prompt: StringProperty(name="Prompt", description="Your request to the assistant", default="")
    role: EnumProperty(name="Role", items=ROLE_ITEMS, default="general")
    mode: EnumProperty(name="Mode", items=MODE_ITEMS, default="propose")
    teach: BoolProperty(name="Teach", description="Explain and give hints before proposing anything",
                        default=False)
    show_resolved: BoolProperty(name="Show resolved", description="Also list applied and rejected proposals",
                                default=False)


def _get_proposal(op, proposal_id):
    p = STORE.get(proposal_id)
    if p is None:
        op.report({"ERROR"}, f"Proposal {proposal_id} no longer exists.")
    return p


def _finish_decision(p, decision, note=""):
    STORE.record_decision(p, decision, note)
    proposals.remove_preview(p)
    audit.log(SESSION.project_root or _root(), "proposal_decision", id=p.id, decision=decision, note=note)
    SESSION.transcript.append({"role": "system", "text": f"{p.id} {decision}{': ' + note if note else ''}"})


def _root():
    from ..core import project
    return project.project_root()


class GS_OT_ai_send(bpy.types.Operator):
    bl_idname = "gamestudio.ai_send"
    bl_label = "Send"
    bl_description = "Send your request to the assistant"

    def execute(self, context):
        ui = context.window_manager.gs_ai
        try:
            SESSION.submit(ui.prompt, role=ui.role, mode=ui.mode, teach=ui.teach)
        except AssistantError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        ui.prompt = ""
        return {"FINISHED"}


class GS_OT_ai_cancel(bpy.types.Operator):
    bl_idname = "gamestudio.ai_cancel"
    bl_label = "Stop"
    bl_description = "Stop the assistant's current request"

    def execute(self, context):
        SESSION.cancel()
        return {"FINISHED"}


class GS_OT_ai_new_conversation(bpy.types.Operator):
    bl_idname = "gamestudio.ai_new_conversation"
    bl_label = "New Conversation"
    bl_description = "Forget the conversation so far (proposals stay in the list)"

    def execute(self, context):
        try:
            SESSION.new_conversation()
        except AssistantError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class _ProposalOp:
    proposal_id: StringProperty(options={"HIDDEN"})
    force: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=False)

    def invoke(self, context, event):
        p = STORE.get(self.proposal_id)
        if p is not None and p.status == "pending":
            reasons = proposals.stale_reasons(p)
            if reasons:
                self.force = True
                return context.window_manager.invoke_confirm(
                    self, event, title="Proposal is out of date",
                    message="Changed since it was proposed: " + "; ".join(reasons[:3]),
                    confirm_text="Apply anyway", icon="WARNING")
            if p.risk == "high":
                return context.window_manager.invoke_confirm(
                    self, event, title="High-risk change",
                    message="; ".join(p.risk_reasons) or "Review before applying",
                    confirm_text="Apply", icon="ERROR")
        return self.execute(context)


class GS_OT_ai_apply(_ProposalOp, bpy.types.Operator):
    bl_idname = "gamestudio.ai_apply"
    bl_label = "Apply AI Proposal"
    bl_description = "Apply this proposal to the scene (one undo step)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = _get_proposal(self, self.proposal_id)
        if p is None or p.kind != "scene" or p.status != "pending":
            return {"CANCELLED"}
        if proposals.stale_reasons(p) and not self.force:
            self.report({"WARNING"}, f"{p.id} is out of date; confirm to apply anyway.")
            return {"CANCELLED"}
        try:
            touched = proposals.apply_scene(p, context)
        except ProposalError as exc:
            # Validation runs before any change, so nothing was modified.
            p.status, p.message = "failed", str(exc)
            _finish_decision(p, "failed", str(exc))
            self.report({"ERROR"}, f"Could not apply {p.id}: {exc}")
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            p.status, p.message = "failed", f"{type(exc).__name__}: {exc}"
            _finish_decision(p, "failed", p.message)
            self.report({"ERROR"}, f"{p.id} failed part-way ({exc}). Press Ctrl+Z to undo the partial change.")
            return {"FINISHED"}  # keep the undo step so the partial change can be undone
        p.status = "applied"
        _finish_decision(p, "applied", f"{len(touched)} object(s): {', '.join(touched[:8])}")
        self.report({"INFO"}, f"Applied {p.id}: {p.title} (Ctrl+Z to undo)")
        return {"FINISHED"}


class GS_OT_ai_apply_file(_ProposalOp, bpy.types.Operator):
    bl_idname = "gamestudio.ai_apply_file"
    bl_label = "Apply AI File Change"
    bl_description = "Write this change to disk. The original is backed up and can be restored with Revert"
    bl_options = {"REGISTER"}

    def execute(self, context):
        p = _get_proposal(self, self.proposal_id)
        if p is None or p.kind != "file" or p.status != "pending":
            return {"CANCELLED"}
        try:
            proposals.apply_file(p, force=self.force)
        except (ProposalError, OSError) as exc:
            self.report({"ERROR"}, f"Could not apply {p.id}: {exc}")
            return {"CANCELLED"}
        p.status = "applied"
        _finish_decision(p, "applied", f"wrote {p.path}")
        self.report({"INFO"}, f"Applied {p.id}: wrote {p.path}")
        return {"FINISHED"}


class GS_OT_ai_reject(bpy.types.Operator):
    bl_idname = "gamestudio.ai_reject"
    bl_label = "Reject"
    bl_description = "Discard this proposal. The assistant is told it was rejected"
    proposal_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        p = _get_proposal(self, self.proposal_id)
        if p is None or p.status != "pending":
            return {"CANCELLED"}
        p.status = "rejected"
        _finish_decision(p, "rejected by the developer")
        return {"FINISHED"}


class GS_OT_ai_revert(bpy.types.Operator):
    bl_idname = "gamestudio.ai_revert"
    bl_label = "Revert"
    bl_description = "Restore the file from the backup made when this change was applied"
    proposal_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        p = _get_proposal(self, self.proposal_id)
        if p is None or p.kind != "file" or p.status != "applied":
            return {"CANCELLED"}
        try:
            note = proposals.revert_file(p)
        except (ProposalError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        p.status = "reverted"
        _finish_decision(p, "reverted by the developer", note)
        self.report({"INFO"}, note)
        return {"FINISHED"}


def _show_text_in_editor(context, text) -> bool:
    for area in context.screen.areas if context.screen else []:
        if area.type == "TEXT_EDITOR":
            area.spaces.active.text = text
            return True
    return False


class GS_OT_ai_preview(bpy.types.Operator):
    bl_idname = "gamestudio.ai_preview"
    bl_label = "Preview"
    bl_description = "Show the full change (diff or operation list) in the Text Editor"
    proposal_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        p = _get_proposal(self, self.proposal_id)
        if p is None:
            return {"CANCELLED"}
        text = proposals.show_preview(p)
        if not _show_text_in_editor(context, text):
            self.report({"INFO"}, f"Open a Text Editor and choose '{text.name}' to see the preview.")
        return {"FINISHED"}


class GS_OT_ai_details(bpy.types.Operator):
    bl_idname = "gamestudio.ai_details"
    bl_label = "Why?"
    bl_description = "Show the assistant's reasoning and the full list of changes"
    proposal_id: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=480)

    def draw(self, context):
        from .ui import draw_wrapped
        p = STORE.get(self.proposal_id)
        layout = self.layout
        if p is None:
            layout.label(text="Proposal not found.")
            return
        layout.label(text=f"{p.id}: {p.title}", icon="LIGHT")
        draw_wrapped(layout, p.rationale or "(no rationale given)", 460)
        layout.separator()
        layout.label(text=f"Risk: {p.risk}" + (f" ({'; '.join(p.risk_reasons)})" if p.risk_reasons else ""))
        for line in p.summary_lines()[:30]:
            draw_wrapped(layout, "- " + line, 460)

    def execute(self, context):
        return {"FINISHED"}


class GS_OT_ai_explain(bpy.types.Operator):
    bl_idname = "gamestudio.ai_explain"
    bl_label = "Explain"
    bl_description = "Ask the assistant to explain this proposal step by step"
    proposal_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        ui = context.window_manager.gs_ai
        try:
            SESSION.submit(f"Explain proposal {self.proposal_id} step by step: what each change does, why, "
                           "and anything I should check before applying it.",
                           role=ui.role, mode="ask", teach=True)
        except AssistantError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class GS_OT_ai_open_transcript(bpy.types.Operator):
    bl_idname = "gamestudio.ai_open_transcript"
    bl_label = "Open Transcript"
    bl_description = "Show the full conversation in the Text Editor"

    def execute(self, context):
        text = bpy.data.texts.get("AI Transcript") or bpy.data.texts.new("AI Transcript")
        text.clear()
        for entry in SESSION.transcript:
            text.write(f"[{entry['role']}]\n{entry['text']}\n\n")
        if not _show_text_in_editor(context, text):
            self.report({"INFO"}, "Open a Text Editor and choose 'AI Transcript'.")
        return {"FINISHED"}


class GS_OT_ai_clear_resolved(bpy.types.Operator):
    bl_idname = "gamestudio.ai_clear_resolved"
    bl_label = "Clear Resolved"
    bl_description = "Remove applied, rejected and failed proposals from the list"

    def execute(self, context):
        for p in STORE.all():
            if p.status != "pending" and not (p.kind == "file" and p.status == "applied"):
                proposals.remove_preview(p)
                STORE._items.pop(p.id, None)
        return {"FINISHED"}


class GS_OT_ai_toggle_lock(bpy.types.Operator):
    bl_idname = "gamestudio.ai_toggle_lock"
    bl_label = "Toggle AI Lock"
    bl_description = "Lock or unlock the selected objects against AI proposals"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        objs = list(context.selected_objects)
        lock = not all(o.gs.ai_lock for o in objs)
        for obj in objs:
            obj.gs.ai_lock = lock
        self.report({"INFO"}, f"{'Locked' if lock else 'Unlocked'} {len(objs)} object(s) for AI")
        return {"FINISHED"}


_install_state = {"running": False, "message": ""}


class GS_OT_ai_install_sdk(bpy.types.Operator):
    bl_idname = "gamestudio.ai_install_sdk"
    bl_label = "Install AI Support"
    bl_description = "Download the Anthropic Python SDK (about 15 MB) into this add-on's folder"

    @classmethod
    def poll(cls, context):
        return not _install_state["running"]

    def execute(self, context):
        if not bpy.app.online_access:
            self.report({"ERROR"}, "Online access is off. Enable it in Preferences > System.")
            return {"CANCELLED"}
        _install_state.update(running=True, message="Installing...")

        def work():
            ok, message = deps.install_sdk()
            agent.MAIN_QUEUE.put(lambda: _install_state.update(running=False, message=message))
        threading.Thread(target=work, daemon=True, name="gamestudio-pip").start()
        return {"FINISHED"}


class GS_OT_ai_enable(bpy.types.Operator):
    bl_idname = "gamestudio.ai_enable"
    bl_label = "Enable AI Assistant"
    bl_description = "Turn on the optional AI assistant (you can turn it off again in add-on preferences)"

    def execute(self, context):
        addon = context.preferences.addons.get(ADDON_ID)
        if addon is None:
            self.report({"ERROR"}, "Enable the GameStudio add-on in Preferences first.")
            return {"CANCELLED"}
        addon.preferences.assistant_enabled = True
        context.preferences.is_dirty = True
        return {"FINISHED"}


CLASSES = (
    GSAssistantUI,
    GS_OT_ai_send,
    GS_OT_ai_cancel,
    GS_OT_ai_new_conversation,
    GS_OT_ai_apply,
    GS_OT_ai_apply_file,
    GS_OT_ai_reject,
    GS_OT_ai_revert,
    GS_OT_ai_preview,
    GS_OT_ai_details,
    GS_OT_ai_explain,
    GS_OT_ai_open_transcript,
    GS_OT_ai_clear_resolved,
    GS_OT_ai_toggle_lock,
    GS_OT_ai_install_sdk,
    GS_OT_ai_enable,
)


def install_status() -> str:
    return _install_state["message"]


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.gs_ai = bpy.props.PointerProperty(type=GSAssistantUI)


def unregister():
    del bpy.types.WindowManager.gs_ai
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
