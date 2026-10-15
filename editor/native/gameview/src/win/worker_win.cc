// SPDX-License-Identifier: GPL-3.0-or-later
// The Windows worker thread: owns the loopback socket and the D3D11 bridge.
//
// It sleeps in WaitForMultipleObjects on: the wake event (messages queued or stop), the
// socket event (WSAEventSelect), the bridge's fence event (a GPU copy finished) and the host
// process handle (it exited). Nothing polls or spins.

#include "d3d11_bridge.hh"
#include "win_util.hh"

#include <cmath>
#include <cstdio>
#include <iterator>
#include <optional>
#include <thread>

#include "../clock_sync.hh"
#include "../protocol.hh"
#include "../surface_impl.hh"

namespace gs::gameview {

namespace {

constexpr double kHelloTimeoutMs = 10000;
constexpr size_t kMaxLineBytes = 1 << 20;
constexpr size_t kMaxUnsentBytes = 8 << 20;
constexpr double kPingIntervalMs = 1000;
constexpr int kFastPings = 5;  // The first pings go out 50 ms apart to sync the clock quickly.

class WinWorker final : public Worker {
 public:
  explicit WinWorker(Surface::Impl &impl) : impl_(impl) {}
  ~WinWorker() override;

  bool init(std::string *error);
  uint16_t port() const noexcept override { return port_; }
  bool start(const HostProcess &host, std::string *error) noexcept override;
  void wake() noexcept override
  {
    if (wake_event_) SetEvent(wake_event_);
  }
  void stop() noexcept override;

 private:
  // Closing: the host closed the link; wait briefly for its exit code (crash or clean exit).
  enum class Phase { Accepting, Handshake, Streaming, Closing, Done };

  void run() noexcept;
  void loop();
  void on_wake();
  void on_listener();
  void on_connection();
  void on_fence();
  void on_process_exit();
  void on_timeout();
  void read_available();
  void on_line(std::string_view line);
  void on_hello(const json::Value &message);
  void on_frame(const json::Value &message);
  void on_revoke(const json::Value &message);
  void on_state(const json::Value &message);
  void on_pong(const json::Value &message);
  void start_copies();
  void finish_copies();
  void send(std::string message);
  void flush();
  void drop_connection(const std::string &code, const std::string &why);
  void finish(State state, std::string message);
  void note_error(std::string message);
  double next_deadline() const;

  Surface::Impl &impl_;
  uint16_t port_ = 0;
  bool wsa_started_ = false;
  SOCKET listener_ = INVALID_SOCKET;
  SOCKET conn_ = INVALID_SOCKET;
  HANDLE listen_event_ = nullptr;
  HANDLE conn_event_ = nullptr;
  HANDLE wake_event_ = nullptr;
  HANDLE process_ = nullptr;
  std::thread thread_;
  std::atomic<bool> stopping_{false};

  Phase phase_ = Phase::Accepting;
  double connect_deadline_ = 0;
  double hello_deadline_ = 0;
  double closing_deadline_ = 0;
  std::string last_handshake_error_;
  /** The host said `bye` (it ends the link on purpose) and why. A link closed without one means
   *  the host crashed or was killed. */
  bool goodbye_ = false;
  std::string goodbye_reason_;
  std::string inbuf_;
  std::string outbuf_;
  bool write_blocked_ = false;

  D3D11Bridge bridge_;
  std::optional<protocol::Frame> pending_;  // newest frame not opened yet
  std::vector<D3D11Bridge::Finished> finished_;
  ClockSync clock_;
  uint64_t next_ping_id_ = 1;
  int pings_sent_ = 0;
  double next_ping_ms_ = 0;
  struct PingRecord {
    uint64_t id = 0;
    double sent_ms = 0;
  } pings_[8];
};

WinWorker::~WinWorker()
{
  stop();
  if (listener_ != INVALID_SOCKET) closesocket(listener_);
  if (conn_ != INVALID_SOCKET) closesocket(conn_);
  win::close_handle(listen_event_);
  win::close_handle(conn_event_);
  win::close_handle(wake_event_);
  win::close_handle(process_);
  if (wsa_started_) WSACleanup();
}

bool WinWorker::init(std::string *error)
{
  const auto fail = [&](std::string message) {
    if (error) *error = std::move(message);
    return false;
  };
  WSADATA wsa;
  if (const int rc = WSAStartup(MAKEWORD(2, 2), &wsa)) {
    return fail(win::error_text("WSAStartup", unsigned(rc)));
  }
  wsa_started_ = true;
  listener_ = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_NO_HANDLE_INHERIT);
  if (listener_ == INVALID_SOCKET) {
    return fail(win::error_text("socket", WSAGetLastError()));
  }
  BOOL exclusive = TRUE;  // No other process may bind the same port.
  setsockopt(listener_, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char *>(&exclusive), sizeof(exclusive));
  sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);  // Loopback only (docs/fork/SURFACE.md §7).
  addr.sin_port = 0;                              // Ephemeral port.
  if (bind(listener_, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == SOCKET_ERROR ||
      listen(listener_, 4) == SOCKET_ERROR)
  {
    return fail(win::error_text("cannot listen on 127.0.0.1", WSAGetLastError()));
  }
  int len = sizeof(addr);
  getsockname(listener_, reinterpret_cast<sockaddr *>(&addr), &len);
  port_ = ntohs(addr.sin_port);

  listen_event_ = WSACreateEvent();
  conn_event_ = WSACreateEvent();
  wake_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  if (listen_event_ == WSA_INVALID_EVENT || conn_event_ == WSA_INVALID_EVENT || !wake_event_) {
    return fail("cannot create the worker's events");
  }
  if (WSAEventSelect(listener_, listen_event_, FD_ACCEPT) == SOCKET_ERROR) {
    return fail(win::error_text("WSAEventSelect", WSAGetLastError()));
  }
  return true;
}

bool WinWorker::start(const HostProcess &host, std::string *error) noexcept
{
  if (thread_.joinable() || process_) {
    if (error) *error = "Surface::start was already called";
    return false;
  }
  if (!DuplicateHandle(GetCurrentProcess(), host.native_handle(), GetCurrentProcess(), &process_,
                       PROCESS_DUP_HANDLE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, 0))
  {
    if (error) *error = win::error_text("cannot keep a handle to the host process", GetLastError());
    process_ = nullptr;
    return false;
  }
  impl_.update_status([](Status &s) { s.state = State::Waiting; });
  try {
    thread_ = std::thread([this] { run(); });
  }
  catch (...) {
    if (error) *error = "cannot start the surface worker thread";
    return false;
  }
  return true;
}

void WinWorker::stop() noexcept
{
  stopping_ = true;
  wake();
  if (thread_.joinable()) {
    thread_.join();
  }
}

void WinWorker::run() noexcept
{
  try {
    loop();
  }
  catch (const std::exception &e) {
    finish(State::Failed, std::string("surface worker error: ") + e.what());
  }
  catch (...) {
    finish(State::Failed, "surface worker error");
  }
  // Closing the socket is what tells the host to exit (docs/fork/SURFACE.md §3.3).
  if (conn_ != INVALID_SOCKET) {
    shutdown(conn_, SD_BOTH);
    closesocket(conn_);
    conn_ = INVALID_SOCKET;
  }
  if (phase_ != Phase::Done) {
    finish(State::Closed, "the editor closed the link");
  }
}

void WinWorker::loop()
{
  connect_deadline_ = now_ms() + impl_.options.connect_timeout_ms;
  while (!stopping_ && phase_ != Phase::Done) {
    HANDLE handles[4];
    DWORD count = 0;
    handles[count++] = wake_event_;
    DWORD socket_index = MAXDWORD;
    if (phase_ != Phase::Closing) {
      socket_index = count;
      handles[count++] = phase_ == Phase::Accepting ? listen_event_ : conn_event_;
    }
    const DWORD fence_index = count;
    if (HANDLE fence = bridge_.wait_handle()) {
      handles[count++] = fence;
    }
    const DWORD process_index = count;  // Last: drain the socket before reacting to an exit.
    handles[count++] = process_;

    const double wait = next_deadline() - now_ms();
    const DWORD timeout = wait <= 0 ? 0 : wait > 3.6e6 ? INFINITE : DWORD(std::ceil(wait));
    const DWORD result = WaitForMultipleObjects(count, handles, FALSE, timeout);
    if (stopping_) {
      break;
    }
    if (result == WAIT_TIMEOUT) {
      on_timeout();
    }
    else if (result == WAIT_OBJECT_0) {
      on_wake();
    }
    else if (result == WAIT_OBJECT_0 + socket_index) {
      phase_ == Phase::Accepting ? on_listener() : on_connection();
    }
    else if (result == WAIT_OBJECT_0 + process_index) {
      on_process_exit();
    }
    else if (result == WAIT_OBJECT_0 + fence_index) {
      on_fence();
    }
    else {
      finish(State::Failed, win::error_text("WaitForMultipleObjects", GetLastError()));
    }
  }
}

double WinWorker::next_deadline() const
{
  switch (phase_) {
    case Phase::Accepting:
      return connect_deadline_;
    case Phase::Handshake:
      return hello_deadline_ < connect_deadline_ ? hello_deadline_ : connect_deadline_;
    case Phase::Streaming:
      return next_ping_ms_;
    case Phase::Closing:
      return closing_deadline_;
    default:
      return now_ms() + 1e9;
  }
}

void WinWorker::on_timeout()
{
  const double now = now_ms();
  if (phase_ == Phase::Closing) {
    // The process is still going away. Its bye says whether that was on purpose.
    if (goodbye_) {
      finish(State::Closed, "the runtime host quit" + (goodbye_reason_.empty() ? "" : ": " + goodbye_reason_));
    }
    else {
      finish(State::Failed, "the runtime host stopped unexpectedly");
    }
    return;
  }
  if (phase_ == Phase::Streaming) {
    PingRecord &record = pings_[next_ping_id_ % std::size(pings_)];
    record = {next_ping_id_, now};
    send(protocol::ping(next_ping_id_++, now));
    flush();
    pings_sent_++;
    next_ping_ms_ = now + (pings_sent_ < kFastPings ? 50 : kPingIntervalMs);
    return;
  }
  if (now >= connect_deadline_) {
    std::string message = "the runtime host did not connect within " +
                          std::to_string(impl_.options.connect_timeout_ms / 1000) + " s";
    if (!last_handshake_error_.empty()) message += " (last handshake error: " + last_handshake_error_ + ")";
    finish(State::Failed, std::move(message));
  }
  else if (phase_ == Phase::Handshake && now >= hello_deadline_) {
    drop_connection("auth", "no hello within 10 s");
  }
}

void WinWorker::on_wake()
{
  if (phase_ != Phase::Streaming) {
    return;  // Queued messages wait for the handshake.
  }
  std::string queued;
  {
    std::lock_guard lock(impl_.outbox_mutex);
    queued.swap(impl_.outbox);
  }
  if (!queued.empty()) {
    outbuf_ += queued;
    flush();
  }
}

void WinWorker::on_listener()
{
  WSANETWORKEVENTS events;
  if (WSAEnumNetworkEvents(listener_, listen_event_, &events) == SOCKET_ERROR || !(events.lNetworkEvents & FD_ACCEPT)) {
    return;
  }
  SOCKET s = accept(listener_, nullptr, nullptr);
  if (s == INVALID_SOCKET) {
    return;
  }
  BOOL nodelay = TRUE;
  setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char *>(&nodelay), sizeof(nodelay));
  if (WSAEventSelect(s, conn_event_, FD_READ | FD_WRITE | FD_CLOSE) == SOCKET_ERROR) {
    closesocket(s);
    return;
  }
  conn_ = s;
  inbuf_.clear();
  outbuf_.clear();
  write_blocked_ = false;
  phase_ = Phase::Handshake;
  hello_deadline_ = now_ms() + kHelloTimeoutMs;
}

void WinWorker::on_connection()
{
  WSANETWORKEVENTS events;
  if (WSAEnumNetworkEvents(conn_, conn_event_, &events) == SOCKET_ERROR) {
    finish(State::Failed, win::error_text("socket", WSAGetLastError()));
    return;
  }
  if (events.lNetworkEvents & FD_WRITE) {
    write_blocked_ = false;
    flush();
  }
  if (events.lNetworkEvents & (FD_READ | FD_CLOSE)) {
    read_available();
  }
  if (conn_ != INVALID_SOCKET && phase_ != Phase::Done && (events.lNetworkEvents & FD_CLOSE)) {
    if (phase_ == Phase::Handshake) {
      drop_connection("", "the peer closed the connection during the handshake");
    }
    else {
      closesocket(conn_);
      conn_ = INVALID_SOCKET;
      // Wait for the exit code. A process that is torn down (GPU resources) can take seconds to
      // signal; without a bye the link loss is a crash anyway, so do not keep the user waiting.
      phase_ = Phase::Closing;
      closing_deadline_ = now_ms() + (goodbye_ ? 3000 : 1000);
    }
  }
}

void WinWorker::read_available()
{
  char buffer[64 * 1024];
  while (conn_ != INVALID_SOCKET) {
    const int n = recv(conn_, buffer, int(sizeof(buffer)), 0);
    if (n <= 0) {
      break;  // WSAEWOULDBLOCK, closed, or error: FD_CLOSE reports the latter two.
    }
    inbuf_.append(buffer, size_t(n));
    size_t start = 0;
    for (size_t nl; (nl = inbuf_.find('\n', start)) != std::string::npos; start = nl + 1) {
      on_line(std::string_view(inbuf_).substr(start, nl - start));
      if (phase_ == Phase::Done || phase_ == Phase::Accepting) {
        return;  // The link was dropped while handling a line.
      }
    }
    inbuf_.erase(0, start);
    if (inbuf_.size() > kMaxLineBytes) {
      drop_connection("protocol", "message line longer than 1 MiB");
      return;
    }
  }
  // Every frame in this batch has been seen; only the newest is still pending.
  start_copies();
  finish_copies();
}

void WinWorker::on_line(std::string_view line)
{
  while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) {
    line.remove_suffix(1);
  }
  if (line.empty()) {
    return;
  }
  json::Value message;
  if (!json::parse(line, message) || message.type != json::Value::Type::Object) {
    if (phase_ == Phase::Handshake) {
      drop_connection("protocol", "the first line is not a JSON object");
    }
    return;  // Streaming: drop bad lines.
  }
  if (phase_ == Phase::Handshake) {
    on_hello(message);
    return;
  }
  const std::string_view type = message.str("t");
  if (type == "surface.frame") on_frame(message);
  else if (type == "surface.revoke") on_revoke(message);
  else if (type == "surface.state") on_state(message);
  else if (type == "pong") on_pong(message);
  else if (type == "cursor.grab") impl_.push_event({EventType::CursorGrab, message.flag("grab", false)});
  else if (type == "error") note_error("host: " + std::string(message.str("msg")));
  else if (type == "bye") {
    goodbye_ = true;
    goodbye_reason_ = std::string(message.str("reason"));
  }
  // Unknown types are ignored (docs/fork/SURFACE.md §8).
}

void WinWorker::on_hello(const json::Value &message)
{
  protocol::Hello hello;
  std::string why;
  if (!protocol::parse_hello(message, hello, why)) {
    drop_connection(message.str("t") == "hello" ? "protocol" : "auth", why);
    return;
  }
  if (!protocol::token_equals(hello.token, impl_.token)) {
    drop_connection("auth", "invalid token");
    return;
  }
  if (hello.mode == "docked") {
    if (!hello.supports_d3d11_nt) {
      drop_connection("protocol", "the host offers no handle kind this editor can open (needs d3d11-nt)");
      return;
    }
    std::string error;
    D3D11Bridge::AdapterChoice choice;
    choice.host_luid = hello.adapter_luid;
    choice.vendor_id = hello.gpu_vendor_id;
    choice.device_id = hello.gpu_device_id;
    choice.fallback_luid = impl_.options.adapter_luid;
    if (!bridge_.init(choice, impl_.options.staging_textures, error)) {
      send(protocol::error("gpu", error));
      flush();
      finish(State::Failed, "cannot open frames: " + error);
      return;
    }
  }
  // Exactly one host per surface: stop listening.
  closesocket(listener_);
  listener_ = INVALID_SOCKET;
  phase_ = Phase::Streaming;
  impl_.update_status([&](Status &s) {
    s.state = State::Connected;
    s.message.clear();
    s.mode = hello.mode;
    s.host_pid = hello.pid;
    s.host_client = hello.client;
    s.adapter_luid = bridge_.adapter_luid();
    s.adapter_name = bridge_.adapter_name();
  });
  send(protocol::welcome());
  send(protocol::configure(impl_.options.width, impl_.options.height, impl_.options.dpr));
  next_ping_ms_ = now_ms();  // Sync the clock right away.
  on_wake();                 // Anything queued before the handshake.
  flush();
}

void WinWorker::on_frame(const json::Value &message)
{
  protocol::Frame frame;
  std::string why;
  if (!protocol::parse_frame(message, frame, why)) {
    note_error(why);
    return;
  }
  frame.received_ms = now_ms();
  impl_.counters.received++;
  if (!bridge_.ready()) {
    send(protocol::release(frame.frame_id));
    impl_.counters.releases++;
    impl_.counters.failed++;
    return;
  }
  if (pending_) {  // Superseded before we could open it.
    send(protocol::release(pending_->frame_id));
    impl_.counters.releases++;
    impl_.counters.skipped++;
  }
  pending_ = frame;
}

void WinWorker::on_revoke(const json::Value &message)
{
  for (const uint64_t id : protocol::parse_revoke(message)) {
    impl_.counters.revoked++;
    if (pending_ && pending_->frame_id == id) {
      pending_.reset();
    }
    else {
      bridge_.mark_revoked(id);
    }
  }
}

void WinWorker::on_state(const json::Value &message)
{
  impl_.update_status([&](Status &s) {
    s.page_state = std::string(message.str("state", s.page_state));
    s.mode = std::string(message.str("mode", s.mode));
    s.paused = message.flag("paused", s.paused);
    s.devtools = message.flag("devtools", s.devtools);
    const std::string_view detail = message.str("msg", message.str("reason"));
    if (!detail.empty()) s.message = "host page " + s.page_state + ": " + std::string(detail);
  });
}

void WinWorker::on_pong(const json::Value &message)
{
  const double id = message.num("re", -1);
  const double host_ts = message.num("ts", NAN);
  for (const PingRecord &record : pings_) {
    if (record.id != 0 && double(record.id) == id && std::isfinite(host_ts)) {
      clock_.add(record.sent_ms, host_ts, now_ms());
      return;
    }
  }
}

void WinWorker::on_fence()
{
  finish_copies();
}

void WinWorker::start_copies()
{
  while (pending_ && bridge_.can_begin()) {
    const protocol::Frame frame = *pending_;
    pending_.reset();
    std::string error;
    if (!bridge_.begin(frame, process_, error)) {
      impl_.counters.failed++;
      note_error(error);
      send(protocol::release(frame.frame_id));
      impl_.counters.releases++;
    }
  }
  flush();
}

void WinWorker::finish_copies()
{
  finished_.clear();
  bridge_.collect(finished_);
  if (finished_.empty()) {
    return;
  }
  // The GPU is done with every source texture: hand them back first.
  for (const auto &done : finished_) {
    if (!done.revoked) {
      send(protocol::release(done.frame.frame_id));
      impl_.counters.releases++;
    }
  }
  flush();

  // Only the newest finished copy is worth reading back.
  const auto &newest = finished_.back();
  impl_.counters.superseded += finished_.size() - 1;
  FrameSlot &slot = impl_.frames.back();
  const size_t bytes = size_t(newest.frame.width) * 4 * size_t(newest.frame.height);
  slot.pixels.resize(bytes);
  double map_ms = 0, memcpy_ms = 0;
  std::string error;
  if (bridge_.read(newest, slot.pixels.data(), map_ms, memcpy_ms, error)) {
    FrameView &v = slot.view;
    v.width = newest.frame.width;
    v.height = newest.frame.height;
    v.row_pitch = newest.frame.width * 4;
    v.format = newest.frame.format;
    v.frame_id = newest.frame.frame_id;
    v.host_ts_ms = newest.frame.ts_ms;
    v.produced_ms = clock_.to_local(newest.frame.ts_ms);
    v.received_ms = newest.frame.received_ms;
    v.ready_ms = now_ms();
    v.open_ms = newest.open_ms;
    v.copy_ms = newest.copy_ms;
    v.map_ms = map_ms;
    v.memcpy_ms = memcpy_ms;
    impl_.frames.publish();
    impl_.counters.delivered++;
    impl_.frame_published();
  }
  else {
    impl_.counters.failed++;
    note_error(error);
  }
  for (const auto &done : finished_) {
    bridge_.recycle(done);
  }
  start_copies();  // Staging slots are free again.
}

void WinWorker::on_process_exit()
{
  if (conn_ != INVALID_SOCKET) {
    read_available();  // Final state messages.
  }
  DWORD code = 0;
  GetExitCodeProcess(process_, &code);
  if (phase_ == Phase::Done) {
    return;
  }
  const bool connected = phase_ == Phase::Streaming || phase_ == Phase::Closing;
  if (code == 0 && connected) {
    finish(State::Closed, goodbye_ && !goodbye_reason_.empty() ? "the runtime host quit: " + goodbye_reason_ :
                                                                 "the runtime host exited");
  }
  else {
    char text[32];
    std::snprintf(text, sizeof(text), code > 0xFFFF ? "0x%08lX" : "%lu", static_cast<unsigned long>(code));
    std::string message = std::string("the runtime host exited with code ") + text +
                          (connected ? "" : " before completing the handshake");
    if (!last_handshake_error_.empty()) message += " (" + last_handshake_error_ + ")";
    finish(State::Failed, std::move(message));
  }
}

void WinWorker::send(std::string message)
{
  if (conn_ == INVALID_SOCKET) {
    return;
  }
  outbuf_ += message;
  outbuf_.push_back('\n');
  if (outbuf_.size() > kMaxUnsentBytes) {
    finish(State::Failed, "the runtime host stopped reading from the link");
  }
}

void WinWorker::flush()
{
  while (conn_ != INVALID_SOCKET && !write_blocked_ && !outbuf_.empty()) {
    const int n = ::send(conn_, outbuf_.data(), int(outbuf_.size() > (1 << 20) ? (1 << 20) : outbuf_.size()), 0);
    if (n == SOCKET_ERROR) {
      if (WSAGetLastError() == WSAEWOULDBLOCK) {
        write_blocked_ = true;  // FD_WRITE will tell us when to continue.
      }
      return;  // Other errors surface as FD_CLOSE.
    }
    outbuf_.erase(0, size_t(n));
  }
}

void WinWorker::drop_connection(const std::string &code, const std::string &why)
{
  if (!code.empty()) {
    send(protocol::error(code, why));
    flush();
  }
  shutdown(conn_, SD_BOTH);
  closesocket(conn_);
  conn_ = INVALID_SOCKET;
  WSAResetEvent(conn_event_);
  last_handshake_error_ = why;
  phase_ = Phase::Accepting;  // Keep waiting for the real host until the connect deadline.
}

void WinWorker::finish(State state, std::string message)
{
  phase_ = Phase::Done;
  impl_.update_status([&](Status &s) {
    s.state = state;
    s.message = std::move(message);
  });
}

void WinWorker::note_error(std::string message)
{
  {
    std::lock_guard lock(impl_.status_mutex);
    if (impl_.status.message == message) {
      return;  // Same failure again (e.g. every frame): one StateChanged is enough.
    }
  }
  impl_.update_status([&](Status &s) { s.message = std::move(message); });
}

}  // namespace

std::unique_ptr<Worker> create_worker(Surface::Impl &impl, std::string *error) noexcept
{
  try {
    auto worker = std::make_unique<WinWorker>(impl);
    if (!worker->init(error)) {
      return nullptr;
    }
    return worker;
  }
  catch (...) {
    if (error) *error = "out of memory creating the surface worker";
    return nullptr;
  }
}

}  // namespace gs::gameview
