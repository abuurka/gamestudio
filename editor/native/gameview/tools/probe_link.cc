// SPDX-License-Identifier: GPL-3.0-or-later

#include "probe_link.hh"

#include <winsock2.h>
#include <ws2tcpip.h>

#include <algorithm>
#include <chrono>
#include <cmath>

namespace gs::probe {

namespace {
double clock_ms()
{
  using namespace std::chrono;
  return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}
}  // namespace

LinkClient::~LinkClient()
{
  if (socket_ != INVALID_SOCKET) closesocket(SOCKET(socket_));
}

bool LinkClient::connect(uint16_t port, const std::string &token, std::string &error)
{
  SOCKET s = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  addr.sin_port = htons(port);
  if (s == INVALID_SOCKET || ::connect(s, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0) {
    error = "cannot connect to the live-link hub on port " + std::to_string(port);
    if (s != INVALID_SOCKET) closesocket(s);
    return false;
  }
  socket_ = uintptr_t(s);
  json::Writer hello("hello");
  hello.str("role", "editor").str("token", token).integer("protocol", 1).str("client", "gs_gameview_probe");
  if (!send(hello.finish())) {
    error = "cannot send hello to the hub";
    return false;
  }
  json::Value msg;
  while (read(msg, 5000)) {
    if (msg.str("t") == "welcome") {
      welcome_ = std::move(msg);
      return true;
    }
    if (msg.str("t") == "error") {
      error = "hub refused the probe: " + std::string(msg.str("msg"));
      return false;
    }
  }
  error = "no welcome from the hub";
  return false;
}

bool LinkClient::find_game(std::string_view marker, int timeout_ms, std::string &peer, std::string &error)
{
  // Games already connected were listed in the welcome; later ones arrive as `peers` broadcasts.
  if (const json::Value *list = welcome_.get("peers"); list && scan_peers(*list, marker, peer)) return true;
  const double deadline = clock_ms() + timeout_ms;
  json::Value msg;
  while (clock_ms() < deadline) {
    if (!read(msg, int(deadline - clock_ms()) + 1)) break;
    if (const json::Value *list = msg.get("peers"); list && scan_peers(*list, marker, peer)) return true;
  }
  error = "no game with \"" + std::string(marker) + "\" in its client string joined the hub";
  return false;
}

bool LinkClient::scan_peers(const json::Value &list, std::string_view marker, std::string &peer)
{
  if (list.type != json::Value::Type::Array) return false;
  for (const json::Value &p : list.array) {
    if (p.str("role") == "game" && p.str("client").find(marker) != std::string_view::npos) {
      peer = std::string(p.str("peer"));
      game_peer_ = peer;
      return true;
    }
  }
  return false;
}

bool LinkClient::position(const std::string &peer, std::string_view component, std::string_view name,
                          std::array<double, 3> &out, std::string &error)
{
  const std::string id = "probe-" + std::to_string(next_id_++);
  const std::string filter = "{\"component\":\"" + std::string(component) + "\"}";
  if (!send(json::Writer("state.request").str("id", id).str("to", peer).raw("filter", filter).finish())) {
    error = "lost the hub connection";
    return false;
  }
  json::Value msg;
  const double deadline = clock_ms() + 3000;
  while (clock_ms() < deadline && read(msg, int(deadline - clock_ms()) + 1)) {
    if (msg.str("t") != "state.response" || msg.str("re") != id) continue;
    const json::Value *entities = msg.get("entities");
    for (const json::Value &e : entities ? entities->array : std::vector<json::Value>{}) {
      const json::Value *p = e.get("position");
      if (e.str("name") == name && p && p->array.size() == 3) {
        for (int i = 0; i < 3; i++) out[size_t(i)] = p->array[size_t(i)].number;
        return true;
      }
    }
    error = "the game has no entity \"" + std::string(name) + "\" with " + std::string(component) + " (yet)";
    return false;
  }
  error = "no state.response within 3 s";
  return false;
}

void LinkClient::listen(int ms)
{
  json::Value ignored;
  const double deadline = clock_ms() + ms;
  while (clock_ms() < deadline && read(ignored, int(deadline - clock_ms()) + 1)) {
  }
}

double LinkClient::game_fps() const
{
  if (game_fps_.empty()) return 0;
  std::vector<double> sorted = game_fps_;
  std::sort(sorted.begin(), sorted.end());
  return sorted[sorted.size() / 2];
}

bool LinkClient::send(const std::string &line)
{
  const std::string data = line + "\n";
  return ::send(SOCKET(socket_), data.data(), int(data.size()), 0) == int(data.size());
}

bool LinkClient::read(json::Value &out, int timeout_ms)
{
  const double deadline = clock_ms() + timeout_ms;
  while (true) {
    const size_t nl = buffer_.find('\n');
    if (nl != std::string::npos) {
      const std::string line = buffer_.substr(0, nl);
      buffer_.erase(0, nl + 1);
      if (!json::parse(line, out)) continue;
      if (out.str("t") == "stats" && !game_peer_.empty() && out.str("from") == game_peer_) {
        game_fps_.push_back(out.num("fps", 0));
      }
      return true;
    }
    const double left = deadline - clock_ms();
    if (left <= 0) return false;
    fd_set readable;
    FD_ZERO(&readable);
    FD_SET(SOCKET(socket_), &readable);
    const timeval tv = {long(left / 1000), long(std::fmod(left, 1000.0) * 1000)};
    if (select(0, &readable, nullptr, nullptr, &tv) <= 0) return false;
    char chunk[16384];
    const int n = recv(SOCKET(socket_), chunk, int(sizeof(chunk)), 0);
    if (n <= 0) return false;
    buffer_.append(chunk, size_t(n));
  }
}

}  // namespace gs::probe
