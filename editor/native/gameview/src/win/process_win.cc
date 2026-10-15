// SPDX-License-Identifier: GPL-3.0-or-later
// Child processes in a kill-on-close job object (docs/fork/SURFACE.md §3.3), and the runtime-host
// command line (§3.1).

#include "win_util.hh"

#include <cstdio>
#include <memory>

#include "gs_gameview/gameview.hh"

namespace gs::gameview {

namespace {

/** Appends `arg` so that CommandLineToArgvW / the MSVC CRT read it back unchanged. */
void append_arg(std::wstring &cmd, const std::wstring &arg)
{
  if (!cmd.empty()) {
    cmd.push_back(L' ');
  }
  if (!arg.empty() && arg.find_first_of(L" \t\n\v\"") == std::wstring::npos) {
    cmd += arg;
    return;
  }
  cmd.push_back(L'"');
  size_t backslashes = 0;
  for (const wchar_t c : arg) {
    if (c == L'\\') {
      backslashes++;
      continue;
    }
    if (c == L'"') {
      cmd.append(backslashes * 2 + 1, L'\\');
    }
    else {
      cmd.append(backslashes, L'\\');
    }
    backslashes = 0;
    cmd.push_back(c);
  }
  cmd.append(backslashes * 2, L'\\');
  cmd.push_back(L'"');
}

/** The current environment minus ELECTRON_RUN_AS_NODE, as a CREATE_UNICODE_ENVIRONMENT block. */
std::wstring child_environment()
{
  static const std::wstring_view kDropped = L"ELECTRON_RUN_AS_NODE=";
  std::wstring block;
  wchar_t *env = GetEnvironmentStringsW();
  if (!env) {
    return block;
  }
  for (const wchar_t *entry = env; *entry; entry += wcslen(entry) + 1) {
    const std::wstring_view view(entry);
    const bool dropped = view.size() >= kDropped.size() &&
                         CompareStringOrdinal(view.data(), int(kDropped.size()), kDropped.data(),
                                              int(kDropped.size()), TRUE) == CSTR_EQUAL;
    if (!dropped) {
      block.append(view);
      block.push_back(L'\0');
    }
  }
  FreeEnvironmentStringsW(env);
  block.push_back(L'\0');
  return block;
}

struct AttributeList {
  std::unique_ptr<uint8_t[]> storage;
  LPPROC_THREAD_ATTRIBUTE_LIST list = nullptr;
  ~AttributeList()
  {
    if (list) {
      DeleteProcThreadAttributeList(list);
    }
  }
};

}  // namespace

// --- HostProcess --------------------------------------------------------------------------

HostProcess::~HostProcess()
{
  reset();
}

HostProcess::HostProcess(HostProcess &&other) noexcept
    : process_(other.process_), job_(other.job_), pid_(other.pid_)
{
  other.process_ = other.job_ = nullptr;
  other.pid_ = 0;
}

HostProcess &HostProcess::operator=(HostProcess &&other) noexcept
{
  if (this != &other) {
    reset();
    process_ = other.process_;
    job_ = other.job_;
    pid_ = other.pid_;
    other.process_ = other.job_ = nullptr;
    other.pid_ = 0;
  }
  return *this;
}

void HostProcess::reset() noexcept
{
  // Closing the last job handle kills every process still in the job.
  HANDLE job = job_;
  HANDLE process = process_;
  win::close_handle(job);
  win::close_handle(process);
  job_ = process_ = nullptr;
  pid_ = 0;
}

bool HostProcess::running() const noexcept
{
  return process_ && WaitForSingleObject(process_, 0) == WAIT_TIMEOUT;
}

bool HostProcess::wait(int timeout_ms, int *exit_code) const noexcept
{
  if (!process_) {
    return true;
  }
  if (WaitForSingleObject(process_, timeout_ms < 0 ? INFINITE : DWORD(timeout_ms)) != WAIT_OBJECT_0) {
    return false;
  }
  DWORD code = 0;
  GetExitCodeProcess(process_, &code);
  if (exit_code) {
    *exit_code = int(code);
  }
  return true;
}

void HostProcess::terminate() noexcept
{
  if (job_) {
    TerminateJobObject(job_, 1);
  }
}

// --- launch -------------------------------------------------------------------------------

bool launch_process(const ProcessSpec &spec, HostProcess &out, std::string *error) noexcept
{
  const auto fail = [&](std::string message) {
    if (error) *error = std::move(message);
    return false;
  };
  try {
    out = HostProcess();
    const std::wstring exe = win::widen(spec.executable);
    const DWORD attributes = GetFileAttributesW(exe.c_str());
    if (exe.empty() || attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
      return fail("executable not found: " + spec.executable);
    }
    std::wstring cmd;
    append_arg(cmd, exe);
    for (const std::string &arg : spec.args) {
      append_arg(cmd, win::widen(arg));
    }
    std::wstring env = child_environment();
    const std::wstring cwd = win::widen(spec.working_dir);

    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (!job) {
      return fail(win::error_text("CreateJobObject", GetLastError()));
    }
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
      const DWORD code = GetLastError();
      CloseHandle(job);
      return fail(win::error_text("SetInformationJobObject", code));
    }

    // Optional log file: the only handle the child inherits.
    HANDLE log = nullptr;
    STARTUPINFOEXW startup = {};
    startup.StartupInfo.cb = sizeof(startup);
    AttributeList attributes_list;
    DWORD flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT;
    if (!spec.log_file.empty()) {
      SECURITY_ATTRIBUTES inherit = {sizeof(inherit), nullptr, TRUE};
      log = CreateFileW(win::widen(spec.log_file).c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        &inherit, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
      if (log == INVALID_HANDLE_VALUE) {
        const DWORD code = GetLastError();
        CloseHandle(job);
        return fail(win::error_text("cannot open log file " + spec.log_file, code));
      }
      SIZE_T size = 0;
      InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
      attributes_list.storage = std::make_unique<uint8_t[]>(size);
      attributes_list.list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes_list.storage.get());
      if (!InitializeProcThreadAttributeList(attributes_list.list, 1, 0, &size) ||
          !UpdateProcThreadAttribute(attributes_list.list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, &log,
                                     sizeof(log), nullptr, nullptr))
      {
        const DWORD code = GetLastError();
        attributes_list.list = nullptr;
        CloseHandle(log);
        CloseHandle(job);
        return fail(win::error_text("cannot set up the child's handles", code));
      }
      startup.lpAttributeList = attributes_list.list;
      startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
      startup.StartupInfo.hStdInput = nullptr;
      startup.StartupInfo.hStdOutput = log;
      startup.StartupInfo.hStdError = log;
      flags |= EXTENDED_STARTUPINFO_PRESENT;
    }

    PROCESS_INFORMATION info = {};
    const BOOL created = CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, log ? TRUE : FALSE, flags,
                                        env.data(), cwd.empty() ? nullptr : cwd.c_str(), &startup.StartupInfo,
                                        &info);
    const DWORD create_error = GetLastError();
    if (log) {
      CloseHandle(log);
    }
    if (!created) {
      CloseHandle(job);
      return fail(win::error_text("cannot start " + spec.executable, create_error));
    }
    // Assign before the first instruction runs, so no grandchild escapes the job.
    if (!AssignProcessToJobObject(job, info.hProcess)) {
      const DWORD code = GetLastError();
      TerminateProcess(info.hProcess, 1);
      CloseHandle(info.hThread);
      CloseHandle(info.hProcess);
      CloseHandle(job);
      return fail(win::error_text("cannot put the child in a job object", code));
    }
    ResumeThread(info.hThread);
    CloseHandle(info.hThread);
    out.process_ = info.hProcess;
    out.job_ = job;
    out.pid_ = info.dwProcessId;
    return true;
  }
  catch (...) {
    return fail("out of memory launching " + spec.executable);
  }
}

bool launch_host(const HostLaunch &launch, HostProcess &out, std::string *error) noexcept
{
  try {
    if (launch.surface_port == 0 || launch.token.empty()) {
      if (error) *error = "launch_host: surface_port and token must come from Surface::port() / token()";
      return false;
    }
    ProcessSpec spec;
    spec.executable = launch.executable;
    spec.log_file = launch.log_file;
    if (!launch.app_dir.empty()) {
      spec.args.push_back(launch.app_dir);
    }
    // Electron exits at once (code -1, no output) when a URL-like argument is followed by
    // anything (its CVE-2018-1000006 guard), unless "--" comes first. "--" also keeps
    // Chromium from reading the host's options as its own switches (docs/fork/SURFACE.md §3.1).
    spec.args.push_back("--");
    char dpr[32];
    std::snprintf(dpr, sizeof(dpr), "%g", double(launch.dpr));
    const std::string args[] = {
        "--surface", "127.0.0.1:" + std::to_string(launch.surface_port),
        "--token", launch.token,
        "--url", launch.url,
        "--mode", launch.docked ? "docked" : "window",
        "--width", std::to_string(launch.width),
        "--height", std::to_string(launch.height),
        "--dpr", dpr,
        "--fps", std::to_string(launch.fps),
        "--max-in-flight", std::to_string(launch.max_in_flight),
    };
    spec.args.insert(spec.args.end(), std::begin(args), std::end(args));
    if (launch.adapter_luid) {
      char luid[17];
      std::snprintf(luid, sizeof(luid), "%016llx", static_cast<unsigned long long>(launch.adapter_luid));
      spec.args.push_back("--adapter-luid");
      spec.args.push_back(luid);
    }
    spec.args.insert(spec.args.end(), launch.extra_args.begin(), launch.extra_args.end());
    return launch_process(spec, out, error);
  }
  catch (...) {
    if (error) *error = "out of memory launching the runtime host";
    return false;
  }
}

}  // namespace gs::gameview
