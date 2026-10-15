// SPDX-License-Identifier: GPL-3.0-or-later
//
// The extension editor (SPACE_EXTENSION, seams S05-S07).
//
// A single native editor type whose content comes from registered providers
// (core/providers.hh). Each provider appears in the editor-type menu as a sub-type,
// the way node-tree types do in the Node Editor. Python providers draw with panels
// whose `bl_context` equals the provider idname; native providers (the Game View)
// draw the main region themselves.
//
// This file is part of the Blender adapter layer: it is the only kind of file in
// editor/native allowed to include Blender headers.

#include <cstring>

#include "MEM_guardedalloc.h"

#include "DNA_screen_types.h"
#include "DNA_space_extension_types.h"
#include "DNA_space_types.h"
#include "DNA_userdef_types.h"

#include "BLI_listbase.h"
#include "BLI_string.h"
#include "BLI_string_utf8.h"

#include "BKE_context.hh"
#include "BKE_screen.hh"

#include "BLO_read_write.hh"

#include "ED_screen.hh"
#include "ED_space_api.hh"

#include "RNA_define.hh"
#include "RNA_enum_types.hh"

#include "UI_interface_c.hh"
#include "UI_resources.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "../core/providers.hh"
#include "game_view.hh"

namespace blender {

static SpaceExtension *extension_space(const ScrArea *area)
{
  return static_cast<SpaceExtension *>(area->spacedata.first);
}

static const gs::EditorProvider *extension_provider(const SpaceExtension *sext)
{
  return sext ? gs::providers().find(sext->provider_idname) : nullptr;
}

/* -------------------------------------------------------------------- */
/* Space callbacks. */

static SpaceLink *extension_create(const ScrArea * /*area*/, const Scene * /*scene*/)
{
  SpaceExtension *sext = MEM_new<SpaceExtension>("extension editor");
  sext->spacetype = SPACE_EXTENSION;
  if (const gs::EditorProvider *first = gs::providers().at(0)) {
    STRNCPY(sext->provider_idname, first->idname.c_str());
  }

  ARegion *region = BKE_area_region_new();
  BLI_addtail(&sext->regionbase, region);
  region->regiontype = RGN_TYPE_HEADER;
  region->alignment = (U.uiflag & USER_HEADER_BOTTOM) ? RGN_ALIGN_BOTTOM : RGN_ALIGN_TOP;

  region = BKE_area_region_new();
  BLI_addtail(&sext->regionbase, region);
  region->regiontype = RGN_TYPE_UI;
  region->alignment = RGN_ALIGN_RIGHT;
  region->flag = RGN_FLAG_HIDDEN;

  region = BKE_area_region_new();
  BLI_addtail(&sext->regionbase, region);
  region->regiontype = RGN_TYPE_WINDOW;

  return reinterpret_cast<SpaceLink *>(sext);
}

/* Doesn't free the space-link itself. */
static void extension_free(SpaceLink * /*sl*/) {}

static void extension_init(wmWindowManager * /*wm*/, ScrArea * /*area*/) {}

static SpaceLink *extension_duplicate(SpaceLink *sl)
{
  return reinterpret_cast<SpaceLink *>(MEM_dupalloc(reinterpret_cast<SpaceExtension *>(sl)));
}

static void extension_blend_write(BlendWriter *writer, SpaceLink *sl)
{
  writer->write_struct_cast<SpaceExtension>(sl);
}

/* Providers are listed as sub-types of the editor in the editor-type menu. */
static int extension_space_subtype_get(ScrArea *area)
{
  const int index = gs::providers().index_of(extension_space(area)->provider_idname);
  return index < 0 ? 0 : index;
}

static void extension_space_subtype_set(ScrArea *area, int value)
{
  if (const gs::EditorProvider *provider = gs::providers().at(value)) {
    STRNCPY(extension_space(area)->provider_idname, provider->idname.c_str());
  }
}

static void extension_space_subtype_item_extend(bContext * /*C*/,
                                                EnumPropertyItem **item,
                                                int *totitem)
{
  const gs::ProviderRegistry &registry = gs::providers();
  if (registry.size() == 0) {
    const EnumPropertyItem fallback = {
        0, "EXTENSION_EDITOR", ICON_WINDOW, "Extension Editor", "No editor providers registered"};
    RNA_enum_item_add(item, totitem, &fallback);
    return;
  }
  for (int i = 0; i < registry.size(); i++) {
    const gs::EditorProvider *provider = registry.at(i);
    const EnumPropertyItem entry = {i,
                                    provider->idname.c_str(),
                                    provider->icon ? provider->icon : ICON_WINDOW,
                                    provider->label.c_str(),
                                    provider->description.c_str()};
    RNA_enum_item_add(item, totitem, &entry);
  }
}

static StringRefNull extension_space_name_get(const ScrArea *area)
{
  const gs::EditorProvider *provider = extension_provider(extension_space(area));
  return provider ? StringRefNull(provider->label.c_str()) : StringRefNull("Extension Editor");
}

static int extension_space_icon_get(const ScrArea *area)
{
  const gs::EditorProvider *provider = extension_provider(extension_space(area));
  return (provider && provider->icon) ? provider->icon : ICON_WINDOW;
}

/* -------------------------------------------------------------------- */
/* Regions. */

static void extension_region_listener(const wmRegionListenerParams *params)
{
  const wmNotifier *wmn = params->notifier;
  if (wmn->category == NC_SPACE && wmn->data == ND_SPACE_EXTENSION) {
    ED_region_tag_redraw(params->region);
  }
}

static void extension_header_init(wmWindowManager * /*wm*/, ARegion *region)
{
  ED_region_header_init(region);
}

static void extension_header_draw(const bContext *C, ARegion *region)
{
  ED_region_header(C, region);
}

/* Without any Python header (shell not loaded, or a stock template) the header would be empty
 * and the user could not switch the area to another editor. This native header draws just the
 * editor-type menu, and only while it is the region's only header type. */
static bool extension_fallback_header_poll(const bContext *C, HeaderType *self)
{
  const ARegion *region = CTX_wm_region(C);
  if (region == nullptr) {
    return true;
  }
  for (const HeaderType &ht : region->runtime->type->headertypes) {
    if (&ht != self) {
      return false;
    }
  }
  return true;
}

static void extension_fallback_header_draw(const bContext *C, Header *header)
{
  ui::template_header(header->layout, const_cast<bContext *>(C));
}

static void extension_fallback_header_register(ARegionType *art)
{
  HeaderType *ht = MEM_new_zeroed<HeaderType>(__func__);
  STRNCPY_UTF8(ht->idname, "EXTENSION_EDITOR_HT_fallback");
  ht->space_type = SPACE_EXTENSION;
  ht->region_type = RGN_TYPE_HEADER;
  ht->poll = extension_fallback_header_poll;
  ht->draw = extension_fallback_header_draw;
  BLI_addtail(&art->headertypes, ht);
}

/** Panels are filtered by provider: a panel shows when its `bl_context` is the provider idname. */
static void extension_panels_layout(const bContext *C, ARegion *region)
{
  SpaceExtension *sext = reinterpret_cast<SpaceExtension *>(CTX_wm_space_data(C));
  const char *contexts[2] = {sext ? sext->provider_idname : "", nullptr};
  ED_region_panels_layout_ex(C,
                             region,
                             &region->runtime->type->paneltypes,
                             wm::OpCallContext::InvokeRegionWin,
                             contexts,
                             nullptr);
}

static void extension_panels_init(wmWindowManager *wm, ARegion *region)
{
  ED_region_panels_init(wm, region);
}

static void extension_main_region_init(wmWindowManager *wm, ARegion *region)
{
  ED_region_panels_init(wm, region);
  game_view_region_init(wm, region);
}

static const gs::EditorProvider *extension_provider_from_context(const bContext *C)
{
  return extension_provider(reinterpret_cast<SpaceExtension *>(CTX_wm_space_data(C)));
}

static void extension_main_layout(const bContext *C, ARegion *region)
{
  const gs::EditorProvider *provider = extension_provider_from_context(C);
  if (provider && provider->draw_main) {
    return; /* Native providers draw everything in draw_main. */
  }
  extension_panels_layout(C, region);
}

static void extension_main_draw(const bContext *C, ARegion *region)
{
  const gs::EditorProvider *provider = extension_provider_from_context(C);
  if (provider && provider->draw_main) {
    provider->draw_main(C, region);
    return;
  }
  ED_region_panels_draw(C, region);
}

void ED_spacetype_extension()
{
  std::unique_ptr<SpaceType> st = std::make_unique<SpaceType>();
  st->spaceid = SPACE_EXTENSION;
  STRNCPY_UTF8(st->name, "Extension Editor");

  st->create = extension_create;
  st->free = extension_free;
  st->init = extension_init;
  st->duplicate = extension_duplicate;
  st->blend_write = extension_blend_write;
  st->space_subtype_item_extend = extension_space_subtype_item_extend;
  st->space_subtype_get = extension_space_subtype_get;
  st->space_subtype_set = extension_space_subtype_set;
  st->space_name_get = extension_space_name_get;
  st->space_icon_get = extension_space_icon_get;
  st->operatortypes = game_view_operatortypes;
  st->keymap = game_view_keymap;

  /* Main region: panels from Python providers, or a native provider's own drawing. */
  ARegionType *art = MEM_new_zeroed<ARegionType>("extension editor main region");
  art->regionid = RGN_TYPE_WINDOW;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D;
  art->init = extension_main_region_init;
  art->layout = extension_main_layout;
  art->draw = extension_main_draw;
  art->listener = extension_region_listener;
  BLI_addhead(&st->regiontypes, art);

  /* Header: Python `Header` classes with bl_space_type = 'EXTENSION_EDITOR'. */
  art = MEM_new_zeroed<ARegionType>("extension editor header region");
  art->regionid = RGN_TYPE_HEADER;
  art->prefsizey = HEADERY;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D | ED_KEYMAP_HEADER;
  art->init = extension_header_init;
  art->draw = extension_header_draw;
  art->listener = extension_region_listener;
  extension_fallback_header_register(art);
  BLI_addhead(&st->regiontypes, art);

  /* Sidebar: provider panels with bl_region_type = 'UI'. */
  art = MEM_new_zeroed<ARegionType>("extension editor sidebar region");
  art->regionid = RGN_TYPE_UI;
  art->prefsizex = UI_SIDEBAR_PANEL_WIDTH;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_FRAMES;
  art->init = extension_panels_init;
  art->layout = extension_panels_layout;
  art->draw = ED_region_panels_draw;
  art->listener = extension_region_listener;
  BLI_addhead(&st->regiontypes, art);

  BKE_spacetype_register(std::move(st));

  game_view_register();
}

}  // namespace blender
