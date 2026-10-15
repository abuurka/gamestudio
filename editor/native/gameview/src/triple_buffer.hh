// SPDX-License-Identifier: GPL-3.0-or-later
// Lock-free single-producer / single-consumer triple buffer of CPU frames.
//
// The writer (worker thread) fills back(), then publish() swaps it with the middle slot.
// The reader (main thread) acquire()s the middle slot only when it holds a frame it has not
// seen yet, swapping in its previous slot. Each side only ever touches the slot it owns, so
// neither ever waits, and the writer may reallocate its own slot when the size changes.
//
// The worker's copy into these slots is deliberate: the main thread then uploads from memory
// that is warm in the CPU cache. Uploading straight from the mapped staging texture (written by
// the GPU, so cold) was measured 2-3x slower on the main thread, with long spikes.

#pragma once

#include "gs_gameview/gameview.hh"

#include <atomic>
#include <vector>

namespace gs::gameview {

struct FrameSlot {
  std::vector<uint8_t> pixels;
  FrameView view;  ///< Metadata; `view.pixels` is filled in by acquire().
};

class TripleBuffer {
 public:
  /** Writer: the slot to fill next. */
  FrameSlot &back() noexcept { return slots_[back_]; }

  /** Writer: makes back() the newest frame. */
  void publish() noexcept
  {
    const int previous = middle_.exchange(back_ | kFresh, std::memory_order_acq_rel);
    back_ = previous & kIndexMask;
  }

  /** Reader: the newest frame if it is newer than the last one acquired, else nullptr. */
  FrameSlot *acquire() noexcept
  {
    if (!has_fresh()) {
      return nullptr;
    }
    const int previous = middle_.exchange(front_, std::memory_order_acq_rel);
    front_ = previous & kIndexMask;
    FrameSlot &slot = slots_[front_];
    slot.view.pixels = slot.pixels.data();
    return &slot;
  }

  bool has_fresh() const noexcept { return (middle_.load(std::memory_order_acquire) & kFresh) != 0; }

 private:
  static constexpr int kIndexMask = 3;
  static constexpr int kFresh = 4;

  FrameSlot slots_[3];
  int back_ = 0;   // owned by the writer
  int front_ = 1;  // owned by the reader
  std::atomic<int> middle_{2};
};

}  // namespace gs::gameview
