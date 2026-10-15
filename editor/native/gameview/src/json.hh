// SPDX-License-Identifier: GPL-3.0-or-later
// Minimal JSON for the surface protocol: parse one NDJSON line, build flat messages.
// No exceptions; parse() reports failure by returning false.

#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace gs::gameview::json {

struct Value {
  enum class Type : uint8_t { Null, Bool, Number, String, Array, Object };
  Type type = Type::Null;
  bool boolean = false;
  double number = 0;
  std::string string;
  std::vector<Value> array;
  std::vector<std::pair<std::string, Value>> object;

  /** Member lookup; nullptr if this is not an object or the key is missing. */
  const Value *get(std::string_view key) const noexcept;
  std::string_view str(std::string_view key, std::string_view fallback = {}) const noexcept;
  double num(std::string_view key, double fallback) const noexcept;
  bool flag(std::string_view key, bool fallback) const noexcept;
};

/** Parses one complete JSON text (nesting depth <= 32). */
bool parse(std::string_view text, Value &out) noexcept;

/** Appends `s` as a quoted, escaped JSON string. */
void append_quoted(std::string &out, std::string_view s);

/** Builds one flat JSON object: Writer("ping").num("id", 3).str("x", "y").finish(). */
class Writer {
 public:
  explicit Writer(std::string_view type);
  Writer &str(std::string_view key, std::string_view value);
  Writer &num(std::string_view key, double value);
  Writer &integer(std::string_view key, int64_t value);
  Writer &boolean(std::string_view key, bool value);
  /** `json` must already be valid JSON (e.g. an array built by the caller). */
  Writer &raw(std::string_view key, std::string_view json);
  std::string finish();

 private:
  void key(std::string_view key);
  std::string out_;
};

}  // namespace gs::gameview::json
