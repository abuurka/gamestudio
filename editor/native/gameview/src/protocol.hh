// SPDX-License-Identifier: GPL-3.0-or-later
// Surface protocol messages (docs/fork/SURFACE.md §4): builders for editor → host messages and
// parsers for host → editor messages. Portable; no OS calls.

#pragma once

#include "gs_gameview/gameview.hh"
#include "json.hh"

#include <string>
#include <string_view>
#include <vector>

namespace gs::gameview::protocol {

// --- editor → host -------------------------------------------------------------------
std::string welcome();
std::string error(std::string_view code, std::string_view message);
std::string configure(int width, int height, float dpr);
std::string release(uint64_t frame_id);
std::string key(const KeyEvent &event);
std::string pointer(const PointerEvent &event);
std::string focus(bool focused);
std::string pause(bool paused);
std::string detach();
std::string devtools(bool open);
std::string ping(uint64_t id, double ts_ms);

// --- host → editor -------------------------------------------------------------------
struct Hello {
  std::string token;
  int protocol = 0;
  uint32_t pid = 0;
  std::string client;
  std::string mode;
  uint64_t adapter_luid = 0;  ///< 0 if the host did not name one.
  uint32_t gpu_vendor_id = 0;
  uint32_t gpu_device_id = 0;
  bool supports_d3d11_nt = false;
};
/** Validates a hello; on failure `why` says what is wrong. Does not check the token. */
bool parse_hello(const json::Value &msg, Hello &out, std::string &why);

struct Frame {
  uint64_t frame_id = 0;
  uint64_t handle = 0;  ///< Handle value in the host process.
  int width = 0;
  int height = 0;
  PixelFormat format = PixelFormat::BGRA8;
  double ts_ms = 0;
  double received_ms = 0;  ///< Filled by the caller.
};
bool parse_frame(const json::Value &msg, Frame &out, std::string &why);

/** frameIds of a surface.revoke. */
std::vector<uint64_t> parse_revoke(const json::Value &msg);

/** Constant-time comparison for the hello token. */
bool token_equals(std::string_view a, std::string_view b) noexcept;

}  // namespace gs::gameview::protocol
