# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene Properties > Game Project: the game project, its levels and the web build.

Project-level UI lives in Scene Properties, next to the scene settings it goes with:

  Game Project   the project (name, folder, the scene's export name, the start scene, the last
                 export), the dev server and the project's packages, the component registry,
                 Open in <code editor>, Claude Code and Codex, the scene's Live Link setting, and the
                 Levels list (Open, the start and current level) with Export All
    Scripts      the components and systems in src/, New Component and New System
    Lighting     Bake Lighting (Preview, Final), Clear and the scene's baked lightmaps (ops_lighting.py)
    Build       Build Web (npm run build), its log and output folder

Every body is a draw function of ui.py or editors/project.py, shared with the sidebar and the
Project editor. Without a project the panel says why and offers New Project and Open Project.
"""

import bpy

from . import claude_code, ops_lighting, ui
from .core import project
from .editors import project as project_editor


class _ScenePanel:
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"


def _has_project(_context) -> bool:
    return project.project_root() is not None


def draw_scene_settings(layout, context) -> None:
    """The scene's own GameStudio settings, in the Properties editor's split layout."""
    scene_props = getattr(context.scene, "gs", None)
    if scene_props is None:
        return
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(scene_props, "live_enabled")


class GAMESTUDIO_PT_scene_project(_ScenePanel, bpy.types.Panel):
    bl_label = "Game Project"
    bl_idname = "GAMESTUDIO_PT_scene_project"

    def draw(self, context):
        layout = self.layout
        if project.project_root() is None:
            project_editor.draw_new_or_open(layout)
            return
        ui.draw_project(layout, context)
        project_editor.draw_open_buttons(layout)
        claude_code.draw_project_panel(self, context)
        draw_scene_settings(layout, context)
        ui.draw_levels(layout, context, default_closed=False)


class GAMESTUDIO_PT_scene_scripts(_ScenePanel, bpy.types.Panel):
    bl_label = "Scripts"
    bl_idname = "GAMESTUDIO_PT_scene_scripts"
    bl_parent_id = GAMESTUDIO_PT_scene_project.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _has_project(context)

    def draw(self, context):
        project_editor.draw_scripts(self.layout, context)


class GAMESTUDIO_PT_scene_lighting(_ScenePanel, bpy.types.Panel):
    bl_label = "Lighting"
    bl_idname = "GAMESTUDIO_PT_scene_lighting"
    bl_parent_id = GAMESTUDIO_PT_scene_project.bl_idname

    @classmethod
    def poll(cls, context):
        return _has_project(context)

    def draw(self, context):
        ops_lighting.draw_lighting(self.layout, context)


class GAMESTUDIO_PT_scene_build(_ScenePanel, bpy.types.Panel):
    bl_label = "Build"
    bl_idname = "GAMESTUDIO_PT_scene_build"
    bl_parent_id = GAMESTUDIO_PT_scene_project.bl_idname

    @classmethod
    def poll(cls, context):
        return _has_project(context)

    def draw(self, context):
        project_editor.draw_build(self.layout, context)


_CLASSES = (GAMESTUDIO_PT_scene_project, GAMESTUDIO_PT_scene_scripts, GAMESTUDIO_PT_scene_lighting,
            GAMESTUDIO_PT_scene_build)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
