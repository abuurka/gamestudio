// SPDX-License-Identifier: GPL-3.0-or-later
// Maps the host's `ts` clock onto now_ms() from ping/pong round trips (docs/fork/SURFACE.md §4.4).
// Uses the sample with the smallest round trip among the recent ones (NTP-style).

#pragma once

#include <cmath>
#include <limits>

namespace gs::gameview {

class ClockSync {
 public:
  /** One round trip: ping sent at `local_send`, host stamped `host_ts`, pong seen at `local_recv`. */
  void add(double local_send, double host_ts, double local_recv) noexcept
  {
    const double rtt = local_recv - local_send;
    if (!(rtt >= 0)) {
      return;
    }
    samples_[count_++ % kWindow] = {rtt, host_ts - (local_send + local_recv) / 2};
    best_rtt_ = std::numeric_limits<double>::infinity();
    const int n = count_ < kWindow ? count_ : kWindow;
    for (int i = 0; i < n; i++) {
      if (samples_[i].rtt < best_rtt_) {
        best_rtt_ = samples_[i].rtt;
        offset_ = samples_[i].offset;
      }
    }
  }

  bool synced() const noexcept { return count_ > 0; }
  /** Host time → now_ms(); NaN before the first sample. */
  double to_local(double host_ts) const noexcept
  {
    return synced() ? host_ts - offset_ : std::numeric_limits<double>::quiet_NaN();
  }
  double best_rtt() const noexcept { return best_rtt_; }

 private:
  static constexpr int kWindow = 16;
  struct Sample {
    double rtt = 0;
    double offset = 0;
  };
  Sample samples_[kWindow];
  int count_ = 0;
  double offset_ = 0;  // host - local
  double best_rtt_ = std::numeric_limits<double>::infinity();
};

}  // namespace gs::gameview
