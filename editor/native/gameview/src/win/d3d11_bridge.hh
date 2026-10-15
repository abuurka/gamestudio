// SPDX-License-Identifier: GPL-3.0-or-later
// The Windows CPU bridge (ARCHITECTURE.md §4, "v1 consumer"): opens a frame's D3D11 NT
// handle on our own device, copies it into a ring of staging textures, waits for the GPU on
// a fence event (never spins), then maps the result for a memcpy into CPU memory.
// Worker thread only.

#pragma once

#include "win_util.hh"

#include <d3d11_4.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <deque>
#include <string>
#include <vector>

#include "../protocol.hh"

namespace gs::gameview {

class D3D11Bridge {
 public:
  /** Which adapter to use, most authoritative first; zero fields are skipped. */
  struct AdapterChoice {
    uint64_t host_luid = 0;  ///< The host says it is pinned to this LUID.
    uint32_t vendor_id = 0;  ///< The host's active GPU, when it names no LUID.
    uint32_t device_id = 0;
    uint64_t fallback_luid = 0;  ///< SurfaceOptions::adapter_luid
  };

  /** A copy that the GPU has finished; its staging slot stays reserved until recycle(). */
  struct Finished {
    protocol::Frame frame;
    int slot = 0;
    bool revoked = false;
    double open_ms = 0;
    double copy_ms = 0;
  };

  D3D11Bridge() = default;
  ~D3D11Bridge();
  D3D11Bridge(const D3D11Bridge &) = delete;
  D3D11Bridge &operator=(const D3D11Bridge &) = delete;

  bool init(const AdapterChoice &choice, int staging_slots, std::string &error);
  bool ready() const { return device_ != nullptr; }
  uint64_t adapter_luid() const { return adapter_luid_; }
  const std::string &adapter_name() const { return adapter_name_; }

  /** A staging texture is free, so begin() can start another copy. */
  bool can_begin() const;
  /** DuplicateHandle + OpenSharedResource1 + CopyResource + fence signal. */
  bool begin(const protocol::Frame &frame, HANDLE host_process, std::string &error);
  /** Signalled when the oldest copy in flight has finished (null when nothing is waited on). */
  HANDLE wait_handle() const { return copies_.empty() ? nullptr : fence_event_; }
  /** Moves finished copies (oldest first) to `out` and drops their source textures. */
  void collect(std::vector<Finished> &out);
  /** Marks a copy in flight as revoked by the host. */
  void mark_revoked(uint64_t frame_id);
  /** Maps a finished copy and writes tightly packed rows to `dst` (width * 4 * height bytes). */
  bool read(const Finished &done, uint8_t *dst, double &map_ms, double &memcpy_ms, std::string &error);
  void recycle(const Finished &done);

 private:
  struct Copy {
    Finished info;
    uint64_t fence_value = 0;
    double submitted_ms = 0;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> source;
  };
  struct Staging {
    Microsoft::WRL::ComPtr<ID3D11Texture2D> texture;
    UINT width = 0, height = 0;
    DXGI_FORMAT format = DXGI_FORMAT_UNKNOWN;
    bool busy = false;
  };

  void arm_fence();

  Microsoft::WRL::ComPtr<ID3D11Device1> device_;
  Microsoft::WRL::ComPtr<ID3D11DeviceContext> context_;
  Microsoft::WRL::ComPtr<ID3D11DeviceContext4> context4_;  // null: no fences (pre-1703)
  Microsoft::WRL::ComPtr<ID3D11Fence> fence_;
  HANDLE fence_event_ = nullptr;
  uint64_t fence_value_ = 0;
  std::vector<Staging> staging_;
  std::deque<Copy> copies_;
  uint64_t adapter_luid_ = 0;
  std::string adapter_name_;
};

}  // namespace gs::gameview
