// SPDX-License-Identifier: GPL-3.0-or-later
//
// The `_gamestudio` built-in Python module (seam S06). The IDE shell never imports it
// directly: it goes through `gamestudio.host`, which falls back gracefully on stock Blender.

#include <Python.h>

#include <bitset>
#include <string>
#include <unordered_set>

#include "DNA_screen_types.h"
#include "DNA_space_types.h"
#include "DNA_windowmanager_types.h"

#include "BLI_listbase.h"

#include "BKE_global.hh"
#include "BKE_main.hh"

#include "ED_screen.hh"

#include "RNA_access.hh"
#include "RNA_enum_types.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "../core/providers.hh"
#include "game_view.hh"

#ifndef GAMESTUDIO_VERSION_STR
#  define GAMESTUDIO_VERSION_STR "0.0.0"
#endif

namespace blender {

/* Seam S08: defined in rna_screen.cc, null unless set here. */
extern bool (*RNA_area_ui_type_hidden_fn)(int space_type, const char *identifier);

/* The top bar and status bar are global areas, which Python cannot reach (`screen.areas` leaves
 * them out) and which only redraw on a few notifiers. The shell shows play state there. */
void tag_redraw_global_areas()
{
  if (G_MAIN == nullptr) {
    return;
  }
  for (wmWindowManager &wm : G_MAIN->wm) {
    for (wmWindow &win : wm.windows) {
      for (ScrArea &area : win.global_areas.areabase) {
        ED_area_tag_redraw(&area);
      }
    }
  }
}

namespace {

/* Whole editor types ('SEQUENCE_EDITOR') and editor sub-types ('CompositorNodeTree'). */
std::bitset<SPACE_TYPE_NUM> hidden_space_types;
std::unordered_set<std::string> hidden_subtypes;

bool ui_type_hidden(const int space_type, const char *identifier)
{
  if (space_type >= 0 && space_type < SPACE_TYPE_NUM && hidden_space_types.test(space_type)) {
    return true;
  }
  return identifier && identifier[0] && hidden_subtypes.count(identifier);
}

void tag_redraw_extension_editors()
{
  WM_main_add_notifier(NC_SPACE | ND_SPACE_EXTENSION, nullptr);
}

PyObject *gs_version(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyUnicode_FromString(GAMESTUDIO_VERSION_STR);
}

PyObject *gs_capabilities(PyObject * /*self*/, PyObject * /*args*/)
{
#ifdef _WIN32
  /* The Game View's frame transport is Windows-only in v1 (docs/fork/SURFACE.md). */
  return Py_BuildValue("[ssss]", "native", "extension_editors", "editor_type_filter", "game_view");
#else
  return Py_BuildValue("[sss]", "native", "extension_editors", "editor_type_filter");
#endif
}

PyObject *gs_register_editor(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  static const char *keywords[] = {"idname", "label", "icon", "description", nullptr};
  const char *idname = nullptr, *label = nullptr, *icon = "NONE", *description = "";
  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "ss|ss:register_editor", const_cast<char **>(keywords), &idname, &label, &icon,
          &description))
  {
    return nullptr;
  }
  if (!gs::is_valid_provider_idname(idname)) {
    PyErr_Format(PyExc_ValueError,
                 "register_editor: idname '%s' must match [A-Za-z][A-Za-z0-9_]{0,62}",
                 idname);
    return nullptr;
  }
  int icon_value = 0;
  if (!RNA_enum_value_from_id(rna_enum_icon_items, icon, &icon_value)) {
    PyErr_Format(PyExc_ValueError, "register_editor: unknown icon '%s'", icon);
    return nullptr;
  }
  gs::EditorProvider provider;
  provider.idname = idname;
  provider.label = label;
  provider.description = description;
  provider.icon = icon_value;
  /* Re-registering keeps a native draw function (the Game View) when Python only updates
   * the label or icon. */
  if (const gs::EditorProvider *existing = gs::providers().find(idname)) {
    provider.draw_main = existing->draw_main;
  }
  gs::providers().add(std::move(provider));
  tag_redraw_extension_editors();
  Py_RETURN_NONE;
}

PyObject *gs_unregister_editor(PyObject * /*self*/, PyObject *args)
{
  const char *idname = nullptr;
  if (!PyArg_ParseTuple(args, "s:unregister_editor", &idname)) {
    return nullptr;
  }
  const gs::EditorProvider *provider = gs::providers().find(idname);
  if (provider && provider->draw_main) {
    /* Native providers belong to the module; Python may not remove them. */
    Py_RETURN_FALSE;
  }
  const bool removed = gs::providers().remove(idname);
  tag_redraw_extension_editors();
  return PyBool_FromLong(removed);
}

PyObject *gs_editors(PyObject * /*self*/, PyObject * /*args*/)
{
  const gs::ProviderRegistry &registry = gs::providers();
  PyObject *list = PyList_New(registry.size());
  for (int i = 0; i < registry.size(); i++) {
    PyList_SET_ITEM(list, i, PyUnicode_FromString(registry.at(i)->idname.c_str()));
  }
  return list;
}

PyObject *gs_set_hidden_editor_types(PyObject * /*self*/, PyObject *arg)
{
  PyObject *seq = PySequence_Fast(arg, "set_hidden_editor_types: expected a sequence of strings");
  if (seq == nullptr) {
    return nullptr;
  }
  std::bitset<SPACE_TYPE_NUM> hidden;
  std::unordered_set<std::string> subtypes;
  const Py_ssize_t count = PySequence_Fast_GET_SIZE(seq);
  for (Py_ssize_t i = 0; i < count; i++) {
    const char *identifier = PyUnicode_AsUTF8(PySequence_Fast_GET_ITEM(seq, i));
    if (identifier == nullptr) {
      Py_DECREF(seq);
      return nullptr;
    }
    int value = 0;
    if (RNA_enum_value_from_id(rna_enum_space_type_items, identifier, &value)) {
      hidden.set(value);
    }
    else if (gs::is_valid_provider_idname(identifier)) {
      /* Sub-types (node tree types, providers) are only known when the menu is built. */
      subtypes.insert(identifier);
    }
    else {
      PyErr_Format(PyExc_ValueError, "set_hidden_editor_types: invalid identifier '%s'", identifier);
      Py_DECREF(seq);
      return nullptr;
    }
  }
  Py_DECREF(seq);
  hidden_space_types = hidden;
  hidden_subtypes = std::move(subtypes);
  const bool any = hidden.any() || !hidden_subtypes.empty();
  RNA_area_ui_type_hidden_fn = any ? ui_type_hidden : nullptr;
  Py_RETURN_NONE;
}

PyObject *gs_tag_redraw(PyObject * /*self*/, PyObject * /*args*/)
{
  tag_redraw_extension_editors();
  tag_redraw_global_areas();
  Py_RETURN_NONE;
}

PyMethodDef methods[] = {
    {"version", gs_version, METH_NOARGS, "GameStudio Editor version string."},
    {"capabilities", gs_capabilities, METH_NOARGS, "Native capabilities of this build."},
    {"register_editor",
     reinterpret_cast<PyCFunction>(reinterpret_cast<void *>(gs_register_editor)),
     METH_VARARGS | METH_KEYWORDS,
     "register_editor(idname, label, icon='NONE', description='')\n"
     "Add (or update) an extension-editor provider. Its panels use bl_context=idname."},
    {"unregister_editor", gs_unregister_editor, METH_VARARGS, "Remove a Python editor provider."},
    {"editors", gs_editors, METH_NOARGS, "Identifiers of the registered editor providers."},
    {"set_hidden_editor_types",
     gs_set_hidden_editor_types,
     METH_O,
     "Hide editor types ('SEQUENCE_EDITOR') and sub-types ('CompositorNodeTree') from the "
     "editor-type menu. Replaces the previous set; an empty sequence shows everything."},
    {"tag_redraw", gs_tag_redraw, METH_NOARGS, "Redraw every extension editor, the top bar and the status bar."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_gamestudio",
    "GameStudio Editor native API (use gamestudio.host instead of importing this directly).",
    -1,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

}  // namespace

PyObject *BPyInit__gamestudio();

PyObject *BPyInit__gamestudio()
{
  PyObject *mod = PyModule_Create(&module);
  if (mod && game_view_add_python_methods(mod) != 0) {
    Py_DECREF(mod);
    return nullptr;
  }
  return mod;
}

}  // namespace blender
