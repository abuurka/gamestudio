// SPDX-License-Identifier: GPL-3.0-or-later

#include "json.hh"

#include <charconv>
#include <cmath>
#include <cstdio>

namespace gs::gameview::json {

namespace {

constexpr int kMaxDepth = 32;

class Parser {
 public:
  explicit Parser(std::string_view text) : s_(text) {}

  bool document(Value &out)
  {
    if (!value(out, 0)) {
      return false;
    }
    skip_ws();
    return pos_ == s_.size();
  }

 private:
  bool value(Value &out, int depth)
  {
    if (depth > kMaxDepth) {
      return false;
    }
    skip_ws();
    if (pos_ >= s_.size()) {
      return false;
    }
    switch (s_[pos_]) {
      case '{':
        return object(out, depth);
      case '[':
        return array(out, depth);
      case '"':
        out.type = Value::Type::String;
        return string(out.string);
      case 't':
        out.type = Value::Type::Bool;
        out.boolean = true;
        return literal("true");
      case 'f':
        out.type = Value::Type::Bool;
        out.boolean = false;
        return literal("false");
      case 'n':
        out.type = Value::Type::Null;
        return literal("null");
      default:
        out.type = Value::Type::Number;
        return number(out.number);
    }
  }

  bool object(Value &out, int depth)
  {
    out.type = Value::Type::Object;
    ++pos_;  // {
    skip_ws();
    if (peek('}')) {
      ++pos_;
      return true;
    }
    while (true) {
      skip_ws();
      std::string key;
      if (!peek('"') || !string(key)) {
        return false;
      }
      skip_ws();
      if (!peek(':')) {
        return false;
      }
      ++pos_;
      Value member;
      if (!value(member, depth + 1)) {
        return false;
      }
      out.object.emplace_back(std::move(key), std::move(member));
      skip_ws();
      if (peek(',')) {
        ++pos_;
        continue;
      }
      if (peek('}')) {
        ++pos_;
        return true;
      }
      return false;
    }
  }

  bool array(Value &out, int depth)
  {
    out.type = Value::Type::Array;
    ++pos_;  // [
    skip_ws();
    if (peek(']')) {
      ++pos_;
      return true;
    }
    while (true) {
      Value item;
      if (!value(item, depth + 1)) {
        return false;
      }
      out.array.push_back(std::move(item));
      skip_ws();
      if (peek(',')) {
        ++pos_;
        continue;
      }
      if (peek(']')) {
        ++pos_;
        return true;
      }
      return false;
    }
  }

  bool string(std::string &out)
  {
    ++pos_;  // opening quote
    while (pos_ < s_.size()) {
      const char c = s_[pos_++];
      if (c == '"') {
        return true;
      }
      if (static_cast<unsigned char>(c) < 0x20) {
        return false;
      }
      if (c != '\\') {
        out.push_back(c);
        continue;
      }
      if (pos_ >= s_.size()) {
        return false;
      }
      const char e = s_[pos_++];
      switch (e) {
        case '"': out.push_back('"'); break;
        case '\\': out.push_back('\\'); break;
        case '/': out.push_back('/'); break;
        case 'b': out.push_back('\b'); break;
        case 'f': out.push_back('\f'); break;
        case 'n': out.push_back('\n'); break;
        case 'r': out.push_back('\r'); break;
        case 't': out.push_back('\t'); break;
        case 'u': {
          uint32_t cp = 0;
          if (!hex4(cp)) {
            return false;
          }
          if (cp >= 0xD800 && cp <= 0xDBFF) {  // High surrogate: expect a low one.
            uint32_t low = 0;
            if (pos_ + 1 >= s_.size() || s_[pos_] != '\\' || s_[pos_ + 1] != 'u') {
              return false;
            }
            pos_ += 2;
            if (!hex4(low) || low < 0xDC00 || low > 0xDFFF) {
              return false;
            }
            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
          }
          append_utf8(out, cp);
          break;
        }
        default:
          return false;
      }
    }
    return false;
  }

  bool number(double &out)
  {
    const char *begin = s_.data() + pos_;
    const char *end = s_.data() + s_.size();
    const auto result = std::from_chars(begin, end, out);
    if (result.ec != std::errc() || result.ptr == begin || !std::isfinite(out)) {
      return false;
    }
    pos_ += size_t(result.ptr - begin);
    return true;
  }

  bool literal(std::string_view word)
  {
    if (s_.substr(pos_, word.size()) != word) {
      return false;
    }
    pos_ += word.size();
    return true;
  }

  bool hex4(uint32_t &out)
  {
    if (pos_ + 4 > s_.size()) {
      return false;
    }
    out = 0;
    for (int i = 0; i < 4; i++) {
      const char c = s_[pos_++];
      out <<= 4;
      if (c >= '0' && c <= '9') out |= uint32_t(c - '0');
      else if (c >= 'a' && c <= 'f') out |= uint32_t(c - 'a' + 10);
      else if (c >= 'A' && c <= 'F') out |= uint32_t(c - 'A' + 10);
      else return false;
    }
    return true;
  }

  static void append_utf8(std::string &out, uint32_t cp)
  {
    if (cp < 0x80) {
      out.push_back(char(cp));
    }
    else if (cp < 0x800) {
      out.push_back(char(0xC0 | (cp >> 6)));
      out.push_back(char(0x80 | (cp & 0x3F)));
    }
    else if (cp < 0x10000) {
      out.push_back(char(0xE0 | (cp >> 12)));
      out.push_back(char(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(char(0x80 | (cp & 0x3F)));
    }
    else {
      out.push_back(char(0xF0 | (cp >> 18)));
      out.push_back(char(0x80 | ((cp >> 12) & 0x3F)));
      out.push_back(char(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(char(0x80 | (cp & 0x3F)));
    }
  }

  void skip_ws()
  {
    while (pos_ < s_.size() && (s_[pos_] == ' ' || s_[pos_] == '\t' || s_[pos_] == '\n' || s_[pos_] == '\r')) {
      ++pos_;
    }
  }

  bool peek(char c) const { return pos_ < s_.size() && s_[pos_] == c; }

  std::string_view s_;
  size_t pos_ = 0;
};

}  // namespace

const Value *Value::get(std::string_view key) const noexcept
{
  if (type != Type::Object) {
    return nullptr;
  }
  for (const auto &[name, value] : object) {
    if (name == key) {
      return &value;
    }
  }
  return nullptr;
}

std::string_view Value::str(std::string_view key, std::string_view fallback) const noexcept
{
  const Value *v = get(key);
  return v && v->type == Type::String ? std::string_view(v->string) : fallback;
}

double Value::num(std::string_view key, double fallback) const noexcept
{
  const Value *v = get(key);
  return v && v->type == Type::Number ? v->number : fallback;
}

bool Value::flag(std::string_view key, bool fallback) const noexcept
{
  const Value *v = get(key);
  return v && v->type == Type::Bool ? v->boolean : fallback;
}

bool parse(std::string_view text, Value &out) noexcept
{
  try {
    out = Value();
    return Parser(text).document(out);
  }
  catch (...) {  // std::bad_alloc on absurd input.
    return false;
  }
}

void append_quoted(std::string &out, std::string_view s)
{
  out.push_back('"');
  for (const char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", unsigned(c));
          out += buf;
        }
        else {
          out.push_back(c);
        }
    }
  }
  out.push_back('"');
}

Writer::Writer(std::string_view type)
{
  out_ = "{\"t\":";
  append_quoted(out_, type);
}

void Writer::key(std::string_view key)
{
  out_.push_back(',');
  append_quoted(out_, key);
  out_.push_back(':');
}

Writer &Writer::str(std::string_view key, std::string_view value)
{
  this->key(key);
  append_quoted(out_, value);
  return *this;
}

Writer &Writer::num(std::string_view key, double value)
{
  this->key(key);
  if (!std::isfinite(value)) {
    out_ += "0";
    return *this;
  }
  char buf[32];
  const auto result = std::to_chars(buf, buf + sizeof(buf), value);
  out_.append(buf, result.ptr);
  return *this;
}

Writer &Writer::integer(std::string_view key, int64_t value)
{
  this->key(key);
  char buf[24];
  const auto result = std::to_chars(buf, buf + sizeof(buf), value);
  out_.append(buf, result.ptr);
  return *this;
}

Writer &Writer::boolean(std::string_view key, bool value)
{
  this->key(key);
  out_ += value ? "true" : "false";
  return *this;
}

Writer &Writer::raw(std::string_view key, std::string_view json)
{
  this->key(key);
  out_ += json;
  return *this;
}

std::string Writer::finish()
{
  out_.push_back('}');
  return std::move(out_);
}

}  // namespace gs::gameview::json
