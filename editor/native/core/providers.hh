// SPDX-License-Identifier: GPL-3.0-or-later
//
// Editor providers: the kinds of content a GameStudio extension editor can show
// (Game View, Inspector, AI assistant, ...). Plain C++ with no Blender headers,
// so it never breaks on Blender upgrades. Only editor/native/blender/ talks to Blender.
#pragma once

#include <memory>
#include <string>
#include <vector>

namespace gs {

struct EditorProvider {
  /** Stored in .blend files and used as the panel `bl_context`, e.g. "GAMESTUDIO_INSPECTOR".
   *  Letters, digits and underscores only, so it is also a valid enum identifier. */
  std::string idname;
  std::string label;        ///< Shown in the editor-type menu and the area header.
  std::string description;  ///< Tooltip in the editor-type menu.
  int icon = 0;             ///< Blender icon id; opaque here.

  /**
   * Native providers draw the main region themselves (the Game View does). Python providers
   * leave this null and draw with panels whose `bl_context` equals `idname`. The arguments
   * are a `bContext *` and an `ARegion *`; they are opaque here to keep Blender out.
   */
  void (*draw_main)(const void *context, void *region) = nullptr;
};

/** Ordered registry. Pointers to providers stay valid until the provider is removed. */
class ProviderRegistry {
 public:
  /** Adds the provider, or replaces the one with the same idname (keeps its position). */
  void add(EditorProvider provider);
  bool remove(const std::string &idname);
  const EditorProvider *find(const std::string &idname) const;
  /** Position in the registry, or -1. Used as the editor-type menu sub-type. */
  int index_of(const std::string &idname) const;
  const EditorProvider *at(int index) const;
  int size() const;

 private:
  std::vector<std::unique_ptr<EditorProvider>> providers_;
};

ProviderRegistry &providers();

/** True if `idname` is a valid provider identifier ([A-Za-z][A-Za-z0-9_]{0,62}). */
bool is_valid_provider_idname(const std::string &idname);

}  // namespace gs
