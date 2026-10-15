// SPDX-License-Identifier: GPL-3.0-or-later
// State shared by the Surface API (surface.cc, portable) and the platform worker thread
// (win/worker_win.cc). The worker interface is all a new OS backend has to implement.

#pragma once

#include "gs_gameview/gameview.hh"
#include "triple_buffer.hh"

#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <string>

namespace gs::gameview {

/** The OS-specific half of a Surface: owns the socket, the GPU importer and the thread. */
class Worker {
 public:
  virtual ~Worker() = default;
  virtual uint16_t port() const noexcept = 0;
  /** Starts the thread; it accepts `host`'s connection and streams frames. */
  virtual bool start(const HostProcess &host, std::string *error) noexcept = 0;
  /** Wakes the thread to send queued messages (any thread). */
  virtual void wake() noexcept = 0;
  /** Closes the link and joins the thread. Idempotent. */
  virtual void stop() noexcept = 0;
};

/** Creates the platform worker and binds its listening socket (fails off Windows in v1). */
std::unique_ptr<Worker> create_worker(Surface::Impl &impl, std::string *error) noexcept;

/** Cryptographically secure random bytes. */
bool secure_random(uint8_t *out, size_t size) noexcept;

struct Surface::Impl {
  SurfaceOptions options;
  std::string token;
  std::unique_ptr<Worker> worker;

  // Editor → host messages queued by any thread, sent by the worker.
  std::mutex outbox_mutex;
  std::string outbox;

  // Status and events, written by the worker.
  mutable std::mutex status_mutex;
  Status status;
  std::deque<Event> events;

  // Frames: the worker publishes, the consumer thread acquires.
  TripleBuffer frames;
  bool view_held = false;  // consumer thread only
  std::mutex wait_mutex;
  std::condition_variable wait_cv;
  std::function<void()> frame_callback;

  struct Counters {
    std::atomic<uint64_t> received{0}, delivered{0}, acquired{0}, skipped{0}, superseded{0}, revoked{0},
        failed{0}, releases{0};
  } counters;

  /** Queues one NDJSON message and wakes the worker. */
  void post(std::string message) noexcept;
  /** Worker: changes the status under the lock and emits StateChanged. */
  template<typename Fn> void update_status(Fn &&fn) noexcept
  {
    {
      std::lock_guard lock(status_mutex);
      try {
        fn(status);
        events.push_back({EventType::StateChanged, false});
      }
      catch (...) {
      }
    }
    wake_waiters();
  }
  void push_event(const Event &event) noexcept;
  /** Worker: a new frame was published. */
  void frame_published() noexcept;
  void wake_waiters() noexcept;
};

}  // namespace gs::gameview
