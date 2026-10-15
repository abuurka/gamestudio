// SPDX-License-Identifier: GPL-3.0-or-later
//
// gs_gameview: the editor side of the docked Game View (protocol: docs/fork/SURFACE.md).
//
// The game runs in a separate runtime-host process (Electron, MIT). It renders offscreen
// into GPU-shared textures and announces each frame over a loopback TCP link. This library:
//   1. listens on 127.0.0.1 and launches the host inside a kill-on-close job object;
//   2. on a worker thread, opens each frame's texture (DuplicateHandle +
//      OpenSharedResource1 on a D3D11 device on the host's GPU), copies it to a staging
//      texture, maps it and memcpys it into a CPU triple buffer, then releases the frame;
//   3. lets the main thread take the newest complete frame without blocking, and sends
//      configure / input / focus / pause / detach / devtools messages back to the host.
//
// Blender-independent: no Blender headers, no exceptions escape the API (every function is
// noexcept). Windows only in v1; on other platforms Surface::create() fails cleanly.
//
// Typical use (editor main thread):
//
//   std::string err;
//   auto surface = gs::gameview::Surface::create({.width = w, .height = h}, &err);
//   gs::gameview::HostLaunch launch;
//   launch.executable = ".../electron.exe";  launch.app_dir = ".../runtime/packages/host";
//   launch.url = "http://localhost:5173/";   launch.surface_port = surface->port();
//   launch.token = surface->token();         launch.adapter_luid = gs::gameview::default_adapter_luid();
//   launch.width = w;  launch.height = h;
//   gs::gameview::HostProcess host;
//   if (!gs::gameview::launch_host(launch, host, &err) || !surface->start(host, &err)) { report(err); }
//
//   // Every redraw of the Game View region:
//   gs::gameview::FrameView frame;
//   if (surface->acquire_latest(frame)) {   // true only when a NEWER frame is ready
//     upload(frame.pixels, frame.width, frame.height);   // e.g. GPU_texture_update_sub(UBYTE)
//     surface->release_view();
//   }
//   draw_last_uploaded_texture();
//
//   // Shutdown: destroy the surface first (the host exits when the link closes), give it a
//   // moment (host.wait(2000), off the main thread if you can), then destroy the HostProcess:
//   // closing its job kills whatever is left. Electron's process teardown takes ~0.2 s on an
//   // idle machine but was measured at several seconds with the CPU saturated.

#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace gs::gameview {

/** Surface protocol version spoken by this library (`protocol` in hello/welcome). */
inline constexpr int kProtocolVersion = 1;

/** Milliseconds on the library's monotonic clock. All local times in FrameView use it. */
double now_ms() noexcept;

// -------------------------------------------------------------------------------------
// Process launch
// -------------------------------------------------------------------------------------

struct ProcessSpec;

/**
 * A child process that lives in its own kill-on-close job object: destroying the
 * HostProcess (or the editor crashing) terminates the process and all its children.
 * Move-only. All methods may be called from any thread.
 */
class HostProcess {
 public:
  HostProcess() noexcept = default;
  ~HostProcess();
  HostProcess(HostProcess &&other) noexcept;
  HostProcess &operator=(HostProcess &&other) noexcept;
  HostProcess(const HostProcess &) = delete;
  HostProcess &operator=(const HostProcess &) = delete;

  bool valid() const noexcept { return process_ != nullptr; }
  uint32_t pid() const noexcept { return pid_; }
  /** Win32 process HANDLE (full access, including PROCESS_DUP_HANDLE). Owned: do not close. */
  void *native_handle() const noexcept { return process_; }
  bool running() const noexcept;
  /** Waits up to `timeout_ms` (-1: forever). True when the process has exited. */
  bool wait(int timeout_ms, int *exit_code = nullptr) const noexcept;
  /** Kills the process tree now (the job is terminated). */
  void terminate() noexcept;

 private:
  friend bool launch_process(const ProcessSpec &, HostProcess &, std::string *) noexcept;
  void reset() noexcept;
  void *process_ = nullptr;
  void *job_ = nullptr;
  uint32_t pid_ = 0;
};

/** A generic process launch (used by launch_host; also handy for tools). Strings are UTF-8. */
struct ProcessSpec {
  std::string executable;          ///< Full path; not searched on PATH.
  std::vector<std::string> args;   ///< Quoted for CommandLineToArgvW as needed.
  std::string working_dir;         ///< Empty: inherit.
  std::string log_file;            ///< Append stdout+stderr here. Empty: inherit the console.
};

/**
 * Starts `spec` suspended, assigns it to a new JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE job,
 * then resumes it, so no grandchild can escape the job. ELECTRON_RUN_AS_NODE is removed
 * from the child's environment (it would turn electron.exe into plain Node).
 */
bool launch_process(const ProcessSpec &spec, HostProcess &out, std::string *error) noexcept;

/** How to start the runtime host (docs/fork/SURFACE.md §3.1). */
struct HostLaunch {
  std::string executable;     ///< electron.exe, or a packaged host executable.
  std::string app_dir;        ///< runtime/packages/host when run from source; empty when packaged.
  std::string url;            ///< Game URL: loopback http(s) or file:.
  bool docked = true;         ///< false: --mode window (a normal window, no frames).
  uint16_t surface_port = 0;  ///< Surface::port()
  std::string token;          ///< Surface::token()
  int width = 1280;           ///< Initial texture size in pixels.
  int height = 720;
  float dpr = 1.0f;           ///< devicePixelRatio the page sees.
  int fps = 60;               ///< Offscreen frame rate (1..240).
  int max_in_flight = 3;      ///< Frames the editor may hold before the host revokes the oldest.
  uint64_t adapter_luid = 0;  ///< Pin Chromium to this GPU (see default_adapter_luid()); 0: Chromium chooses.
  std::string log_file;       ///< Host stdout+stderr; empty: inherit.
  /** More host options. They go after the "--" that launch_host() inserts (Electron needs it
   *  before URL arguments, see SURFACE.md §3.1), so they cannot be Chromium switches. */
  std::vector<std::string> extra_args;
};

/** Builds the host command line from `launch` and calls launch_process(). */
bool launch_host(const HostLaunch &launch, HostProcess &out, std::string *error) noexcept;

/**
 * LUID (as a 64-bit value: HighPart << 32 | LowPart) of the GPU that drives the primary
 * monitor, which is normally the one the editor renders on. 0 if unknown.
 */
uint64_t default_adapter_luid() noexcept;

// -------------------------------------------------------------------------------------
// Frames
// -------------------------------------------------------------------------------------

enum class PixelFormat : uint8_t {
  BGRA8,  ///< bytes B,G,R,A (Electron's default). Upload as RGBA8 with a "bgra" swizzle.
  RGBA8,
};

/**
 * A complete frame in CPU memory, owned by the Surface. Valid from a successful
 * acquire_latest() until release_view(). Rows are top-to-bottom, `row_pitch` bytes apart;
 * colours are sRGB-encoded, alpha is straight (the game page is normally opaque).
 */
struct FrameView {
  const uint8_t *pixels = nullptr;
  int width = 0;
  int height = 0;
  int row_pitch = 0;  ///< Bytes per row; width * 4 (tightly packed) in v1.
  PixelFormat format = PixelFormat::BGRA8;
  uint64_t frame_id = 0;

  /** The host's `ts` for this frame (its own clock, when Chromium handed it over). */
  double host_ts_ms = 0;
  /** host_ts_ms mapped onto now_ms() by ping/pong clock sync; NaN until the first sync. */
  double produced_ms = 0;
  double received_ms = 0;  ///< now_ms() when surface.frame arrived.
  double ready_ms = 0;     ///< now_ms() when the pixels were complete in CPU memory.

  /** Worker-thread cost of this frame, in milliseconds. */
  double open_ms = 0;    ///< DuplicateHandle + OpenSharedResource1 + CopyResource submit.
  double copy_ms = 0;    ///< Submit until the GPU copy finished (waited on a fence, not spun).
  double map_ms = 0;     ///< ID3D11DeviceContext::Map of the staging texture.
  double memcpy_ms = 0;  ///< Copy into the CPU triple buffer.
};

// -------------------------------------------------------------------------------------
// Input (editor → game). Coordinates are game pixels: texture pixels, origin top-left.
// -------------------------------------------------------------------------------------

enum Modifier : uint32_t { kShift = 1, kControl = 2, kAlt = 4, kMeta = 8 };

struct KeyEvent {
  bool down = true;
  std::string_view code;  ///< DOM KeyboardEvent.code, layout-independent: "KeyW", "Space", "ShiftLeft".
  std::string_view key;   ///< DOM KeyboardEvent.key for the current layout: "w", " ", "Shift".
  bool repeat = false;
  uint32_t modifiers = 0;  ///< Modifier bits held after this event.
};

enum class PointerType : uint8_t { Move, Down, Up, Wheel };

struct PointerEvent {
  PointerType type = PointerType::Move;
  double x = 0, y = 0;        ///< Position in game pixels.
  int button = 0;             ///< Button that changed (Down/Up): 0 left, 1 middle, 2 right, 3 back, 4 forward.
  int buttons = 0;            ///< Held after the event, DOM MouseEvent.buttons bits: 1 left, 2 right, 4 middle.
  double dx = 0, dy = 0;      ///< Relative motion in game pixels (needed while the cursor is grabbed).
  double delta_x = 0, delta_y = 0;  ///< Wheel delta in CSS pixels, +y = scroll down (~100 per notch).
  uint32_t modifiers = 0;
};

// -------------------------------------------------------------------------------------
// Surface
// -------------------------------------------------------------------------------------

struct SurfaceOptions {
  int width = 1280;  ///< Sent as the first surface.configure after the handshake.
  int height = 720;
  float dpr = 1.0f;
  uint64_t adapter_luid = 0;          ///< Fallback GPU if the host names none; 0: primary-monitor GPU.
  int connect_timeout_ms = 30000;     ///< The host must connect and say hello within this.
  int staging_textures = 3;           ///< Staging ring size (1..4), so Map never waits on a copy in flight.
};

enum class State : uint8_t {
  Created,    ///< create() done, start() not called yet.
  Waiting,    ///< Listening; the host has not completed the handshake yet.
  Connected,  ///< Handshake done; frames may flow.
  Closed,     ///< The link closed or the host exited cleanly. Final.
  Failed,     ///< Something went wrong; see Status::message. Final.
};

struct Status {
  State state = State::Created;
  std::string message;      ///< Why it is Closed/Failed, or the last non-fatal error.
  std::string page_state;   ///< Host's page: "loading", "running", "crashed", "error" (SURFACE.md §4.2).
  std::string mode;         ///< "docked" or "window".
  bool paused = false;
  bool devtools = false;    ///< The host has DevTools open for the game page.
  uint32_t host_pid = 0;
  std::string host_client;  ///< The host's self-description from hello.
  uint64_t adapter_luid = 0;
  std::string adapter_name;
};

enum class EventType : uint8_t {
  StateChanged,  ///< status() changed (connection state, page state, mode or pause).
  CursorGrab,    ///< The game asked to grab (true) or release (false) the cursor: Event::grab.
};

struct Event {
  EventType type = EventType::StateChanged;
  bool grab = false;
};

/** Cumulative counters; cheap to read from any thread. */
struct Stats {
  uint64_t frames_received = 0;    ///< surface.frame messages.
  uint64_t frames_delivered = 0;   ///< Copied into the triple buffer.
  uint64_t frames_acquired = 0;    ///< Returned by acquire_latest().
  uint64_t frames_skipped = 0;     ///< Superseded before they were opened (worker was busy).
  uint64_t frames_superseded = 0;  ///< Copied, but a newer copy finished first.
  uint64_t frames_revoked = 0;     ///< Revoked by the host (editor too slow).
  uint64_t frames_failed = 0;      ///< Could not be opened or copied (see Status::message).
  uint64_t releases_sent = 0;
};

/**
 * One docked Game View link. Threading:
 *   - acquire_latest() / release_view() / wait_for_frame(): one consumer thread (the main thread);
 *   - everything else: any thread.
 * Input and control calls queue a message and return at once; the worker sends it.
 */
class Surface {
 public:
  /** Binds 127.0.0.1 on an ephemeral port and generates a 128-bit token. Nothing runs yet. */
  static std::unique_ptr<Surface> create(const SurfaceOptions &options, std::string *error) noexcept;
  ~Surface();  ///< Closes the link (the host then exits) and joins the worker thread.
  Surface(const Surface &) = delete;
  Surface &operator=(const Surface &) = delete;

  uint16_t port() const noexcept;
  const std::string &token() const noexcept;

  /**
   * Starts the worker thread, which accepts the host's connection and streams frames.
   * `host` must have been launched with this surface's port and token; the Surface keeps
   * its own duplicate of the process handle (to DuplicateHandle frames and notice exit).
   */
  bool start(const HostProcess &host, std::string *error) noexcept;

  /**
   * Main thread, never blocks. If a frame newer than the last acquired one is ready, fills
   * `out` and returns true; call release_view() once you have uploaded it. Returns false
   * when there is nothing new: keep drawing what you uploaded last.
   */
  bool acquire_latest(FrameView &out) noexcept;
  /** Ends the access granted by acquire_latest(); `pixels` must not be used afterwards. */
  void release_view() noexcept;
  /** Blocks up to `timeout_ms` until a new frame is ready (for tools and tests). */
  bool wait_for_frame(int timeout_ms) noexcept;

  /** Called on the worker thread after each new frame. Must be quick and thread-safe
   *  (e.g. post a wake-up to the UI loop). Set it before start(). */
  void set_frame_callback(std::function<void()> callback) noexcept;

  /** Resize the game: width x height texture pixels, page devicePixelRatio `dpr`. */
  void configure(int width, int height, float dpr = 1.0f) noexcept;
  void send_key(const KeyEvent &event) noexcept;
  void send_pointer(const PointerEvent &event) noexcept;
  /** Captured / released: on false the game clears held keys and ends any cursor grab. */
  void set_focus(bool focused) noexcept;
  /** Stop/resume frame production (e.g. while the view is hidden). */
  void set_paused(bool paused) noexcept;
  /** Ask the host to move the game into its own window (frames stop). */
  void detach() noexcept;
  /** Open (or focus) Chromium DevTools for the game page in a window of its own; false closes it. */
  void devtools(bool open = true) noexcept;

  Status status() const noexcept;
  bool poll_event(Event &out) noexcept;
  Stats stats() const noexcept;

  struct Impl;

 private:
  explicit Surface(std::unique_ptr<Impl> impl) noexcept;
  std::unique_ptr<Impl> impl_;
};

}  // namespace gs::gameview
