// SPDX-License-Identifier: GPL-3.0-or-later

#include "probe_devserver.hh"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <filesystem>
#include <fstream>
#include <sstream>

#include "json.hh"

namespace gs::probe {

namespace fs = std::filesystem;
namespace json = gs::gameview::json;

namespace {

fs::path session_path(const std::string &project)
{
  return utf8_path(project) / ".gamestudio" / "session.json";
}

bool read_file(const fs::path &path, std::string &out)
{
  std::ifstream in(path, std::ios::binary);
  if (!in) return false;
  std::ostringstream text;
  text << in.rdbuf();
  out = text.str();
  return true;
}

bool process_alive(uint32_t pid)
{
  HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (!h) return false;
  DWORD code = 0;
  const bool alive = GetExitCodeProcess(h, &code) && code == STILL_ACTIVE;
  CloseHandle(h);
  return alive;
}

uint16_t free_loopback_port()
{
  SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  uint16_t port = 0;
  if (bind(s, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0) {
    int len = sizeof(addr);
    getsockname(s, reinterpret_cast<sockaddr *>(&addr), &len);
    port = ntohs(addr.sin_port);
  }
  closesocket(s);
  return port;
}

bool connect_with_timeout(const sockaddr *addr, int addr_len, int family, int timeout_ms)
{
  SOCKET s = socket(family, SOCK_STREAM, IPPROTO_TCP);
  if (s == INVALID_SOCKET) return false;
  u_long nonblocking = 1;
  ioctlsocket(s, FIONBIO, &nonblocking);
  connect(s, addr, addr_len);
  fd_set writable, failed;
  FD_ZERO(&writable);
  FD_ZERO(&failed);
  FD_SET(s, &writable);
  FD_SET(s, &failed);
  timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
  const bool ok = select(0, nullptr, &writable, &failed, &tv) > 0 && FD_ISSET(s, &writable);
  closesocket(s);
  return ok;
}

}  // namespace

bool read_session(const std::string &project, DevSession &out, std::string &error)
{
  std::string text;
  if (!read_file(session_path(project), text)) {
    error = "no " + path_utf8(session_path(project));
    return false;
  }
  json::Value v;
  if (!json::parse(text, v) || v.type != json::Value::Type::Object) {
    error = "session.json is not valid JSON";
    return false;
  }
  out.pid = uint32_t(v.num("pid", 0));
  out.url = std::string(v.str("url"));
  out.editor_port = uint16_t(v.num("editorPort", 0));
  out.token = std::string(v.str("token"));
  if (out.url.empty() || out.editor_port == 0 || out.token.empty()) {
    error = "session.json lacks url, editorPort or token";
    return false;
  }
  return true;
}

bool url_reachable(const std::string &url, int timeout_ms)
{
  // http://host:port/...
  const size_t scheme = url.find("://");
  if (scheme == std::string::npos) return false;
  const size_t host_start = scheme + 3;
  const size_t path = url.find('/', host_start);
  const std::string authority = url.substr(host_start, path == std::string::npos ? std::string::npos : path - host_start);
  const size_t colon = authority.rfind(':');
  std::string host = colon == std::string::npos ? authority : authority.substr(0, colon);
  const std::string port = colon == std::string::npos ? "80" : authority.substr(colon + 1);
  if (host.size() > 2 && host.front() == '[') host = host.substr(1, host.size() - 2);

  addrinfo hints = {};
  hints.ai_socktype = SOCK_STREAM;
  addrinfo *list = nullptr;
  if (getaddrinfo(host.c_str(), port.c_str(), &hints, &list) != 0) return false;
  bool ok = false;
  for (addrinfo *a = list; a && !ok; a = a->ai_next) {
    ok = connect_with_timeout(a->ai_addr, int(a->ai_addrlen), a->ai_family, timeout_ms);
  }
  freeaddrinfo(list);
  return ok;
}

bool find_live_session(const std::string &project, DevSession &out)
{
  std::string ignored;
  return read_session(project, out, ignored) && process_alive(out.pid) && url_reachable(out.url, 1000);
}

bool start_vite(const std::string &project, const std::string &log_file, gameview::HostProcess &process,
                DevSession &session, std::string &error)
{
  const uint16_t port = free_loopback_port();
  if (!port) {
    error = "no free port for Vite";
    return false;
  }
  DevSession previous;
  std::string ignored;
  read_session(project, previous, ignored);

  wchar_t comspec[MAX_PATH] = L"";
  GetEnvironmentVariableW(L"ComSpec", comspec, MAX_PATH);
  gameview::ProcessSpec spec;
  spec.executable = path_utf8(fs::path(comspec[0] ? comspec : L"C:\\Windows\\System32\\cmd.exe"));
  spec.args = {"/d", "/s", "/c", "npx vite --port " + std::to_string(port) + " --strictPort"};
  spec.working_dir = project;
  spec.log_file = log_file;
  if (!gameview::launch_process(spec, process, &error)) {
    return false;
  }
  // Vite writes session.json once its hub listens; wait for one that names our port.
  const std::string expected = ":" + std::to_string(port) + "/";
  for (int waited = 0; waited < 60000; waited += 250) {
    if (process.wait(250)) {
      error = "Vite exited early; see " + log_file;
      return false;
    }
    if (read_session(project, session, ignored) && session.token != previous.token &&
        session.url.find(expected) != std::string::npos && url_reachable(session.url, 500))
    {
      return true;
    }
  }
  error = "Vite did not come up within 60 s; see " + log_file;
  return false;
}

void remove_session_if_ours(const std::string &project, const std::string &token)
{
  DevSession current;
  std::string ignored;
  if (read_session(project, current, ignored) && current.token == token) {
    std::error_code ec;
    fs::remove(session_path(project), ec);
  }
}

}  // namespace gs::probe
