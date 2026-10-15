// SPDX-License-Identifier: GPL-3.0-or-later
// Placeholder backend for platforms without a docked Game View yet (macOS: IOSurface over a
// Mach port; Linux: DMA-BUF over SCM_RIGHTS; see docs/fork/SURFACE.md §6). Everything fails
// cleanly, so the editor can fall back to the detached window.

#include "../surface_impl.hh"

#include <random>

namespace gs::gameview {

namespace {
constexpr const char *kUnsupported = "the docked Game View is not implemented on this platform yet";
}

std::unique_ptr<Worker> create_worker(Surface::Impl & /*impl*/, std::string *error) noexcept
{
  if (error) *error = kUnsupported;
  return nullptr;
}

bool secure_random(uint8_t *out, size_t size) noexcept
{
  try {
    std::random_device device;
    for (size_t i = 0; i < size; i++) {
      out[i] = uint8_t(device());
    }
    return true;
  }
  catch (...) {
    return false;
  }
}

uint64_t default_adapter_luid() noexcept
{
  return 0;
}

HostProcess::~HostProcess() = default;
HostProcess::HostProcess(HostProcess &&other) noexcept : process_(other.process_), job_(other.job_), pid_(other.pid_)
{
  other.process_ = other.job_ = nullptr;
  other.pid_ = 0;
}
HostProcess &HostProcess::operator=(HostProcess &&other) noexcept
{
  process_ = other.process_;
  job_ = other.job_;
  pid_ = other.pid_;
  other.process_ = other.job_ = nullptr;
  other.pid_ = 0;
  return *this;
}
void HostProcess::reset() noexcept {}
bool HostProcess::running() const noexcept
{
  return false;
}
bool HostProcess::wait(int /*timeout_ms*/, int * /*exit_code*/) const noexcept
{
  return true;
}
void HostProcess::terminate() noexcept {}

bool launch_process(const ProcessSpec & /*spec*/, HostProcess & /*out*/, std::string *error) noexcept
{
  if (error) *error = kUnsupported;
  return false;
}

bool launch_host(const HostLaunch & /*launch*/, HostProcess & /*out*/, std::string *error) noexcept
{
  if (error) *error = kUnsupported;
  return false;
}

}  // namespace gs::gameview
