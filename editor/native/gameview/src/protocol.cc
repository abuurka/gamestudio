// SPDX-License-Identifier: GPL-3.0-or-later

#include "protocol.hh"

#include <charconv>
#include <cmath>

namespace gs::gameview::protocol {

namespace {

constexpr int kMaxTextureSize = 16384;

std::string modifiers_json(uint32_t bits)
{
  std::string out = "[";
  const char *names[] = {"\"Shift\"", "\"Control\"", "\"Alt\"", "\"Meta\""};
  for (int i = 0; i < 4; i++) {
    if (bits & (1u << i)) {
      if (out.size() > 1) {
        out.push_back(',');
      }
      out += names[i];
    }
  }
  out.push_back(']');
  return out;
}

bool parse_u64(std::string_view text, uint64_t &out)
{
  if (text.empty() || text.size() > 20) {
    return false;
  }
  const auto result = std::from_chars(text.data(), text.data() + text.size(), out, 10);
  return result.ec == std::errc() && result.ptr == text.data() + text.size();
}

bool exact_integer(double value, double min, double max)
{
  return std::isfinite(value) && value >= min && value <= max && std::floor(value) == value;
}

}  // namespace

std::string welcome()
{
  return json::Writer("welcome").integer("protocol", kProtocolVersion).finish();
}

std::string error(std::string_view code, std::string_view message)
{
  return json::Writer("error").str("code", code).str("msg", message).finish();
}

std::string configure(int width, int height, float dpr)
{
  return json::Writer("surface.configure").integer("width", width).integer("height", height).num("dpr", dpr).finish();
}

std::string release(uint64_t frame_id)
{
  return json::Writer("surface.release").integer("frameId", int64_t(frame_id)).finish();
}

std::string key(const KeyEvent &e)
{
  return json::Writer("input.key")
      .str("type", e.down ? "down" : "up")
      .str("code", e.code)
      .str("key", e.key)
      .boolean("repeat", e.repeat)
      .raw("modifiers", modifiers_json(e.modifiers))
      .finish();
}

std::string pointer(const PointerEvent &e)
{
  static const char *types[] = {"move", "down", "up", "wheel"};
  json::Writer w("input.pointer");
  w.str("type", types[int(e.type) & 3]).num("x", e.x).num("y", e.y);
  w.integer("button", e.button).integer("buttons", e.buttons);
  if (e.dx != 0 || e.dy != 0) {
    w.num("dx", e.dx).num("dy", e.dy);
  }
  if (e.type == PointerType::Wheel) {
    w.num("deltaX", e.delta_x).num("deltaY", e.delta_y);
  }
  return w.raw("modifiers", modifiers_json(e.modifiers)).finish();
}

std::string focus(bool focused)
{
  return json::Writer("focus").boolean("focused", focused).finish();
}

std::string pause(bool paused)
{
  return json::Writer("pause").boolean("paused", paused).finish();
}

std::string detach()
{
  return json::Writer("detach").finish();
}

std::string devtools(bool open)
{
  return json::Writer("devtools").boolean("open", open).finish();
}

std::string ping(uint64_t id, double ts_ms)
{
  return json::Writer("ping").integer("id", int64_t(id)).num("ts", ts_ms).finish();
}

bool parse_hello(const json::Value &msg, Hello &out, std::string &why)
{
  if (msg.str("t") != "hello") {
    why = "the first message must be hello";
    return false;
  }
  out.protocol = int(msg.num("protocol", 0));
  if (out.protocol != kProtocolVersion) {
    why = "unsupported protocol " + std::to_string(out.protocol) + " (this editor speaks " +
          std::to_string(kProtocolVersion) + ")";
    return false;
  }
  if (msg.str("role") != "host") {
    why = "hello.role must be \"host\"";
    return false;
  }
  out.token = std::string(msg.str("token"));
  const double pid = msg.num("pid", 0);
  out.pid = exact_integer(pid, 1, 4294967295.0) ? uint32_t(pid) : 0;
  out.client = std::string(msg.str("client"));
  out.mode = std::string(msg.str("mode", "docked"));
  const std::string_view luid = msg.str("adapterLuid");
  if (!luid.empty()) {
    const auto result = std::from_chars(luid.data(), luid.data() + luid.size(), out.adapter_luid, 16);
    if (result.ec != std::errc() || luid.size() != 16) {
      why = "hello.adapterLuid must be 16 hex digits";
      return false;
    }
  }
  if (const json::Value *gpu = msg.get("gpu")) {
    out.gpu_vendor_id = uint32_t(gpu->num("vendorId", 0));
    out.gpu_device_id = uint32_t(gpu->num("deviceId", 0));
  }
  if (const json::Value *handles = msg.get("handles"); handles && handles->type == json::Value::Type::Array) {
    for (const json::Value &h : handles->array) {
      out.supports_d3d11_nt |= h.type == json::Value::Type::String && h.string == "d3d11-nt";
    }
  }
  return true;
}

bool parse_frame(const json::Value &msg, Frame &out, std::string &why)
{
  const double id = msg.num("frameId", -1);
  const double width = msg.num("width", 0);
  const double height = msg.num("height", 0);
  if (!exact_integer(id, 1, 9007199254740991.0)) {
    why = "surface.frame: bad frameId";
    return false;
  }
  if (!exact_integer(width, 1, kMaxTextureSize) || !exact_integer(height, 1, kMaxTextureSize)) {
    why = "surface.frame: bad width/height";
    return false;
  }
  if (!parse_u64(msg.str("handle"), out.handle) || out.handle == 0) {
    why = "surface.frame: handle must be a non-zero decimal string";
    return false;
  }
  const std::string_view format = msg.str("format", "bgra8");
  if (format == "bgra8") {
    out.format = PixelFormat::BGRA8;
  }
  else if (format == "rgba8") {
    out.format = PixelFormat::RGBA8;
  }
  else {
    why = "surface.frame: unsupported format " + std::string(format);
    return false;
  }
  out.frame_id = uint64_t(id);
  out.width = int(width);
  out.height = int(height);
  out.ts_ms = msg.num("ts", 0);
  return true;
}

std::vector<uint64_t> parse_revoke(const json::Value &msg)
{
  std::vector<uint64_t> ids;
  if (const json::Value *list = msg.get("frameIds"); list && list->type == json::Value::Type::Array) {
    for (const json::Value &v : list->array) {
      if (v.type == json::Value::Type::Number && exact_integer(v.number, 1, 9007199254740991.0)) {
        ids.push_back(uint64_t(v.number));
      }
    }
  }
  return ids;
}

bool token_equals(std::string_view a, std::string_view b) noexcept
{
  unsigned diff = unsigned(a.size() ^ b.size());
  for (size_t i = 0; i < a.size(); i++) {
    diff |= unsigned(uint8_t(a[i]) ^ uint8_t(i < b.size() ? b[i] : 0));
  }
  return diff == 0;
}

}  // namespace gs::gameview::protocol
