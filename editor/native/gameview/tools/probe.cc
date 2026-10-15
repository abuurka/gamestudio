// SPDX-License-Identifier: GPL-3.0-or-later
//
// gs_gameview_probe: a fake editor that exercises the whole docked Game View pipeline with
// the real runtime host and a real game, and measures it:
//   1. finds (or starts) the game's dev server;
//   2. listens with gs_gameview::Surface and launches the host with launch_host();
//   3. receives frames for N seconds on this (main) thread, as Blender's region draw would;
//   4. drives WASD through input.key and checks over the live link that the player moved;
//   5. resizes mid-run with surface.configure, pauses briefly, saves PNG snapshots;
//   6. closes the link and checks that the host exits by itself.

#include "gs_gameview/gameview.hh"
#include "probe_devserver.hh"
#include "probe_link.hh"
#include "probe_png.hh"

#include <winsock2.h>
#include <windows.h>
#include <dxgi.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <mutex>
#include <thread>
#include <vector>

namespace gv = gs::gameview;
namespace fs = std::filesystem;
using gs::probe::path_utf8;
using gs::probe::utf8_path;

namespace {

const char kUsage[] = R"(usage: gs_gameview_probe [options]
  --url URL          game URL (default: reuse the project's running dev server, else start npx vite)
  --project DIR      game project (default <repo>/examples/demo-game)
  --repo DIR         repository root (default: the tree this probe was built from)
  --electron PATH    electron.exe (default <repo>/node_modules/electron/dist/electron.exe)
  --host-dir DIR     runtime host app (default <repo>/runtime/packages/host)
  --out DIR          PNGs and logs (default %TEMP%\gs_gameview_probe)
  --seconds N        streaming time after the first frame (default 10)
  --size WxH         initial size (default 1280x720)
  --resize WxH       mid-run surface.configure size (default 1920x1080; 0x0 disables)
  --resize-at S      seconds after the first frame (default 6)
  --fps N            host frame rate (default 60)
  --no-input         skip the WASD input test
  --own-server       start a new dev server even if one is running
  --detach-test      instead: stream 2 s docked, detach into a window, check, close
  --window           instead: launch the host in window mode (no frames), check, close
  --grab-test        instead: click (pointer lock -> cursor.grab), press Escape (release); use
                     with --url pointing at a page that locks on pointerdown
  --adapter-luid HEX pin the host (and so the consumer) to this GPU (default: primary monitor's)
  --list-adapters    print the DXGI adapters and their LUIDs, then exit
)";

struct Options {
  std::string repo = GS_REPO_ROOT;
  std::string project, url, electron, host_dir, out;
  double seconds = 10;
  int width = 1280, height = 720;
  int resize_w = 1920, resize_h = 1080;
  double resize_at = 6;
  int fps = 60;
  bool input = true;
  bool own_server = false;
  bool detach_test = false;
  bool window_mode = false;
  bool grab_test = false;
  bool list_adapters = false;
  uint64_t adapter_luid = 0;
};

bool parse_size(const char *text, int &w, int &h)
{
  char *end = nullptr;
  w = int(std::strtol(text, &end, 10));
  if (!end || *end != 'x') return false;
  h = int(std::strtol(end + 1, &end, 10));
  return *end == 0 && w >= 0 && h >= 0;
}

bool parse_options(int argc, char **argv, Options &o)
{
  for (int i = 1; i < argc; i++) {
    const std::string a = argv[i];
    const char *next = i + 1 < argc ? argv[i + 1] : nullptr;
    const auto value = [&]() -> const char * { return next ? argv[++i] : nullptr; };
    if (a == "--no-input") o.input = false;
    else if (a == "--own-server") o.own_server = true;
    else if (a == "--detach-test") o.detach_test = true;
    else if (a == "--window") o.window_mode = true;
    else if (a == "--grab-test") o.grab_test = true;
    else if (a == "--list-adapters") o.list_adapters = true;
    else if (a == "--adapter-luid" && next) o.adapter_luid = std::strtoull(value(), nullptr, 16);
    else if (a == "--url" && next) o.url = value();
    else if (a == "--project" && next) o.project = value();
    else if (a == "--repo" && next) o.repo = value();
    else if (a == "--electron" && next) o.electron = value();
    else if (a == "--host-dir" && next) o.host_dir = value();
    else if (a == "--out" && next) o.out = value();
    else if (a == "--seconds" && next) o.seconds = std::atof(value());
    else if (a == "--resize-at" && next) o.resize_at = std::atof(value());
    else if (a == "--fps" && next) o.fps = std::atoi(value());
    else if (a == "--size" && next) { if (!parse_size(value(), o.width, o.height)) return false; }
    else if (a == "--resize" && next) { if (!parse_size(value(), o.resize_w, o.resize_h)) return false; }
    else return false;
  }
  if (o.grab_test) {  // Its own short scenario: no WASD script, no resize.
    o.input = false;
    o.resize_w = o.resize_h = 0;
  }
  const fs::path repo = utf8_path(o.repo);
  if (o.project.empty()) o.project = path_utf8(repo / "examples" / "demo-game");
  if (o.electron.empty()) o.electron = path_utf8(repo / "node_modules" / "electron" / "dist" / "electron.exe");
  if (o.host_dir.empty()) o.host_dir = path_utf8(repo / "runtime" / "packages" / "host");
  if (o.out.empty()) o.out = path_utf8(fs::temp_directory_path() / "gs_gameview_probe");
  return o.seconds > 0 && o.width >= 16 && o.height >= 16 && o.fps > 0;
}

// --- Shared state between the frame loop (main thread) and the input script ---------------

struct Shot {
  std::string name;
  int width = 0, height = 0;
  gv::PixelFormat format = gv::PixelFormat::BGRA8;
  std::vector<uint8_t> pixels;
  int row_pitch = 0;
};

struct Shared {
  std::mutex mutex;
  std::condition_variable cv;
  bool stop = false;
  std::atomic<bool> input_done{false};
  std::string shot_request;  // main thread copies the next frame under this name
  std::vector<Shot> shots;

  void request_shot(const std::string &name)
  {
    std::lock_guard lock(mutex);
    shot_request = name;
  }
  /** Sleeps up to `ms`; false if the run is stopping. */
  bool sleep(int ms)
  {
    std::unique_lock lock(mutex);
    return !cv.wait_for(lock, std::chrono::milliseconds(ms), [&] { return stop; });
  }
};

struct InputResult {
  bool ran = false, ok = false;
  std::string note;
  std::array<double, 3> start{}, after_w{}, end{};
  double moved_w = 0, moved_asd = 0;
  double game_fps = 0;  // median of the game's own `stats` reports
};

double horizontal_distance(const std::array<double, 3> &a, const std::array<double, 3> &b)
{
  return std::hypot(a[0] - b[0], a[2] - b[2]);
}

/** Runs on its own thread: WASD (plus some pointer motion) and live-link position checks. */
void input_script(gv::Surface &surface, const gs::probe::DevSession &session, Shared &shared, InputResult &r)
{
  r.ran = true;
  gs::probe::LinkClient link;
  std::string peer, err;
  if (!link.connect(session.editor_port, session.token, err) || !link.find_game("GameStudioHost", 20000, peer, err)) {
    r.note = err;
    return;
  }
  // The scene is loaded once the Player exists.
  bool found = false;
  for (int i = 0; i < 100 && !found; i++) {
    found = link.position(peer, "CharacterController", "Player", r.start, err);
    if (!found && !shared.sleep(200)) return;
  }
  if (!found) {
    r.note = err;
    return;
  }
  surface.set_focus(true);
  if (!shared.sleep(800)) return;  // Let the player settle on the ground.
  link.position(peer, "CharacterController", "Player", r.start, err);
  shared.request_shot("frame-before-input");

  const auto hold = [&](const char *code, const char *key, int ms) {
    surface.send_key({.down = true, .code = code, .key = key});
    // Exercise input.pointer too: sweep the cursor while the key is held.
    for (int t = 0; t < ms; t += 100) {
      gv::PointerEvent move;
      move.x = 640 + 200 * std::cos(t / 300.0);
      move.y = 360 + 120 * std::sin(t / 300.0);
      surface.send_pointer(move);
      if (!shared.sleep(std::min(100, ms - t))) break;
    }
    surface.send_key({.down = false, .code = code, .key = key});
  };
  hold("KeyW", "w", 1500);
  if (!shared.sleep(300)) return;
  link.position(peer, "CharacterController", "Player", r.after_w, err);
  r.moved_w = horizontal_distance(r.start, r.after_w);
  shared.request_shot("frame-after-w");
  hold("KeyA", "a", 600);
  hold("KeyS", "s", 600);
  hold("KeyD", "d", 600);
  if (!shared.sleep(300)) return;
  link.position(peer, "CharacterController", "Player", r.end, err);
  r.moved_asd = horizontal_distance(r.after_w, r.end);
  r.ok = r.moved_w > 1.0;
  shared.input_done = true;
  // Until the run ends, keep the game's own frame-rate reports (live-link `stats`).
  while (shared.sleep(0)) {
    link.listen(250);
  }
  r.game_fps = link.game_fps();
}

// --- Statistics -----------------------------------------------------------------------------

struct Sample {
  double t;  // acquire time
  double open, copy, map, memcpy, ready_latency, acquire_latency;
};

double percentile(std::vector<double> v, double p)
{
  v.erase(std::remove_if(v.begin(), v.end(), [](double x) { return !std::isfinite(x); }), v.end());
  if (v.empty()) return NAN;
  std::sort(v.begin(), v.end());
  return v[size_t(std::lround(p * double(v.size() - 1)))];
}

void print_metric(const char *name, const std::vector<Sample> &s, double Sample::*field)
{
  std::vector<double> v;
  for (const Sample &x : s) v.push_back(x.*field);
  std::printf("    %-30s p50 %6.2f ms   p95 %6.2f ms\n", name, percentile(v, 0.5), percentile(v, 0.95));
}

const char *state_name(gv::State s)
{
  switch (s) {
    case gv::State::Created: return "created";
    case gv::State::Waiting: return "waiting";
    case gv::State::Connected: return "connected";
    case gv::State::Closed: return "closed";
    case gv::State::Failed: return "failed";
  }
  return "?";
}

struct Check {
  std::string name;
  bool ok;
  std::string detail;
};

void print_status(const gv::Status &s)
{
  std::printf("  [state] %s, page %s, mode %s%s%s%s\n", state_name(s.state), s.page_state.empty() ? "-" : s.page_state.c_str(),
              s.mode.empty() ? "-" : s.mode.c_str(), s.paused ? ", paused" : "", s.message.empty() ? "" : ": ",
              s.message.c_str());
}

/** Drains surface events; prints them. Returns false once the surface is closed or failed. */
bool pump_events(gv::Surface &surface, std::vector<bool> &grabs)
{
  gv::Event ev;
  while (surface.poll_event(ev)) {
    if (ev.type == gv::EventType::CursorGrab) {
      grabs.push_back(ev.grab);
      std::printf("  [cursor.grab] %s\n", ev.grab ? "grab" : "release");
    }
    else {
      print_status(surface.status());
    }
  }
  const gv::State s = surface.status().state;
  return s != gv::State::Closed && s != gv::State::Failed;
}

void list_adapters()
{
  IDXGIFactory1 *factory = nullptr;
  if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void **>(&factory)))) return;
  IDXGIAdapter1 *adapter = nullptr;
  for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; i++) {
    DXGI_ADAPTER_DESC1 d;
    adapter->GetDesc1(&d);
    const uint64_t luid = (uint64_t(uint32_t(d.AdapterLuid.HighPart)) << 32) | d.AdapterLuid.LowPart;
    std::printf("%016llx  %ls%s%s\n", static_cast<unsigned long long>(luid), d.Description,
                d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE ? "  (software)" : "",
                luid == gv::default_adapter_luid() ? "  (primary monitor)" : "");
    adapter->Release();
  }
  factory->Release();
}

}  // namespace

int main(int argc, char **argv)
{
  std::setvbuf(stdout, nullptr, _IONBF, 0);
  Options o;
  if (!parse_options(argc, argv, o)) {
    std::fputs(kUsage, stderr);
    return 2;
  }
  if (o.list_adapters) {
    list_adapters();
    return 0;
  }
  WSADATA wsa;
  WSAStartup(MAKEWORD(2, 2), &wsa);
  std::error_code ec;
  fs::create_directories(utf8_path(o.out), ec);
  const std::string host_log = path_utf8(utf8_path(o.out) / "host.log");
  fs::remove(utf8_path(host_log), ec);

  if (!fs::exists(utf8_path(o.electron))) {
    std::fprintf(stderr, "error: %s not found. Electron downloads lazily: run  node -e \"require('electron')\"  in %s\n",
                 o.electron.c_str(), o.repo.c_str());
    return 1;
  }

  // 1. Dev server.
  gs::probe::DevSession session;
  bool have_session = false;
  gv::HostProcess vite;
  std::string err;
  if (o.url.empty()) {
    if (!o.own_server && gs::probe::find_live_session(o.project, session)) {
      std::printf("dev server: reusing the running one (pid %u) at %s\n", session.pid, session.url.c_str());
    }
    else {
      std::printf("dev server: starting npx vite in %s ...\n", o.project.c_str());
      if (!gs::probe::start_vite(o.project, path_utf8(utf8_path(o.out) / "vite.log"), vite, session, err)) {
        std::fprintf(stderr, "error: %s\n", err.c_str());
        return 1;
      }
      std::printf("dev server: %s\n", session.url.c_str());
    }
    o.url = session.url;
    have_session = true;
  }
  else {
    have_session = gs::probe::read_session(o.project, session, err) && gs::probe::url_reachable(session.url, 500);
  }

  // 2. Surface + host.
  const double t_launch = gv::now_ms();
  gv::SurfaceOptions options;
  options.width = o.width;
  options.height = o.height;
  options.adapter_luid = o.adapter_luid ? o.adapter_luid : gv::default_adapter_luid();
  auto surface = gv::Surface::create(options, &err);
  if (!surface) {
    std::fprintf(stderr, "error: %s\n", err.c_str());
    return 1;
  }
  gv::HostLaunch launch;
  launch.executable = o.electron;
  launch.app_dir = o.host_dir;
  launch.url = o.url;
  launch.surface_port = surface->port();
  launch.token = surface->token();
  launch.width = o.width;
  launch.height = o.height;
  launch.fps = o.fps;
  launch.docked = !o.window_mode;
  launch.adapter_luid = options.adapter_luid;
  launch.log_file = host_log;
  gv::HostProcess host;
  if (!gv::launch_host(launch, host, &err) || !surface->start(host, &err)) {
    std::fprintf(stderr, "error: %s\n", err.c_str());
    return 1;
  }
  std::printf("surface: listening on 127.0.0.1:%u; host pid %u (log: %s)\n", surface->port(), host.pid(), host_log.c_str());
  std::printf("game: %s\n", o.url.c_str());

  // 3. Frame loop (this thread plays Blender's main thread).
  Shared shared;
  InputResult input;
  std::thread script;
  std::vector<Check> checks;
  std::map<std::pair<int, int>, std::vector<Sample>> samples;
  std::vector<bool> grabs;  // cursor.grab events, in order
  int grab_step = 0;
  double t_first = 0, t_connected = 0, t_resize = 0, t_first_resized = 0;
  double t_pause = 0, t_resume = 0;
  int frames_during_pause = 0;
  bool final_shot = false;
  double t_detach = 0, t_window = 0;
  bool failed = false;
  const bool resize = o.resize_w > 0 && o.resize_h > 0 && !o.detach_test;
  const double run_ms = (o.detach_test ? 2.0 : o.seconds) * 1000;

  while (true) {
    const double now = gv::now_ms();
    if (!pump_events(*surface, grabs)) {
      failed = surface->status().state == gv::State::Failed;
      break;
    }
    const gv::Status status = surface->status();
    if (!t_connected && status.state == gv::State::Connected) {
      t_connected = now;
      std::printf("connected after %.0f ms: %s; GPU %s\n", now - t_launch, status.host_client.c_str(),
                  status.adapter_name.c_str());
    }
    if (o.window_mode) {  // No frames: wait for the page, look at it for a moment, close.
      if (!t_window && status.mode == "window" && status.page_state == "running") {
        t_window = now;
        std::printf("window mode: the game page is running in a normal window\n");
      }
      if (t_window ? now - t_window > 2500 : now - t_launch > 30000) break;
      surface->wait_for_frame(50);
      continue;
    }
    if (!t_first && now - t_launch > 30000) {
      std::fprintf(stderr, "error: no frame within 30 s (%s)\n", status.message.c_str());
      failed = true;
      break;
    }
    if (t_first) {
      const double t = now - t_first;
      const bool script_done = !script.joinable() || shared.input_done;
      if (o.grab_test) {
        // Click the middle of the view (the page locks the pointer), then press Escape (it unlocks).
        const double cx = o.width / 2.0, cy = o.height / 2.0;
        if (grab_step == 0 && t >= 1000) {
          surface->set_focus(true);
          gv::PointerEvent p;
          p.x = cx;
          p.y = cy;
          surface->send_pointer(p);
          p.type = gv::PointerType::Down;
          p.buttons = 1;
          surface->send_pointer(p);
          p.type = gv::PointerType::Up;
          p.buttons = 0;
          surface->send_pointer(p);
          grab_step = 1;
        }
        if (grab_step == 1 && t >= 1500) {
          gv::PointerEvent p;  // Relative motion while grabbed.
          p.x = cx;
          p.y = cy;
          p.dx = 12;
          p.dy = -7;
          surface->send_pointer(p);
          grab_step = 2;
        }
        if (grab_step == 2 && t >= 2000) {
          shared.request_shot("frame-grab-locked");
          grab_step = 3;
        }
        if (grab_step == 3 && t >= 2500) {
          surface->send_key({.down = true, .code = "Escape", .key = "Escape"});
          surface->send_key({.down = false, .code = "Escape", .key = "Escape"});
          grab_step = 4;
        }
        if (grab_step == 4 && t >= 3500) break;
      }
      else if (o.detach_test) {
        if (!t_detach && t >= run_ms) {
          surface->detach();
          t_detach = now;
          std::printf("detach requested\n");
        }
        if (t_detach && !t_window && status.mode == "window") t_window = now;
        if (t_detach && (t_window ? now - t_window > 2500 : now - t_detach > 5000)) break;
      }
      else {
        if (t >= run_ms && (script_done || t >= run_ms + 15000)) break;
        if (resize && !t_resize && t >= o.resize_at * 1000) {
          surface->configure(o.resize_w, o.resize_h, 1.0f);
          t_resize = now;
          std::printf("surface.configure %dx%d sent at t=%.1f s\n", o.resize_w, o.resize_h, t / 1000);
        }
        if (!t_pause && t >= run_ms - 1700) {
          surface->set_paused(true);
          t_pause = now;
        }
        if (t_pause && !t_resume && now - t_pause >= 500) {
          surface->set_paused(false);
          t_resume = now;
        }
        if (!final_shot && t >= run_ms - 400) {
          shared.request_shot("frame-final");
          final_shot = true;
        }
      }
    }
    if (!surface->wait_for_frame(50)) continue;
    gv::FrameView f;
    if (!surface->acquire_latest(f)) continue;
    const double acquired = gv::now_ms();
    if (!t_first) {
      t_first = acquired;
      std::printf("first frame after %.0f ms: %dx%d frame %llu\n", acquired - t_launch, f.width, f.height,
                  static_cast<unsigned long long>(f.frame_id));
      if (o.input && have_session && !o.detach_test) {
        script = std::thread([&] {
          input_script(*surface, session, shared, input);
          shared.input_done = true;
        });
      }
    }
    if (t_resize && !t_first_resized && f.width == o.resize_w && f.height == o.resize_h) {
      t_first_resized = acquired;
      std::printf("first %dx%d frame %.0f ms after the configure\n", f.width, f.height, acquired - t_resize);
    }
    if (t_pause && !t_resume && f.received_ms > t_pause + 150) frames_during_pause++;
    samples[{f.width, f.height}].push_back({acquired, f.open_ms, f.copy_ms, f.map_ms, f.memcpy_ms,
                                            f.ready_ms - f.produced_ms, acquired - f.produced_ms});
    {
      std::lock_guard lock(shared.mutex);
      if (!shared.shot_request.empty()) {
        Shot shot{shared.shot_request, f.width, f.height, f.format,
                  std::vector<uint8_t>(f.pixels, f.pixels + size_t(f.row_pitch) * size_t(f.height)), f.row_pitch};
        shared.shot_request.clear();
        shared.shots.erase(std::remove_if(shared.shots.begin(), shared.shots.end(),
                                          [&](const Shot &s) { return s.name == shot.name; }),
                           shared.shots.end());
        shared.shots.push_back(std::move(shot));
      }
    }
    surface->release_view();
  }
  const double t_end = gv::now_ms();
  {
    std::lock_guard lock(shared.mutex);
    shared.stop = true;
  }
  shared.cv.notify_all();
  if (script.joinable()) script.join();

  // 4. Close the link; the host must exit by itself.
  const gv::Stats stats = surface->stats();
  const gv::Status final_status = surface->status();
  const double t_close = gv::now_ms();
  surface.reset();
  int exit_code = -1;
  // Electron's own process teardown is ~0.2 s on an idle machine but can take many seconds
  // under heavy load, so allow 30 s here. An editor would wait briefly, then close the job.
  const bool host_exited = host.wait(30000, &exit_code);
  const double host_exit_ms = gv::now_ms() - t_close;
  host = gv::HostProcess();  // Closes the job (kills anything left).
  bool host_reacted = false;  // The host logs this line as soon as it sees the link close.
  {
    std::ifstream log(utf8_path(host_log));
    for (std::string line; std::getline(log, line);) host_reacted |= line.find("surface link closed; exiting") != std::string::npos;
  }
  if (vite.valid()) {
    vite = gv::HostProcess();
    gs::probe::remove_session_if_ours(o.project, session.token);
  }

  // 5. Report.
  const double streamed_s = t_first ? (t_end - t_first) / 1000 : 0;
  std::printf("\n=== gs_gameview_probe report ===\n");
  std::printf("adapter: %s (LUID %016llx)\n", final_status.adapter_name.c_str(),
              static_cast<unsigned long long>(final_status.adapter_luid));
  std::printf("streamed %.1f s; frames received %llu (%.1f/s), delivered %llu, acquired %llu\n", streamed_s,
              static_cast<unsigned long long>(stats.frames_received),
              streamed_s ? double(stats.frames_received) / streamed_s : 0.0,
              static_cast<unsigned long long>(stats.frames_delivered), static_cast<unsigned long long>(stats.frames_acquired));
  std::printf("dropped: skipped %llu, superseded %llu, revoked by host %llu, failed %llu, never acquired %llu\n",
              static_cast<unsigned long long>(stats.frames_skipped), static_cast<unsigned long long>(stats.frames_superseded),
              static_cast<unsigned long long>(stats.frames_revoked), static_cast<unsigned long long>(stats.frames_failed),
              static_cast<unsigned long long>(stats.frames_delivered - stats.frames_acquired));
  std::printf("releases sent %llu\n", static_cast<unsigned long long>(stats.releases_sent));
  for (const auto &[size, s] : samples) {
    // Delivered rate over the time frames were flowing (gaps over 250 ms, e.g. the pause, excluded).
    double span = 0;
    size_t intervals = 0;
    for (size_t i = 1; i < s.size(); i++) {
      const double gap = s[i].t - s[i - 1].t;
      if (gap < 250) {
        span += gap;
        intervals++;
      }
    }
    std::printf("  %dx%d: %zu frames acquired, %.1f fps delivered\n", size.first, size.second, s.size(),
                span > 0 ? double(intervals) * 1000 / span : 0.0);
    print_metric("open+copy submit (worker)", s, &Sample::open);
    print_metric("GPU copy until fence (worker)", s, &Sample::copy);
    print_metric("Map (worker)", s, &Sample::map);
    print_metric("memcpy (worker)", s, &Sample::memcpy);
    print_metric("host ts -> CPU frame ready", s, &Sample::ready_latency);
    print_metric("host ts -> acquire (main)", s, &Sample::acquire_latency);
  }

  checks.push_back({"host connected and completed the handshake", t_connected != 0, ""});
  if (o.window_mode) {
    checks.push_back({"window mode: page running, no frames", t_window != 0 && stats.frames_received == 0,
                      std::to_string(stats.frames_received) + " frames"});
  }
  else {
    checks.push_back({"frames delivered", stats.frames_acquired >= (o.detach_test ? 10u : 100u),
                      std::to_string(stats.frames_acquired) + " acquired"});
  }
  for (const Shot &shot : shared.shots) {
    const std::string path = path_utf8(utf8_path(o.out) / (shot.name + ".png"));
    const bool bgra = shot.format == gv::PixelFormat::BGRA8;
    const gs::probe::ImageSummary sum = gs::probe::summarize(shot.pixels.data(), shot.width, shot.height, shot.row_pitch, bgra);
    std::string write_error;
    const bool written = gs::probe::write_png(path, shot.pixels.data(), shot.width, shot.height, shot.row_pitch, bgra, write_error);
    char detail[256];
    std::snprintf(detail, sizeof(detail), "%dx%d, mean luma %.0f, %.0f%% lit, %d colours -> %s", shot.width, shot.height,
                  sum.mean_luma, sum.lit_fraction * 100, sum.distinct_colors, written ? path.c_str() : write_error.c_str());
    // A game scene shows several colours; the grab-test page is one colour plus text.
    const bool varied = sum.distinct_colors >= (o.grab_test ? 1 : 4);
    checks.push_back({shot.name + " is not black", written && sum.mean_luma > 8 && varied, detail});
  }
  if (o.window_mode) {
  }
  else if (o.detach_test) {
    checks.push_back({"detach switched the host to window mode", t_window != 0,
                      t_window ? std::to_string(int(t_window - t_detach)) + " ms" : final_status.mode});
  }
  else {
    if (resize) {
      const auto it = samples.find({o.resize_w, o.resize_h});
      const size_t n = it == samples.end() ? 0 : it->second.size();
      checks.push_back({"frames continue after resizing to " + std::to_string(o.resize_w) + "x" + std::to_string(o.resize_h),
                        n >= 30, std::to_string(n) + " frames at the new size"});
    }
    if (t_pause) {
      checks.push_back({"pause stops frames, resume restarts them", frames_during_pause <= 2 && t_resume != 0,
                        std::to_string(frames_during_pause) + " frames while paused"});
    }
    if (o.input && have_session) {
      char detail[256];
      std::snprintf(detail, sizeof(detail), "W moved the player %.2f m ([%.2f %.2f %.2f] -> [%.2f %.2f %.2f]); A/S/D %.2f m%s%s",
                    input.moved_w, input.start[0], input.start[1], input.start[2], input.after_w[0], input.after_w[1],
                    input.after_w[2], input.moved_asd, input.note.empty() ? "" : "; ", input.note.c_str());
      checks.push_back({"input.key reached the game (live-link state.request)", input.ok, detail});
    }
    else if (o.input) {
      checks.push_back({"input test", false, "no live-link session for " + o.project + " (pass --project)"});
    }
  }
  checks.push_back({"host exited by itself when the link closed", host_reacted && host_exited && exit_code == 0,
                    std::string(host_reacted ? "it logged the close; " : "no close in its log; ") +
                        (host_exited ? "process gone after " + std::to_string(int(host_exit_ms)) + " ms, code " +
                                           std::to_string(exit_code)
                                     : "still running after 30 s")});
  if (o.grab_test) {
    checks.push_back({"requestPointerLock -> cursor.grab true, exitPointerLock -> false",
                      grabs.size() == 2 && grabs[0] && !grabs[1], std::to_string(grabs.size()) + " cursor.grab events"});
  }
  if (input.game_fps > 0) std::printf("the game itself reported %.0f fps (live-link stats, median)\n", input.game_fps);
  if (failed) checks.push_back({"surface stayed healthy", false, final_status.message});

  int failures = 0;
  std::printf("\n");
  for (const Check &c : checks) {
    failures += !c.ok;
    std::printf("%s  %s%s%s\n", c.ok ? "PASS" : "FAIL", c.name.c_str(), c.detail.empty() ? "" : "  (", c.detail.empty() ? "" : (c.detail + ")").c_str());
  }
  std::printf("\n%d/%zu checks passed\n", int(checks.size()) - failures, checks.size());
  if (failures) {
    std::ifstream log(utf8_path(host_log));
    std::vector<std::string> lines;
    for (std::string line; std::getline(log, line);) lines.push_back(line);
    std::printf("\nlast lines of %s:\n", host_log.c_str());
    for (size_t i = lines.size() > 20 ? lines.size() - 20 : 0; i < lines.size(); i++) std::printf("  %s\n", lines[i].c_str());
  }
  WSACleanup();
  return failures ? 1 : 0;
}
