// SPDX-License-Identifier: GPL-3.0-or-later

#include "d3d11_bridge.hh"

#include <cstring>

namespace gs::gameview {

using Microsoft::WRL::ComPtr;

namespace {

uint64_t luid_value(const LUID &luid)
{
  return (uint64_t(uint32_t(luid.HighPart)) << 32) | luid.LowPart;
}

std::vector<ComPtr<IDXGIAdapter1>> list_adapters()
{
  std::vector<ComPtr<IDXGIAdapter1>> adapters;
  ComPtr<IDXGIFactory1> factory;
  if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) {
    return adapters;
  }
  ComPtr<IDXGIAdapter1> adapter;
  for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; i++) {
    adapters.push_back(adapter);
  }
  return adapters;
}

bool drives_primary_monitor(IDXGIAdapter1 *adapter)
{
  const HMONITOR primary = MonitorFromPoint({0, 0}, MONITOR_DEFAULTTOPRIMARY);
  ComPtr<IDXGIOutput> output;
  for (UINT j = 0; adapter->EnumOutputs(j, &output) != DXGI_ERROR_NOT_FOUND; j++) {
    DXGI_OUTPUT_DESC desc;
    if (SUCCEEDED(output->GetDesc(&desc)) && desc.Monitor == primary) {
      return true;
    }
  }
  return false;
}

bool is_rgba8_family(DXGI_FORMAT format)
{
  switch (format) {
    case DXGI_FORMAT_B8G8R8A8_UNORM:
    case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
    case DXGI_FORMAT_B8G8R8A8_TYPELESS:
    case DXGI_FORMAT_R8G8B8A8_UNORM:
    case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
    case DXGI_FORMAT_R8G8B8A8_TYPELESS:
      return true;
    default:
      return false;
  }
}

std::string hr_text(std::string_view what, HRESULT hr)
{
  return win::error_text(what, static_cast<unsigned long>(hr));
}

}  // namespace

uint64_t default_adapter_luid() noexcept
{
  try {
    for (const auto &adapter : list_adapters()) {
      if (drives_primary_monitor(adapter.Get())) {
        DXGI_ADAPTER_DESC1 desc;
        adapter->GetDesc1(&desc);
        return luid_value(desc.AdapterLuid);
      }
    }
  }
  catch (...) {
  }
  return 0;
}

D3D11Bridge::~D3D11Bridge()
{
  copies_.clear();
  staging_.clear();
  win::close_handle(fence_event_);
}

bool D3D11Bridge::init(const AdapterChoice &choice, int staging_slots, std::string &error)
{
  const auto adapters = list_adapters();
  if (adapters.empty()) {
    error = "no DXGI adapters found";
    return false;
  }
  const auto by_luid = [&](uint64_t luid) -> IDXGIAdapter1 * {
    for (const auto &a : adapters) {
      DXGI_ADAPTER_DESC1 d;
      if (SUCCEEDED(a->GetDesc1(&d)) && luid_value(d.AdapterLuid) == luid) {
        return a.Get();
      }
    }
    return nullptr;
  };
  IDXGIAdapter1 *adapter = choice.host_luid ? by_luid(choice.host_luid) : nullptr;
  if (choice.host_luid && !adapter) {
    error = "the host renders on an adapter this process cannot see (LUID mismatch)";
    return false;
  }
  for (size_t i = 0; !adapter && choice.vendor_id && i < adapters.size(); i++) {
    DXGI_ADAPTER_DESC1 d;
    if (SUCCEEDED(adapters[i]->GetDesc1(&d)) && !(d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) &&
        d.VendorId == choice.vendor_id && d.DeviceId == choice.device_id)
    {
      adapter = adapters[i].Get();
    }
  }
  if (!adapter && choice.fallback_luid) {
    adapter = by_luid(choice.fallback_luid);
  }
  for (size_t i = 0; !adapter && i < adapters.size(); i++) {
    if (drives_primary_monitor(adapters[i].Get())) {
      adapter = adapters[i].Get();
    }
  }
  if (!adapter) {
    adapter = adapters[0].Get();
  }

  DXGI_ADAPTER_DESC1 desc;
  adapter->GetDesc1(&desc);
  adapter_luid_ = luid_value(desc.AdapterLuid);
  adapter_name_ = win::narrow(desc.Description);

  const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1,
                                      D3D_FEATURE_LEVEL_10_0};
  ComPtr<ID3D11Device> device;
  HRESULT hr = D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, levels, UINT(std::size(levels)),
                                 D3D11_SDK_VERSION, &device, nullptr, &context_);
  if (FAILED(hr)) {
    error = hr_text("D3D11CreateDevice on " + adapter_name_, hr);
    return false;
  }
  if (FAILED(device.As(&device_))) {
    error = "Direct3D 11.1 (ID3D11Device1) is required to open shared NT handles";
    return false;
  }
  // Fences (Windows 10 1703+) let the worker sleep until a copy is done; without them Map waits.
  ComPtr<ID3D11Device5> device5;
  if (SUCCEEDED(device.As(&device5)) && SUCCEEDED(context_.As(&context4_)) &&
      SUCCEEDED(device5->CreateFence(0, D3D11_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence_))))
  {
    fence_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  }
  if (!fence_event_) {
    fence_.Reset();
    context4_.Reset();
  }
  staging_.resize(size_t(staging_slots < 1 ? 1 : staging_slots > 4 ? 4 : staging_slots));
  return true;
}

bool D3D11Bridge::can_begin() const
{
  for (const Staging &s : staging_) {
    if (!s.busy) {
      return true;
    }
  }
  return false;
}

bool D3D11Bridge::begin(const protocol::Frame &frame, HANDLE host_process, std::string &error)
{
  const double t0 = now_ms();
  const std::string id = std::to_string(frame.frame_id);
  HANDLE local = nullptr;
  if (!DuplicateHandle(host_process, reinterpret_cast<HANDLE>(uintptr_t(frame.handle)), GetCurrentProcess(), &local,
                       0, FALSE, DUPLICATE_SAME_ACCESS))
  {
    error = win::error_text("DuplicateHandle for frame " + id, GetLastError());
    return false;
  }
  ComPtr<ID3D11Texture2D> source;
  const HRESULT hr = device_->OpenSharedResource1(local, IID_PPV_ARGS(&source));
  CloseHandle(local);  // The opened resource keeps its own reference.
  if (FAILED(hr)) {
    error = hr_text("OpenSharedResource1 for frame " + id + " on " + adapter_name_ +
                        " (does the host render on another GPU? pass HostLaunch::adapter_luid)",
                    hr);
    return false;
  }
  D3D11_TEXTURE2D_DESC desc;
  source->GetDesc(&desc);
  if (int(desc.Width) != frame.width || int(desc.Height) != frame.height || !is_rgba8_family(desc.Format) ||
      desc.ArraySize != 1 || desc.SampleDesc.Count != 1)
  {
    error = "frame " + id + ": the shared texture (" + std::to_string(desc.Width) + "x" +
            std::to_string(desc.Height) + ", DXGI format " + std::to_string(int(desc.Format)) +
            ") does not match the message (" + std::to_string(frame.width) + "x" + std::to_string(frame.height) +
            ", 8-bit RGBA); stale handle?";
    return false;
  }

  int slot = -1;
  for (size_t i = 0; i < staging_.size() && slot < 0; i++) {
    if (!staging_[i].busy) {
      slot = int(i);
    }
  }
  if (slot < 0) {
    error = "no free staging texture";
    return false;
  }
  Staging &staging = staging_[size_t(slot)];
  if (!staging.texture || staging.width != desc.Width || staging.height != desc.Height || staging.format != desc.Format)
  {
    D3D11_TEXTURE2D_DESC sd = {};
    sd.Width = desc.Width;
    sd.Height = desc.Height;
    sd.MipLevels = 1;
    sd.ArraySize = 1;
    sd.Format = desc.Format;
    sd.SampleDesc.Count = 1;
    sd.Usage = D3D11_USAGE_STAGING;
    sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging.texture.Reset();
    const HRESULT created = device_->CreateTexture2D(&sd, nullptr, &staging.texture);
    if (FAILED(created)) {
      error = hr_text("CreateTexture2D (staging " + std::to_string(desc.Width) + "x" + std::to_string(desc.Height) + ")",
                      created);
      return false;
    }
    staging.width = desc.Width;
    staging.height = desc.Height;
    staging.format = desc.Format;
  }

  context_->CopyResource(staging.texture.Get(), source.Get());
  Copy copy;
  if (context4_) {
    context4_->Signal(fence_.Get(), ++fence_value_);
    copy.fence_value = fence_value_;
  }
  context_->Flush();
  staging.busy = true;
  copy.info.frame = frame;
  copy.info.frame.format = (desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB ||
                            desc.Format == DXGI_FORMAT_R8G8B8A8_TYPELESS) ?
                               PixelFormat::RGBA8 :
                               PixelFormat::BGRA8;
  copy.info.slot = slot;
  copy.source = std::move(source);
  copy.submitted_ms = now_ms();
  copy.info.open_ms = copy.submitted_ms - t0;
  copies_.push_back(std::move(copy));
  if (copies_.size() == 1) {
    arm_fence();
  }
  return true;
}

void D3D11Bridge::arm_fence()
{
  if (!fence_ || copies_.empty()) {
    return;
  }
  ResetEvent(fence_event_);
  fence_->SetEventOnCompletion(copies_.front().fence_value, fence_event_);
}

void D3D11Bridge::collect(std::vector<Finished> &out)
{
  const uint64_t completed = fence_ ? fence_->GetCompletedValue() : UINT64_MAX;
  const double now = now_ms();
  bool popped = false;
  while (!copies_.empty() && copies_.front().fence_value <= completed) {
    Copy &copy = copies_.front();
    copy.info.copy_ms = now - copy.submitted_ms;
    out.push_back(copy.info);
    copies_.pop_front();
    popped = true;
  }
  if (popped) {
    arm_fence();
  }
}

void D3D11Bridge::mark_revoked(uint64_t frame_id)
{
  for (Copy &copy : copies_) {
    if (copy.info.frame.frame_id == frame_id) {
      copy.info.revoked = true;
    }
  }
}

bool D3D11Bridge::read(const Finished &done, uint8_t *dst, double &map_ms, double &memcpy_ms, std::string &error)
{
  Staging &staging = staging_[size_t(done.slot)];
  D3D11_MAPPED_SUBRESOURCE mapped;
  const double t0 = now_ms();
  const HRESULT hr = context_->Map(staging.texture.Get(), 0, D3D11_MAP_READ, 0, &mapped);
  const double t1 = now_ms();
  map_ms = t1 - t0;
  if (FAILED(hr)) {
    error = hr_text("Map (staging texture)", hr);
    return false;
  }
  const size_t row = size_t(done.frame.width) * 4;
  const auto *src = static_cast<const uint8_t *>(mapped.pData);
  if (mapped.RowPitch == row) {
    std::memcpy(dst, src, row * size_t(done.frame.height));
  }
  else {
    for (int y = 0; y < done.frame.height; y++) {
      std::memcpy(dst + size_t(y) * row, src + size_t(y) * mapped.RowPitch, row);
    }
  }
  context_->Unmap(staging.texture.Get(), 0);
  memcpy_ms = now_ms() - t1;
  return true;
}

void D3D11Bridge::recycle(const Finished &done)
{
  staging_[size_t(done.slot)].busy = false;
}

}  // namespace gs::gameview
