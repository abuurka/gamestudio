// SPDX-License-Identifier: GPL-3.0-or-later
//
// The docked Game View: a native extension-editor provider (GAMESTUDIO_GAME_VIEW) that shows
// the game running in the runtime host (docs/fork/ARCHITECTURE.md section 4, docs/fork/SURFACE.md).
//
// Part of the Blender adapter layer. The surface link itself is gs_gameview (gameview/),
// which knows nothing about Blender.

#pragma once

#include <cstdint>

/* Python's object type, without pulling Python.h into every includer. */
struct _object;
typedef struct _object PyObject;

namespace blender {

struct ARegion;
struct wmKeyConfig;
struct wmWindowManager;

/* -------------------------------------------------------------------- */
/* Wiring (called from space_extension.cc and python_module.cc). */

/** Adds the native GAMESTUDIO_GAME_VIEW provider. Call from ED_spacetype_extension(). */
void game_view_register();

/** Redraws the top bar and status bar of every window (python_module.cc; they show play state). */
void tag_redraw_global_areas();

/** #SpaceType::operatortypes: registers GAMESTUDIO_OT_game_view_capture. */
void game_view_operatortypes();

/**
 * #SpaceType::keymap: creates the empty "Game View" keymap (SPACE_EXTENSION, WINDOW).
 * Its items come from the shell through the add-on key configuration, because Blender rebuilds
 * the default key configuration from Python presets after this runs.
 */
void game_view_keymap(wmKeyConfig *keyconf);

/** Main (WINDOW) region init: attaches the "Game View" keymap, active only in Game Views. */
void game_view_region_init(wmWindowManager *wm, ARegion *region);

/**
 * Adds game_view_start/stop/status/detach/devtools/set_paused to the `_gamestudio` module.
 * Returns 0, or -1 with a Python error set.
 */
int game_view_add_python_methods(PyObject *module);

/* -------------------------------------------------------------------- */
/* Internal: DOM vocabulary for Blender events (game_view_input.cc). */

struct GameViewDomKey {
  /** DOM `KeyboardEvent.code`, layout-independent ("KeyW", "ShiftLeft"). */
  const char *code;
  /** DOM `KeyboardEvent.key` when no text was typed ("w", "Shift", "Enter"). */
  const char *key;
  /** The same with Shift held, for letters; null when it depends on the layout. */
  const char *shift_key;
};

/** False for events that are not keys a web page can see. */
bool game_view_dom_key(int event_type, GameViewDomKey *r_key);
/** Blender `wmEvent::modifier` flags to gs::gameview::Modifier bits. */
uint32_t game_view_dom_modifiers(int event_modifier);
/** DOM `MouseEvent.button` for a Blender mouse button event type, or -1. */
int game_view_dom_button(int event_type);
/** DOM `MouseEvent.buttons` bit for a DOM `button` value. */
int game_view_dom_buttons_bit(int dom_button);

}  // namespace blender
