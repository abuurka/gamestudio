// SPDX-License-Identifier: GPL-3.0-or-later
// The portable half of Surface: queues, status, events and the consumer side of the
// triple buffer. Socket, process and GPU work live in the platform worker.

#include "protocol.hh"
#include "surface_impl.hh"

#include <chrono>

namespace gs::gameview {

double now_ms() noexcept
{
  using namespace std::chrono;
  return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

// --- Impl helpers -------------------------------------------------------------------------

void Surface::Impl::post(std::string message) noexcept
{
  try {
    std::lock_guard lock(outbox_mutex);
    outbox += message;
    outbox.push_back('\n');
  }
  catch (...) {
    return;  // Out of memory: drop the message.
  }
  if (worker) {
    worker->wake();
  }
}

void Surface::Impl::push_event(const Event &event) noexcept
{
  try {
    std::lock_guard lock(status_mutex);
    events.push_back(event);
  }
  catch (...) {
  }
}

void Surface::Impl::wake_waiters() noexcept
{
  {
    std::lock_guard lock(wait_mutex);
  }
  wait_cv.notify_all();
}

void Surface::Impl::frame_published() noexcept
{
  wake_waiters();
  if (frame_callback) {
    try {
      frame_callback();
    }
    catch (...) {
    }
  }
}

// --- Surface ------------------------------------------------------------------------------

Surface::Surface(std::unique_ptr<Impl> impl) noexcept : impl_(std::move(impl)) {}

Surface::~Surface()
{
  if (impl_ && impl_->worker) {
    impl_->worker->stop();
  }
}

std::unique_ptr<Surface> Surface::create(const SurfaceOptions &options, std::string *error) noexcept
{
  try {
    auto impl = std::make_unique<Impl>();
    impl->options = options;
    uint8_t random[16];
    if (!secure_random(random, sizeof(random))) {
      if (error) *error = "could not generate a link token (no secure random source)";
      return nullptr;
    }
    static const char hex[] = "0123456789abcdef";
    for (const uint8_t byte : random) {
      impl->token.push_back(hex[byte >> 4]);
      impl->token.push_back(hex[byte & 15]);
    }
    impl->worker = create_worker(*impl, error);
    if (!impl->worker) {
      return nullptr;
    }
    return std::unique_ptr<Surface>(new Surface(std::move(impl)));
  }
  catch (...) {
    if (error) *error = "out of memory creating the surface";
    return nullptr;
  }
}

uint16_t Surface::port() const noexcept
{
  return impl_->worker->port();
}

const std::string &Surface::token() const noexcept
{
  return impl_->token;
}

bool Surface::start(const HostProcess &host, std::string *error) noexcept
{
  if (!host.valid()) {
    if (error) *error = "Surface::start: the host process is not running";
    return false;
  }
  return impl_->worker->start(host, error);
}

bool Surface::acquire_latest(FrameView &out) noexcept
{
  FrameSlot *slot = impl_->frames.acquire();
  if (!slot) {
    return false;
  }
  out = slot->view;
  impl_->view_held = true;
  impl_->counters.acquired++;
  return true;
}

void Surface::release_view() noexcept
{
  // The slot stays reader-owned until the next acquire (see TripleBuffer), so releasing is
  // bookkeeping today; a zero-copy backend would unmap or unlock here.
  impl_->view_held = false;
}

bool Surface::wait_for_frame(int timeout_ms) noexcept
{
  try {
    std::unique_lock lock(impl_->wait_mutex);
    const auto ready = [&] {
      if (impl_->frames.has_fresh()) {
        return true;
      }
      std::lock_guard status_lock(impl_->status_mutex);
      return impl_->status.state == State::Closed || impl_->status.state == State::Failed;
    };
    impl_->wait_cv.wait_for(lock, std::chrono::milliseconds(timeout_ms < 0 ? 0 : timeout_ms), ready);
  }
  catch (...) {
  }
  return impl_->frames.has_fresh();
}

void Surface::set_frame_callback(std::function<void()> callback) noexcept
{
  impl_->frame_callback = std::move(callback);
}

void Surface::configure(int width, int height, float dpr) noexcept
{
  try {
    impl_->post(protocol::configure(width, height, dpr));
  }
  catch (...) {
  }
}

void Surface::send_key(const KeyEvent &event) noexcept
{
  try {
    impl_->post(protocol::key(event));
  }
  catch (...) {
  }
}

void Surface::send_pointer(const PointerEvent &event) noexcept
{
  try {
    impl_->post(protocol::pointer(event));
  }
  catch (...) {
  }
}

void Surface::set_focus(bool focused) noexcept
{
  try {
    impl_->post(protocol::focus(focused));
  }
  catch (...) {
  }
}

void Surface::set_paused(bool paused) noexcept
{
  try {
    impl_->post(protocol::pause(paused));
  }
  catch (...) {
  }
}

void Surface::detach() noexcept
{
  try {
    impl_->post(protocol::detach());
  }
  catch (...) {
  }
}

void Surface::devtools(bool open) noexcept
{
  try {
    impl_->post(protocol::devtools(open));
  }
  catch (...) {
  }
}

Status Surface::status() const noexcept
{
  try {
    std::lock_guard lock(impl_->status_mutex);
    return impl_->status;
  }
  catch (...) {
    Status fallback;
    fallback.state = State::Failed;
    return fallback;
  }
}

bool Surface::poll_event(Event &out) noexcept
{
  std::lock_guard lock(impl_->status_mutex);
  if (impl_->events.empty()) {
    return false;
  }
  out = impl_->events.front();
  impl_->events.pop_front();
  return true;
}

Stats Surface::stats() const noexcept
{
  const auto &c = impl_->counters;
  Stats s;
  s.frames_received = c.received.load();
  s.frames_delivered = c.delivered.load();
  s.frames_acquired = c.acquired.load();
  s.frames_skipped = c.skipped.load();
  s.frames_superseded = c.superseded.load();
  s.frames_revoked = c.revoked.load();
  s.frames_failed = c.failed.load();
  s.releases_sent = c.releases.load();
  return s;
}

}  // namespace gs::gameview
