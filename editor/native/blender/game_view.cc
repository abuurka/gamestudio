// SPDX-License-Identifier: GPL-3.0-or-later
//
// The docked Game View (docs/fork/ARCHITECTURE.md section 4): the native provider
// GAMESTUDIO_GAME_VIEW shows frames from the runtime host and forwards input to it.
//
// There is one session per process, and only the main thread touches it:
//   - Python starts it (`_gamestudio.game_view_start`). That launches the host through
//     gs_gameview and registers a BLI timer.
//   - The worker thread's frame callback only sets an atomic flag. The timer (main thread)
//     tags Game View regions for redraw, drains state and cursor-grab events, sends a
//     debounced `surface.configure` and pauses the game while no Game View is visible.
//   - draw_main uploads the newest frame into a GPU texture and draws it letterboxed.
//   - GAMESTUDIO_OT_game_view_capture forwards keyboard and mouse input while captured.
// The session ends with `game_view_stop`, or at exit when BLI_timer_free() frees the timer
// (before the GPU module exits). Stopping never waits: a reaper thread closes the link, frees
// the frame transport, gives the host time to exit by itself, then closes its job.
//
// Part of the Blender adapter layer.

#include <Python.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "MEM_guardedalloc.h"

#include "DNA_screen_types.h"
#include "DNA_space_extension_types.h"
#include "DNA_space_types.h"
#include "DNA_userdef_types.h"
#include "DNA_windowmanager_types.h"

#include "BLI_listbase_iterator.hh"
#include "BLI_rect.h"
#include "BLI_string_utf8.h"
#include "BLI_timer.h"

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_main.hh"
#include "BKE_screen.hh"
#include "BKE_wm_runtime.hh"

#include "GHOST_IWindow.hh"

#include "ED_screen.hh"

#include "GPU_capabilities.hh"
#include "GPU_immediate.hh"
#include "GPU_immediate_util.hh"
#include "GPU_shader_builtin.hh"
#include "GPU_state.hh"
#include "GPU_texture.hh"

#include "UI_interface_c.hh"
#include "UI_resources.hh"

#include "WM_api.hh"
#include "WM_keymap.hh"
#include "WM_types.hh"

#include "gs_gameview/gameview.hh"

#include "../core/providers.hh"
#include "game_view.hh"

namespace blender {

namespace gv = gs::gameview;

static constexpr const char *GAME_VIEW_IDNAME = "GAMESTUDIO_GAME_VIEW";
static constexpr const char *GAME_VIEW_KEYMAP = "Game View";
/** Let a region resize settle before resizing the game (each resize reallocates its textures). */
static constexpr double CONFIGURE_DEBOUNCE_MS = 150.0;
/** Pause the game once no Game View has been visible for this long. */
static constexpr double AUTO_PAUSE_DELAY_MS = 500.0;
/** How long the "how to release" hint shows after input is captured. */
static constexpr double RELEASE_HINT_MS = 3000.0;
/** DOM wheel delta per notch, in CSS pixels. */
static constexpr float WHEEL_NOTCH = 100.0f;
static constexpr int MIN_GAME_SIZE = 16;
static constexpr int MAX_GAME_SIZE = 16384;

/* -------------------------------------------------------------------- */
/* Session state. */

/** One Game View region's drawing, for status() (tests and diagnostics). */
struct GameViewRegionDraws {
  const ARegion *region = nullptr;
  uint64_t draws = 0;
  /** Draws that showed a game frame, and the newest frame shown. */
  uint64_t frames_drawn = 0;
  uint64_t frame_id = 0;
};

struct GameViewSession {
  std::unique_ptr<gv::Surface> surface;
  gv::HostProcess host;
  /** Refreshed by the timer on StateChanged events. */
  gv::Status status;
  std::vector<GameViewRegionDraws> region_draws;

  gpu::Texture *texture = nullptr;
  int texture_w = 0;
  int texture_h = 0;
  gv::PixelFormat texture_format = gv::PixelFormat::BGRA8;
  bool texture_has_frame = false;
  uint64_t frame_id = 0;
  uint64_t frames_uploaded = 0;
  double upload_ms = 0.0;
  double latency_ms = 0.0;
  double copy_ms = 0.0;

  /** Size last sent with surface.configure (or at launch), and a newer one being debounced. */
  int sent_w = 0;
  int sent_h = 0;
  float sent_dpr = 1.0f;
  int pending_w = 0;
  int pending_h = 0;
  float pending_dpr = 1.0f;
  double pending_since = 0.0;

  bool user_paused = false;
  bool auto_paused = false;
  bool sent_paused = false;
  /** When the last visible Game View went away; 0 while one is visible. */
  double hidden_since = 0.0;

  /** The game asked for pointer lock (cursor.grab). Applied only while input is captured. */
  bool game_wants_grab = false;
};

struct HeldKey {
  std::string code;
  std::string key;
};

/** Owned by the running capture operator (`wmOperator::customdata`). */
struct GameViewCapture {
  wmWindow *win = nullptr;
  /** The Game View region in window coordinates; confines a grabbed cursor. */
  rcti region_rect = {};
  int last_xy[2] = {0, 0};
  bool have_last_xy = false;
  /** Last pointer position in game pixels; frozen while the cursor is grabbed. */
  double game_x = 0.0;
  double game_y = 0.0;
  /** DOM `MouseEvent.buttons` held, and keys reported down. Both get an "up" on release. */
  int buttons = 0;
  std::vector<HeldKey> keys;
  bool grabbed = false;
  /** Released from outside the operator; it finishes on its next event. */
  bool released = false;
  double started_ms = 0.0;
};

/* Raw pointers on purpose: static destructors run after GPU and WM exit. */
static GameViewSession *g_session = nullptr;
static GameViewCapture *g_capture = nullptr;
/** Set by the worker thread's frame callback, cleared by the timer. */
static std::atomic<bool> g_frame_ready{false};

static uintptr_t game_view_timer_uuid()
{
  return reinterpret_cast<uintptr_t>(&g_frame_ready);
}

static const char *game_view_state_name(const gv::State state)
{
  switch (state) {
    case gv::State::Created:
      return "created";
    case gv::State::Waiting:
      return "waiting";
    case gv::State::Connected:
      return "connected";
    case gv::State::Closed:
      return "closed";
    case gv::State::Failed:
      return "failed";
  }
  return "unknown";
}

static float game_view_dpr()
{
  const float scale = UI_SCALE_FAC;
  return scale > 0.0f ? std::clamp(scale, 0.25f, 8.0f) : 1.0f;
}

static bool session_accepts_input(const GameViewSession &s)
{
  return s.status.state == gv::State::Connected && s.status.mode != "window";
}

/** The game is docked here and the view shows its frames (not detached, not stopped). */
static bool session_shows_frames(const GameViewSession &s)
{
  return s.status.state == gv::State::Connected && s.status.mode != "window" &&
         s.status.page_state != "crashed" && s.status.page_state != "error";
}

/** What the view says over (or instead of) the game. No title: just the game. */
struct GameViewOverlay {
  const char *title = nullptr;
  std::string detail;
};

static GameViewOverlay session_overlay(const gv::Status &status, const bool has_frame)
{
  const auto restart_hint = [](const std::string &why) {
    return why.empty() ? std::string("Press Restart to run it again") :
                         why + " - press Restart to run it again";
  };
  GameViewOverlay overlay;
  switch (status.state) {
    case gv::State::Created:
    case gv::State::Waiting:
      overlay.title = "Starting the game...";
      break;
    case gv::State::Connected:
      if (status.mode == "window") {
        overlay.title = "The game is running in its own window";
        overlay.detail = "Restart docks it here again";
      }
      else if (status.page_state == "crashed") {
        overlay.title = "Game stopped (crashed)";
        overlay.detail = restart_hint(status.message);
      }
      else if (status.page_state == "error") {
        overlay.title = "The game failed to load";
        overlay.detail = status.message;
      }
      else if (!has_frame) {
        overlay.title = "Loading the game...";
      }
      break;
    case gv::State::Closed:
      overlay.title = "Game stopped";
      overlay.detail = status.message;
      break;
    case gv::State::Failed:
      /* After a handshake (the host said hello) a failure means the host died or hung. */
      overlay.title = status.host_pid ? "Game stopped (crashed)" : "Game View failed";
      overlay.detail = status.host_pid ? restart_hint(status.message) : status.message;
      break;
  }
  return overlay;
}

/* -------------------------------------------------------------------- */
/* Areas showing the Game View. */

static bool area_shows_game_view(const ScrArea *area)
{
  if (area == nullptr || area->spacetype != SPACE_EXTENSION) {
    return false;
  }
  const SpaceExtension *sext = static_cast<const SpaceExtension *>(area->spacedata.first);
  return sext && std::strcmp(sext->provider_idname, GAME_VIEW_IDNAME) == 0;
}

/** Blender does not draw minimized windows (wm_draw_update), so their Game Views show nothing. */
static bool window_minimized(const wmWindow &win)
{
  const GHOST_IWindow *ghost_window = static_cast<const GHOST_IWindow *>(win.runtime->ghostwin);
  return ghost_window && ghost_window->getState() == GHOST_kWindowStateMinimized;
}

template<typename Fn> static void foreach_game_view_area(Fn fn, const bool skip_minimized = false)
{
  Main *bmain = G_MAIN;
  wmWindowManager *wm = bmain ? static_cast<wmWindowManager *>(bmain->wm.first) : nullptr;
  if (wm == nullptr) {
    return;
  }
  for (wmWindow &win : wm->windows) {
    bScreen *screen = WM_window_get_active_screen(&win);
    if (screen == nullptr || (skip_minimized && window_minimized(win))) {
      continue;
    }
    for (ScrArea &area : screen->areabase) {
      if (area_shows_game_view(&area)) {
        fn(area);
      }
    }
  }
}

/** Tags the main regions of every Game View, and their headers (status text) if asked. */
static void tag_game_views(const bool headers)
{
  foreach_game_view_area([headers](ScrArea &area) {
    for (ARegion &region : area.regionbase) {
      if (region.regiontype == RGN_TYPE_WINDOW) {
        ED_region_tag_redraw_no_rebuild(&region);
      }
      else if (headers && region.regiontype == RGN_TYPE_HEADER) {
        ED_region_tag_redraw(&region);
      }
    }
  });
}

struct GameViewTarget {
  bool visible = false;
  int width = 0;
  int height = 0;
};

/** The largest visible Game View decides the game's size. Minimized windows show nothing. */
static GameViewTarget game_view_target()
{
  GameViewTarget target;
  foreach_game_view_area(
      [&target](ScrArea &area) {
        const ARegion *region = BKE_area_find_region_type(&area, RGN_TYPE_WINDOW);
        if (region == nullptr || !region->runtime->visible || region->winx <= 0 ||
            region->winy <= 0)
        {
          return;
        }
        if (!target.visible || region->winx * region->winy > target.width * target.height) {
          target.visible = true;
          target.width = region->winx;
          target.height = region->winy;
        }
      },
      true);
  target.width = std::clamp(target.width, MIN_GAME_SIZE, MAX_GAME_SIZE);
  target.height = std::clamp(target.height, MIN_GAME_SIZE, MAX_GAME_SIZE);
  return target;
}

/* -------------------------------------------------------------------- */
/* Input capture: shared by the timer and the operator. */

static void capture_update_grab(GameViewCapture &cap)
{
  const bool want = g_session && g_session->game_wants_grab && !cap.released;
  if (want == cap.grabbed || cap.win == nullptr) {
    return;
  }
  if (want) {
    /* A hidden grab keeps reporting positions from where the cursor was: motion continues. */
    WM_cursor_grab_enable(cap.win, WM_CURSOR_WRAP_XY, &cap.region_rect, true);
  }
  else {
    WM_cursor_grab_disable(cap.win, nullptr);
    /* The cursor jumps back to where the grab started: no relative motion across that. */
    cap.have_last_xy = false;
  }
  cap.grabbed = want;
  tag_game_views(true);
}

/** Ends the capture: every key and button the game saw go down goes up, then focus is lost. */
static void capture_release(GameViewCapture &cap)
{
  if (cap.released) {
    return;
  }
  if (GameViewSession *s = g_session) {
    for (const HeldKey &held : cap.keys) {
      gv::KeyEvent event;
      event.down = false;
      event.code = held.code;
      event.key = held.key;
      s->surface->send_key(event);
    }
    for (int button = 0; button < 5; button++) {
      const int bit = game_view_dom_buttons_bit(button);
      if (cap.buttons & bit) {
        cap.buttons &= ~bit;
        gv::PointerEvent event;
        event.type = gv::PointerType::Up;
        event.x = cap.game_x;
        event.y = cap.game_y;
        event.button = button;
        event.buttons = cap.buttons;
        s->surface->send_pointer(event);
      }
    }
    s->surface->set_focus(false);
    /* Losing focus ends the game's pointer lock. */
    s->game_wants_grab = false;
  }
  cap.keys.clear();
  cap.buttons = 0;
  if (cap.grabbed && cap.win) {
    WM_cursor_grab_disable(cap.win, nullptr);
  }
  cap.grabbed = false;
  cap.released = true;
  if (g_capture == &cap) {
    g_capture = nullptr;
  }
  /* Headers too: they show whether input is captured. */
  tag_game_views(true);
}

/* -------------------------------------------------------------------- */
/* Session lifetime. */

static void session_stop(bool unregister_timer);

/**
 * Ends stopped sessions off the main thread, which never waits for them:
 *   1. destroys the Surface: the link closes (so the host exits; its JavaScript is done within
 *      ~15 ms), the worker thread is joined and its D3D11 device released (slow under GPU load);
 *   2. gives the host process up to 2 s to go away (0.2 s to seconds; GPU teardown);
 *   3. destroys the HostProcess, whose kill-on-close job ends whatever is left.
 * The static instance's destructor (at exit) stops step 2 short and joins every teardown, so
 * no thread is still inside D3D11 when its DLL unloads.
 */
class SessionReaper {
 public:
  ~SessionReaper()
  {
    quitting_ = true;
    for (Teardown &teardown : teardowns_) {
      teardown.thread.join();
    }
  }

  void reap(std::unique_ptr<gv::Surface> surface, gv::HostProcess host)
  {
    std::erase_if(teardowns_, [](Teardown &teardown) {
      if (!teardown.done->load()) {
        return false;
      }
      teardown.thread.join();
      return true;
    });
    try {
      /* Nothing may throw once the thread runs (a joinable std::thread must not be dropped). */
      teardowns_.reserve(teardowns_.size() + 1);
      auto done = std::make_shared<std::atomic<bool>>(false);
      std::thread thread(
          [this, done, surface = std::move(surface), host = std::move(host)]() mutable {
            surface.reset();
            for (int waited = 0; waited < 2000 && !quitting_ && !host.wait(50); waited += 50) {
            }
            host = gv::HostProcess();
            done->store(true);
          });
      teardowns_.push_back({std::move(thread), done});
    }
    catch (...) {
      /* No thread: tear down here (the arguments go out of scope; the job closes at once). */
    }
  }

 private:
  struct Teardown {
    std::thread thread;
    std::shared_ptr<std::atomic<bool>> done;
  };
  std::vector<Teardown> teardowns_; /* Main thread only. */
  std::atomic<bool> quitting_{false};
};

static SessionReaper g_reaper;

static void game_view_timer_free(uintptr_t /*uuid*/, void * /*user_data*/)
{
  /* The timer is going away: BLI_timer_free() at exit, or the timer itself returned -1. */
  session_stop(false);
}

static void session_stop(const bool unregister_timer)
{
  GameViewSession *s = g_session;
  if (s == nullptr) {
    return;
  }
  if (g_capture) {
    capture_release(*g_capture);
  }
  g_session = nullptr;
  if (unregister_timer) {
    /* Calls game_view_timer_free(), which finds no session now. */
    BLI_timer_unregister(game_view_timer_uuid());
  }

  g_reaper.reap(std::move(s->surface), std::move(s->host));
  if (s->texture) {
    GPU_texture_free(s->texture);
  }
  MEM_delete(s);
  g_frame_ready.store(false);
  tag_game_views(true);
  tag_redraw_global_areas();
}

static void session_apply_pause(GameViewSession &s)
{
  const bool paused = s.user_paused || s.auto_paused;
  if (paused != s.sent_paused && s.status.state == gv::State::Connected) {
    s.surface->set_paused(paused);
    s.sent_paused = paused;
  }
}

static void session_update_pause(GameViewSession &s, const bool visible, const double now)
{
  if (visible || s.status.mode == "window") {
    s.hidden_since = 0.0;
    s.auto_paused = false;
  }
  else if (s.hidden_since == 0.0) {
    s.hidden_since = now;
  }
  else if (now - s.hidden_since >= AUTO_PAUSE_DELAY_MS) {
    s.auto_paused = true;
  }
  session_apply_pause(s);
}

static void session_update_size(GameViewSession &s, const GameViewTarget &target, const double now)
{
  if (!target.visible || s.status.mode == "window") {
    return;
  }
  const float dpr = game_view_dpr();
  if (target.width == s.sent_w && target.height == s.sent_h && dpr == s.sent_dpr) {
    s.pending_w = 0;
    return;
  }
  if (target.width != s.pending_w || target.height != s.pending_h || dpr != s.pending_dpr) {
    s.pending_w = target.width;
    s.pending_h = target.height;
    s.pending_dpr = dpr;
    s.pending_since = now;
    return;
  }
  if (now - s.pending_since >= CONFIGURE_DEBOUNCE_MS) {
    s.surface->configure(target.width, target.height, dpr);
    s.sent_w = target.width;
    s.sent_h = target.height;
    s.sent_dpr = dpr;
    s.pending_w = 0;
  }
}

/** Main thread, every event-loop iteration while a session exists. */
static double game_view_timer(uintptr_t /*uuid*/, void * /*user_data*/)
{
  GameViewSession *s = g_session;
  if (s == nullptr) {
    return -1.0;
  }

  const bool new_frame = g_frame_ready.exchange(false, std::memory_order_acq_rel);
  bool state_changed = false;
  gv::Event event;
  while (s->surface->poll_event(event)) {
    if (event.type == gv::EventType::StateChanged) {
      state_changed = true;
    }
    else if (event.type == gv::EventType::CursorGrab) {
      s->game_wants_grab = event.grab;
      if (g_capture) {
        capture_update_grab(*g_capture);
      }
    }
  }
  if (state_changed) {
    s->status = s->surface->status();
    if (g_capture && !session_accepts_input(*s)) {
      capture_release(*g_capture);
    }
    if (s->status.state == gv::State::Closed || s->status.state == gv::State::Failed) {
      /* The host exited, crashed or hung: end its whole process tree now, not at the next stop. */
      s->host.terminate();
    }
    /* The status bar shows whether the docked game starts, runs, crashed or failed. */
    tag_redraw_global_areas();
  }

  if (s->status.state == gv::State::Connected) {
    const double now = gv::now_ms();
    const GameViewTarget target = game_view_target();
    session_update_size(*s, target, now);
    session_update_pause(*s, target.visible, now);
  }

  if (new_frame || state_changed) {
    tag_game_views(state_changed);
  }
  return 0.0;
}

static bool session_start(const char *executable,
                          const char *url,
                          const char *app_dir,
                          int width,
                          int height,
                          const char *log_file,
                          std::string *r_error)
{
  /* One session per process: starting again restarts the game. */
  session_stop(true);

  const GameViewTarget target = game_view_target();
  if (target.visible) {
    width = target.width;
    height = target.height;
  }
  width = std::clamp(width, MIN_GAME_SIZE, MAX_GAME_SIZE);
  height = std::clamp(height, MIN_GAME_SIZE, MAX_GAME_SIZE);
  const float dpr = game_view_dpr();

  gv::SurfaceOptions options;
  options.width = width;
  options.height = height;
  options.dpr = dpr;
  options.adapter_luid = gv::default_adapter_luid();
  std::unique_ptr<gv::Surface> surface = gv::Surface::create(options, r_error);
  if (!surface) {
    return false;
  }
  /* Worker thread: no GPU or WM calls here, the timer picks the flag up. */
  surface->set_frame_callback([]() { g_frame_ready.store(true, std::memory_order_release); });

  gv::HostLaunch launch;
  launch.executable = executable;
  launch.app_dir = app_dir;
  launch.url = url;
  launch.docked = true;
  launch.surface_port = surface->port();
  launch.token = surface->token();
  launch.width = width;
  launch.height = height;
  launch.dpr = dpr;
  launch.adapter_luid = options.adapter_luid;
  launch.log_file = log_file;

  gv::HostProcess host;
  if (!gv::launch_host(launch, host, r_error)) {
    return false;
  }
  if (!surface->start(host, r_error)) {
    surface.reset();
    return false;
  }

  GameViewSession *s = MEM_new<GameViewSession>(__func__);
  s->surface = std::move(surface);
  s->host = std::move(host);
  s->status = s->surface->status();
  s->sent_w = width;
  s->sent_h = height;
  s->sent_dpr = dpr;
  g_frame_ready.store(false);
  g_session = s;
  BLI_timer_register(
      game_view_timer_uuid(), game_view_timer, nullptr, game_view_timer_free, 0.0, true);
  tag_game_views(true);
  return true;
}

/* -------------------------------------------------------------------- */
/* Drawing. */

/** Uploads the newest frame, if there is one. Called while drawing (GPU context active). */
static void session_upload_frame(GameViewSession &s)
{
  gv::FrameView frame;
  if (!s.surface->acquire_latest(frame)) {
    return;
  }
  const double start = gv::now_ms();
  const int max_size = GPU_max_texture_size();
  const bool valid = frame.pixels && frame.width > 0 && frame.height > 0 &&
                     frame.width <= max_size && frame.height <= max_size &&
                     frame.row_pitch >= frame.width * 4 && frame.row_pitch % 4 == 0;
  if (valid) {
    if (s.texture == nullptr || s.texture_w != frame.width || s.texture_h != frame.height) {
      if (s.texture) {
        GPU_texture_free(s.texture);
      }
      s.texture = GPU_texture_create_2d("gamestudio_game_view",
                                        frame.width,
                                        frame.height,
                                        1,
                                        gpu::TextureFormat::UNORM_8_8_8_8,
                                        GPU_TEXTURE_USAGE_SHADER_READ,
                                        nullptr);
      s.texture_w = frame.width;
      s.texture_h = frame.height;
      s.texture_has_frame = false;
    }
    if (s.texture) {
      if (!s.texture_has_frame || s.texture_format != frame.format) {
        /* The page is opaque; forcing alpha keeps the region buffer opaque too. */
        GPU_texture_swizzle_set(s.texture,
                                frame.format == gv::PixelFormat::BGRA8 ? "bgr1" : "rgb1");
        s.texture_format = frame.format;
      }
      const int row_length = frame.row_pitch / 4;
      GPU_texture_update_sub(s.texture,
                             GPU_DATA_UBYTE,
                             frame.pixels,
                             0,
                             0,
                             0,
                             frame.width,
                             frame.height,
                             1,
                             row_length == frame.width ? 0 : uint(row_length));
      s.texture_has_frame = true;
      s.frame_id = frame.frame_id;
      s.frames_uploaded++;
      s.copy_ms = frame.copy_ms;
      const double produced = std::isnan(frame.produced_ms) ? frame.received_ms :
                                                              frame.produced_ms;
      s.latency_ms = start - produced;
    }
  }
  s.surface->release_view();
  s.upload_ms = gv::now_ms() - start;
}

/** Where the game is drawn in a region of this size (region pixels), letterboxed. */
static bool frame_rect_in_region(const GameViewSession &s,
                                 const int region_w,
                                 const int region_h,
                                 rctf *r_rect,
                                 int *r_frame_w,
                                 int *r_frame_h)
{
  int frame_w = s.texture_has_frame ? s.texture_w : s.sent_w;
  int frame_h = s.texture_has_frame ? s.texture_h : s.sent_h;
  if (frame_w <= 0 || frame_h <= 0 || region_w <= 0 || region_h <= 0) {
    return false;
  }
  const float scale = std::min(float(region_w) / frame_w, float(region_h) / frame_h);
  const float w = frame_w * scale;
  const float h = frame_h * scale;
  r_rect->xmin = std::floor((region_w - w) * 0.5f);
  r_rect->ymin = std::floor((region_h - h) * 0.5f);
  r_rect->xmax = r_rect->xmin + w;
  r_rect->ymax = r_rect->ymin + h;
  *r_frame_w = frame_w;
  *r_frame_h = frame_h;
  return true;
}

static bool draw_frame(GameViewSession &s, const ARegion *region)
{
  rctf rect;
  int frame_w, frame_h;
  if (s.texture == nullptr || !s.texture_has_frame ||
      !frame_rect_in_region(s, region->winx, region->winy, &rect, &frame_w, &frame_h))
  {
    return false;
  }
  /* Pixel-exact when the region matches the game, filtered when scaled. */
  GPU_texture_filter_mode(s.texture, BLI_rctf_size_x(&rect) != float(frame_w));

  GPUVertFormat *format = immVertexFormat();
  const uint pos = GPU_vertformat_attr_add(format, "pos", gpu::VertAttrType::SFLOAT_32_32);
  const uint tex_coord = GPU_vertformat_attr_add(
      format, "texCoord", gpu::VertAttrType::SFLOAT_32_32);
  immBindBuiltinProgram(GPU_SHADER_3D_IMAGE_COLOR);
  immUniformColor4f(1.0f, 1.0f, 1.0f, 1.0f);
  immBindTexture("image", s.texture);

  /* Frame rows are top-to-bottom, so the first row belongs at the top of the quad. */
  rctf uv;
  uv.xmin = 0.0f;
  uv.xmax = 1.0f;
  uv.ymin = 1.0f;
  uv.ymax = 0.0f;
  immRectf_with_texco(pos, tex_coord, rect, uv);

  immUnbindProgram();
  GPU_texture_unbind(s.texture);
  return true;
}

static void draw_text_line(const ARegion *region, const char *text, const float y, const bool boxed)
{
  const uiFontStyle *fstyle = UI_FSTYLE_WIDGET;
  const float width = float(ui::fontstyle_string_width(fstyle, text));
  const float x = std::floor(std::max(0.0f, (region->winx - width) * 0.5f));
  if (boxed) {
    const float fg[4] = {1.0f, 1.0f, 1.0f, 1.0f};
    const float bg[4] = {0.0f, 0.0f, 0.0f, 0.6f};
    GPU_blend(GPU_BLEND_ALPHA);
    ui::fontstyle_draw_simple_backdrop(fstyle, x, y, text, fg, bg);
    GPU_blend(GPU_BLEND_NONE);
  }
  else {
    uchar color[4];
    ui::theme::get_color_4ubv(TH_TEXT, color);
    ui::fontstyle_draw_simple(fstyle, x, y, text, color);
  }
}

/** One or two centered lines; boxed when drawn over the game. */
static void draw_center_message(const ARegion *region,
                                const char *title,
                                const std::string &detail,
                                const bool boxed)
{
  const int line_h = ui::fontstyle_height_max(UI_FSTYLE_WIDGET);
  const float spacing = std::floor(line_h * 1.8f);
  float y = std::floor(region->winy * 0.5f);
  if (!detail.empty()) {
    y += spacing * 0.5f;
  }
  draw_text_line(region, title, y, boxed);
  if (!detail.empty()) {
    /* First line only, and short enough for a narrow area. */
    std::string line = detail.substr(0, detail.find('\n'));
    if (line.size() > 160) {
      line = line.substr(0, 157) + "...";
    }
    draw_text_line(region, line.c_str(), y - spacing, boxed);
  }
}

static void draw_session_overlay(const GameViewSession &s,
                                 const ARegion *region,
                                 const bool drew_frame)
{
  const GameViewOverlay overlay = session_overlay(s.status, drew_frame);
  if (overlay.title) {
    draw_center_message(region, overlay.title, overlay.detail, drew_frame);
    return;
  }

  /* Running: a small hint at the bottom about input capture. */
  const char *hint = nullptr;
  if (g_capture == nullptr) {
    hint = s.user_paused ? "Paused" : "Click to play";
  }
  else if (gv::now_ms() - g_capture->started_ms < RELEASE_HINT_MS) {
    hint = "Shift+Esc gives the mouse and keyboard back to the editor";
  }
  if (hint) {
    draw_text_line(region, hint, std::floor(UI_UNIT_Y * 0.75f), true);
  }
}

static bool region_shows_game_view(const ARegion *region)
{
  bool found = false;
  foreach_game_view_area([&](ScrArea &area) {
    found |= BKE_area_find_region_type(&area, RGN_TYPE_WINDOW) == region;
  });
  return found;
}

static GameViewRegionDraws *session_region_draws(GameViewSession &s, const ARegion *region)
{
  for (GameViewRegionDraws &entry : s.region_draws) {
    if (entry.region == region) {
      return &entry;
    }
  }
  if (s.region_draws.size() >= 16) {
    /* Forget regions that are gone (their areas changed type or closed). */
    std::erase_if(s.region_draws, [](const GameViewRegionDraws &entry) {
      return !region_shows_game_view(entry.region);
    });
  }
  GameViewRegionDraws entry;
  entry.region = region;
  s.region_draws.push_back(entry);
  return &s.region_draws.back();
}

static void game_view_draw_main(const void * /*context*/, void *region_v)
{
  ARegion *region = static_cast<ARegion *>(region_v);
  ED_region_pixelspace(region);
  ui::theme::frame_buffer_clear(TH_BACK);

  GameViewSession *s = g_session;
  if (s == nullptr) {
    draw_center_message(region, "Press Play to run the game here", "", false);
    return;
  }
  session_upload_frame(*s);
  /* A detached game is not here any more: no stale frame. A stopped one stays under the message. */
  const bool drew_frame = s->status.mode != "window" && draw_frame(*s, region);
  draw_session_overlay(*s, region, drew_frame);

  GameViewRegionDraws *draws = session_region_draws(*s, region);
  if (draws) {
    draws->draws++;
    if (drew_frame) {
      draws->frames_drawn++;
      draws->frame_id = s->frame_id;
    }
  }
}

/* -------------------------------------------------------------------- */
/* Input capture operator. */

/** Sends a pointer event at the event's position, mapped through the letterbox. */
static void capture_send_pointer(GameViewSession &s,
                                 GameViewCapture &cap,
                                 const ARegion *region,
                                 const wmEvent *event,
                                 const gv::PointerType type,
                                 const int button,
                                 const float wheel_x,
                                 const float wheel_y)
{
  rctf rect;
  int frame_w, frame_h;
  if (!frame_rect_in_region(s, region->winx, region->winy, &rect, &frame_w, &frame_h)) {
    return;
  }
  const double scale_x = frame_w / double(BLI_rctf_size_x(&rect));
  const double scale_y = frame_h / double(BLI_rctf_size_y(&rect));

  double dx = 0.0, dy = 0.0;
  if (cap.have_last_xy) {
    /* Blender's y axis points up, the game's down. */
    dx = (event->xy[0] - cap.last_xy[0]) * scale_x;
    dy = -(event->xy[1] - cap.last_xy[1]) * scale_y;
  }
  cap.last_xy[0] = event->xy[0];
  cap.last_xy[1] = event->xy[1];
  cap.have_last_xy = true;

  if (!cap.grabbed) {
    const double region_x = event->xy[0] - region->winrct.xmin;
    const double region_y = event->xy[1] - region->winrct.ymin;
    cap.game_x = std::clamp((region_x - rect.xmin) * scale_x, 0.0, double(frame_w));
    cap.game_y = std::clamp((rect.ymax - region_y) * scale_y, 0.0, double(frame_h));
  }

  gv::PointerEvent pointer;
  pointer.type = type;
  pointer.x = cap.game_x;
  pointer.y = cap.game_y;
  pointer.button = std::max(button, 0);
  pointer.buttons = cap.buttons;
  if (type == gv::PointerType::Move) {
    pointer.dx = dx;
    pointer.dy = dy;
  }
  pointer.delta_x = wheel_x;
  pointer.delta_y = wheel_y;
  pointer.modifiers = game_view_dom_modifiers(event->modifier);
  s.surface->send_pointer(pointer);
}

/** The typed text of a key press, when it is printable. */
static std::string event_typed_text(const wmEvent *event)
{
  const unsigned char first = static_cast<unsigned char>(event->utf8_buf[0]);
  if (first < 0x20 || first == 0x7f) {
    return {};
  }
  const int size = BLI_str_utf8_size_or_error(event->utf8_buf);
  if (size <= 0 || size > int(sizeof(event->utf8_buf))) {
    return {};
  }
  return std::string(event->utf8_buf, size_t(size));
}

static void capture_send_key(GameViewSession &s,
                             GameViewCapture &cap,
                             const wmEvent *event,
                             const bool down)
{
  GameViewDomKey dom;
  if (!game_view_dom_key(event->type, &dom)) {
    return;
  }
  const uint32_t modifiers = game_view_dom_modifiers(event->modifier);
  auto held = std::find_if(cap.keys.begin(), cap.keys.end(), [&dom](const HeldKey &key) {
    return key.code == dom.code;
  });

  gv::KeyEvent key_event;
  key_event.down = down;
  key_event.code = dom.code;
  key_event.modifiers = modifiers;
  std::string key;
  if (down) {
    key = event_typed_text(event);
    if (key.empty()) {
      key = ((modifiers & gv::kShift) && dom.shift_key) ? dom.shift_key : dom.key;
    }
    key_event.repeat = (event->flag & WM_EVENT_IS_REPEAT) || held != cap.keys.end();
    if (held == cap.keys.end()) {
      cap.keys.push_back({dom.code, key});
    }
  }
  else {
    if (held == cap.keys.end()) {
      return; /* Pressed before the capture: the game never saw it go down. */
    }
    key = held->key;
    cap.keys.erase(held);
  }
  key_event.key = key;
  s.surface->send_key(key_event);
}

static void capture_end(wmOperator *op)
{
  GameViewCapture *cap = static_cast<GameViewCapture *>(op->customdata);
  if (cap == nullptr) {
    return;
  }
  capture_release(*cap);
  MEM_delete(cap);
  op->customdata = nullptr;
}

static bool game_view_capture_poll(bContext *C)
{
  const GameViewSession *s = g_session;
  const ARegion *region = CTX_wm_region(C);
  return s && g_capture == nullptr && session_accepts_input(*s) &&
         area_shows_game_view(CTX_wm_area(C)) && region &&
         region->regiontype == RGN_TYPE_WINDOW;
}

static wmOperatorStatus game_view_capture_invoke(bContext *C,
                                                 wmOperator *op,
                                                 const wmEvent *event)
{
  GameViewSession *s = g_session;
  ARegion *region = CTX_wm_region(C);
  if (s == nullptr || region == nullptr || g_capture) {
    return OPERATOR_CANCELLED | OPERATOR_PASS_THROUGH;
  }
  GameViewCapture *cap = MEM_new<GameViewCapture>(__func__);
  cap->win = CTX_wm_window(C);
  cap->region_rect = region->winrct;
  cap->started_ms = gv::now_ms();
  op->customdata = cap;
  g_capture = cap;

  s->surface->set_focus(true);
  WM_event_add_modal_handler(C, op);

  /* The click that captures reaches the game too: games request pointer lock on a click. */
  capture_send_pointer(*s, *cap, region, event, gv::PointerType::Move, 0, 0.0f, 0.0f);
  const int button = game_view_dom_button(event->type);
  if (button >= 0 && event->val == KM_PRESS) {
    cap->buttons |= game_view_dom_buttons_bit(button);
    capture_send_pointer(*s, *cap, region, event, gv::PointerType::Down, button, 0.0f, 0.0f);
  }
  capture_update_grab(*cap);
  tag_game_views(false);
  return OPERATOR_RUNNING_MODAL;
}

static wmOperatorStatus game_view_capture_modal(bContext *C,
                                                wmOperator *op,
                                                const wmEvent *event)
{
  GameViewCapture *cap = static_cast<GameViewCapture *>(op->customdata);
  GameViewSession *s = g_session;
  const ARegion *region = CTX_wm_region(C);
  const bool region_valid = region && region->regiontype == RGN_TYPE_WINDOW &&
                            area_shows_game_view(CTX_wm_area(C));
  if (cap == nullptr || cap->released || s == nullptr || !session_accepts_input(*s) ||
      !region_valid || event->type == WINDEACTIVATE)
  {
    capture_end(op);
    return OPERATOR_FINISHED | OPERATOR_PASS_THROUGH;
  }
  cap->region_rect = region->winrct;

  const int type = event->type;
  if (ISTIMER(type)) {
    return OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH;
  }
  /* Double clicks reach modal operators as presses; click and drag events are synthesized
   * from presses the game already got. */
  const bool press = event->val == KM_PRESS || event->val == KM_DBL_CLICK;
  const bool release = event->val == KM_RELEASE;
  const bool shift = (event->modifier & KM_SHIFT) != 0;

  /* Release chords. Esc alone belongs to the game while it has the cursor grabbed. */
  if (press && ((type == EVT_ESCKEY && (shift || !cap->grabbed)) || (type == EVT_F1KEY && shift)))
  {
    capture_end(op);
    return OPERATOR_FINISHED;
  }

  const bool inside = cap->grabbed || BLI_rcti_isect_pt_v(&region->winrct, event->xy);

  if (ISMOUSE_MOTION(type)) {
    if (!inside) {
      /* Let the editor's UI highlight what the mouse is over. */
      cap->have_last_xy = false;
      return OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH;
    }
    capture_send_pointer(*s, *cap, region, event, gv::PointerType::Move, 0, 0.0f, 0.0f);
    return OPERATOR_RUNNING_MODAL;
  }

  if (const int button = game_view_dom_button(type); button >= 0) {
    if (press && !inside) {
      /* Clicking elsewhere hands the mouse back to the editor, and the click goes through. */
      capture_end(op);
      return OPERATOR_FINISHED | OPERATOR_PASS_THROUGH;
    }
    const int bit = game_view_dom_buttons_bit(button);
    if (press) {
      cap->buttons |= bit;
      capture_send_pointer(*s, *cap, region, event, gv::PointerType::Down, button, 0.0f, 0.0f);
    }
    else if (release && (cap->buttons & bit)) {
      cap->buttons &= ~bit;
      capture_send_pointer(*s, *cap, region, event, gv::PointerType::Up, button, 0.0f, 0.0f);
    }
    return OPERATOR_RUNNING_MODAL;
  }

  if (ISMOUSE_WHEEL(type) || type == MOUSEPAN) {
    if (!inside) {
      return OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH;
    }
    float wheel_x = 0.0f, wheel_y = 0.0f;
    switch (type) {
      case WHEELUPMOUSE:
        wheel_y = -WHEEL_NOTCH;
        break;
      case WHEELDOWNMOUSE:
        wheel_y = WHEEL_NOTCH;
        break;
      case WHEELLEFTMOUSE:
        wheel_x = -WHEEL_NOTCH;
        break;
      case WHEELRIGHTMOUSE:
        wheel_x = WHEEL_NOTCH;
        break;
      case MOUSEPAN: {
        /* Trackpad scrolling, in CSS pixels, following the system's scroll direction. */
        const float dpr = game_view_dpr();
        wheel_x = -WM_event_absolute_delta_x(event) / dpr;
        wheel_y = WM_event_absolute_delta_y(event) / dpr;
        break;
      }
      default:
        break;
    }
    if (wheel_x != 0.0f || wheel_y != 0.0f) {
      capture_send_pointer(*s, *cap, region, event, gv::PointerType::Wheel, 0, wheel_x, wheel_y);
    }
    return OPERATOR_RUNNING_MODAL;
  }

  if (ISKEYBOARD(type)) {
    if (press || release) {
      capture_send_key(*s, *cap, event, press);
    }
    /* Every key belongs to the game while captured, including the editor's shortcuts. */
    return OPERATOR_RUNNING_MODAL;
  }

  return OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH;
}

static void game_view_capture_cancel(bContext * /*C*/, wmOperator *op)
{
  capture_end(op);
}

static void GAMESTUDIO_OT_game_view_capture(wmOperatorType *ot)
{
  ot->name = "Capture Game Input";
  ot->idname = "GAMESTUDIO_OT_game_view_capture";
  ot->description =
      "Send the mouse and keyboard to the game in the Game View (Shift+Esc gives them back)";

  ot->invoke = game_view_capture_invoke;
  ot->modal = game_view_capture_modal;
  ot->cancel = game_view_capture_cancel;
  ot->poll = game_view_capture_poll;

  ot->flag = OPTYPE_INTERNAL;
}

/* -------------------------------------------------------------------- */
/* Registration. */

void game_view_register()
{
  gs::EditorProvider provider;
  provider.idname = GAME_VIEW_IDNAME;
  provider.label = "Game View";
  provider.description = "The running game, docked in the editor";
  provider.icon = ICON_PLAY;
  provider.draw_main = game_view_draw_main;
  gs::providers().add(std::move(provider));
}

void game_view_operatortypes()
{
  WM_operatortype_append(GAMESTUDIO_OT_game_view_capture);
}

void game_view_keymap(wmKeyConfig *keyconf)
{
  WM_keymap_ensure(keyconf, GAME_VIEW_KEYMAP, SPACE_EXTENSION, RGN_TYPE_WINDOW);
}

static bool game_view_handler_poll(const wmWindow * /*win*/,
                                   const ScrArea *area,
                                   const ARegion * /*region*/,
                                   const wmEvent * /*event*/)
{
  return area_shows_game_view(area);
}

void game_view_region_init(wmWindowManager *wm, ARegion *region)
{
  if (wm == nullptr || wm->runtime->defaultconf == nullptr) {
    return;
  }
  wmKeyMap *keymap = WM_keymap_ensure(
      wm->runtime->defaultconf, GAME_VIEW_KEYMAP, SPACE_EXTENSION, RGN_TYPE_WINDOW);
  WM_event_add_keymap_handler_poll(&region->runtime->handlers, keymap, game_view_handler_poll);
}

/* -------------------------------------------------------------------- */
/* Python API (`_gamestudio.game_view_*`). */

static bool py_dict_set(PyObject *dict, const char *key, PyObject *value)
{
  if (value == nullptr) {
    return false;
  }
  const int result = PyDict_SetItemString(dict, key, value);
  Py_DECREF(value);
  return result == 0;
}

static PyObject *py_game_view_start(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  static const char *keywords[] = {
      "executable", "url", "app_dir", "width", "height", "log_file", nullptr};
  const char *executable = nullptr, *url = nullptr, *app_dir = "", *log_file = "";
  int width = 1280, height = 720;
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwargs,
                                   "ss|siis:game_view_start",
                                   const_cast<char **>(keywords),
                                   &executable,
                                   &url,
                                   &app_dir,
                                   &width,
                                   &height,
                                   &log_file))
  {
    return nullptr;
  }
  std::string error;
  if (!session_start(executable, url, app_dir, width, height, log_file, &error)) {
    PyErr_SetString(PyExc_RuntimeError,
                    error.empty() ? "could not start the Game View" : error.c_str());
    return nullptr;
  }
  Py_RETURN_NONE;
}

static PyObject *py_game_view_stop(PyObject * /*self*/, PyObject * /*args*/)
{
  session_stop(true);
  Py_RETURN_NONE;
}

static PyObject *py_game_view_status(PyObject * /*self*/, PyObject * /*args*/)
{
  PyObject *dict = PyDict_New();
  if (dict == nullptr) {
    return nullptr;
  }
  const GameViewSession *s = g_session;
  /* Read the surface directly: the timer that caches it does not run in background mode. */
  const gv::Status status = s ? s->surface->status() : gv::Status{};
  const gv::Stats stats = s ? s->surface->stats() : gv::Stats{};

  PyObject *frames = Py_BuildValue("{s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:K}",
                                   "received",
                                   (unsigned long long)stats.frames_received,
                                   "delivered",
                                   (unsigned long long)stats.frames_delivered,
                                   "uploaded",
                                   (unsigned long long)(s ? s->frames_uploaded : 0),
                                   "skipped",
                                   (unsigned long long)stats.frames_skipped,
                                   "superseded",
                                   (unsigned long long)stats.frames_superseded,
                                   "revoked",
                                   (unsigned long long)stats.frames_revoked,
                                   "failed",
                                   (unsigned long long)stats.frames_failed,
                                   "releases",
                                   (unsigned long long)stats.releases_sent);
  PyObject *views = PyList_New(0);
  if (s && views) {
    foreach_game_view_area([&](ScrArea &area) {
      const ARegion *region = BKE_area_find_region_type(&area, RGN_TYPE_WINDOW);
      if (region == nullptr) {
        return;
      }
      GameViewRegionDraws draws;
      for (const GameViewRegionDraws &entry : s->region_draws) {
        if (entry.region == region) {
          draws = entry;
        }
      }
      PyObject *view = Py_BuildValue("{s:i,s:i,s:O,s:K,s:K,s:K}",
                                     "width",
                                     int(region->winx),
                                     "height",
                                     int(region->winy),
                                     "visible",
                                     region->runtime->visible ? Py_True : Py_False,
                                     "draws",
                                     (unsigned long long)draws.draws,
                                     "frames_drawn",
                                     (unsigned long long)draws.frames_drawn,
                                     "frame_id",
                                     (unsigned long long)draws.frame_id);
      if (view) {
        PyList_Append(views, view);
        Py_DECREF(view);
      }
    });
  }
  const GameViewOverlay overlay = s ? session_overlay(status,
                                                      s->texture_has_frame &&
                                                          status.mode != "window") :
                                      GameViewOverlay{};
  PyObject *timings = Py_BuildValue("{s:d,s:d,s:d}",
                                    "upload_ms",
                                    s ? s->upload_ms : 0.0,
                                    "copy_ms",
                                    s ? s->copy_ms : 0.0,
                                    "latency_ms",
                                    s ? s->latency_ms : 0.0);
  const bool ok =
      py_dict_set(dict, "active", PyBool_FromLong(s != nullptr)) &&
      py_dict_set(dict,
                  "state",
                  PyUnicode_FromString(s ? game_view_state_name(status.state) : "none")) &&
      py_dict_set(dict, "message", PyUnicode_FromString(status.message.c_str())) &&
      py_dict_set(dict, "page_state", PyUnicode_FromString(status.page_state.c_str())) &&
      py_dict_set(dict, "mode", PyUnicode_FromString(status.mode.c_str())) &&
      py_dict_set(dict, "paused", PyBool_FromLong(status.paused)) &&
      py_dict_set(dict, "devtools", PyBool_FromLong(status.devtools)) &&
      py_dict_set(dict, "overlay", PyUnicode_FromString(overlay.title ? overlay.title : "")) &&
      py_dict_set(dict, "overlay_detail", PyUnicode_FromString(overlay.detail.c_str())) &&
      py_dict_set(dict, "user_paused", PyBool_FromLong(s && s->user_paused)) &&
      py_dict_set(dict, "host_pid", PyLong_FromUnsignedLong(status.host_pid)) &&
      py_dict_set(dict, "client", PyUnicode_FromString(status.host_client.c_str())) &&
      py_dict_set(dict, "adapter", PyUnicode_FromString(status.adapter_name.c_str())) &&
      py_dict_set(dict, "captured", PyBool_FromLong(g_capture != nullptr)) &&
      py_dict_set(dict, "cursor_grabbed", PyBool_FromLong(g_capture && g_capture->grabbed)) &&
      py_dict_set(dict, "width", PyLong_FromLong(s && s->texture_has_frame ? s->texture_w : 0)) &&
      py_dict_set(dict, "height", PyLong_FromLong(s && s->texture_has_frame ? s->texture_h : 0)) &&
      py_dict_set(dict, "frame_id", PyLong_FromUnsignedLongLong(s ? s->frame_id : 0)) &&
      py_dict_set(dict, "frames", frames) && py_dict_set(dict, "timings", timings) &&
      py_dict_set(dict, "views", views);
  if (!ok) {
    Py_DECREF(dict);
    return nullptr;
  }
  return dict;
}

static PyObject *py_game_view_detach(PyObject * /*self*/, PyObject * /*args*/)
{
  GameViewSession *s = g_session;
  if (s == nullptr || s->surface->status().state != gv::State::Connected) {
    Py_RETURN_FALSE;
  }
  if (g_capture) {
    capture_release(*g_capture);
  }
  s->surface->detach();
  Py_RETURN_TRUE;
}

static PyObject *py_game_view_devtools(PyObject * /*self*/, PyObject *args)
{
  int open = 1;
  if (!PyArg_ParseTuple(args, "|p:game_view_devtools", &open)) {
    return nullptr;
  }
  GameViewSession *s = g_session;
  if (s == nullptr || s->surface->status().state != gv::State::Connected) {
    Py_RETURN_FALSE;
  }
  if (open && g_capture) {
    /* The DevTools window takes the focus anyway. */
    capture_release(*g_capture);
  }
  s->surface->devtools(open != 0);
  Py_RETURN_TRUE;
}

static PyObject *py_game_view_set_paused(PyObject * /*self*/, PyObject *args)
{
  int paused = 0;
  if (!PyArg_ParseTuple(args, "p:game_view_set_paused", &paused)) {
    return nullptr;
  }
  GameViewSession *s = g_session;
  if (s == nullptr) {
    Py_RETURN_FALSE;
  }
  s->user_paused = paused != 0;
  session_apply_pause(*s);
  tag_game_views(true);
  Py_RETURN_TRUE;
}

static PyMethodDef game_view_methods[] = {
    {"game_view_start",
     reinterpret_cast<PyCFunction>(reinterpret_cast<void *>(py_game_view_start)),
     METH_VARARGS | METH_KEYWORDS,
     "game_view_start(executable, url, app_dir='', width=1280, height=720, log_file='')\n"
     "Run the game docked in the Game View: launch the runtime host (electron.exe and its app\n"
     "dir, or a packaged host) on `url`. Restarts a running game. The size is the largest\n"
     "visible Game View's when there is one. Raises RuntimeError with the reason on failure."},
    {"game_view_stop", py_game_view_stop, METH_NOARGS, "Stop the docked game (no-op if none)."},
    {"game_view_status",
     py_game_view_status,
     METH_NOARGS,
     "Game View state: a dict with active, state ('none', 'waiting', 'connected', 'closed',\n"
     "'failed', ...), message, page_state, mode, paused, devtools, captured, overlay (what the\n"
     "view says), frame size, stats and views (each Game View region's draws)."},
    {"game_view_detach",
     py_game_view_detach,
     METH_NOARGS,
     "Move the running game into its own window. Returns False when no game is connected."},
    {"game_view_devtools",
     py_game_view_devtools,
     METH_VARARGS,
     "game_view_devtools(open=True)\nOpen (or focus) Chromium DevTools for the running game in a\n"
     "window of its own, or close them. Returns False when no game is connected."},
    {"game_view_set_paused",
     py_game_view_set_paused,
     METH_VARARGS,
     "game_view_set_paused(paused)\nStop or resume the game's frames. Returns False without a game."},
    {nullptr, nullptr, 0, nullptr},
};

int game_view_add_python_methods(PyObject *module)
{
  return PyModule_AddFunctions(module, game_view_methods);
}

}  // namespace blender
