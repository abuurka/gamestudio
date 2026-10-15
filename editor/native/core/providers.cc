// SPDX-License-Identifier: GPL-3.0-or-later
#include "providers.hh"

#include <cctype>

namespace gs {

void ProviderRegistry::add(EditorProvider provider)
{
  for (auto &existing : providers_) {
    if (existing->idname == provider.idname) {
      *existing = std::move(provider);
      return;
    }
  }
  providers_.push_back(std::make_unique<EditorProvider>(std::move(provider)));
}

bool ProviderRegistry::remove(const std::string &idname)
{
  for (auto it = providers_.begin(); it != providers_.end(); ++it) {
    if ((*it)->idname == idname) {
      providers_.erase(it);
      return true;
    }
  }
  return false;
}

const EditorProvider *ProviderRegistry::find(const std::string &idname) const
{
  for (const auto &provider : providers_) {
    if (provider->idname == idname) {
      return provider.get();
    }
  }
  return nullptr;
}

int ProviderRegistry::index_of(const std::string &idname) const
{
  for (int i = 0; i < int(providers_.size()); i++) {
    if (providers_[i]->idname == idname) {
      return i;
    }
  }
  return -1;
}

const EditorProvider *ProviderRegistry::at(int index) const
{
  return (index >= 0 && index < int(providers_.size())) ? providers_[index].get() : nullptr;
}

int ProviderRegistry::size() const
{
  return int(providers_.size());
}

ProviderRegistry &providers()
{
  static ProviderRegistry registry;
  return registry;
}

bool is_valid_provider_idname(const std::string &idname)
{
  if (idname.empty() || idname.size() > 62 || !std::isalpha(static_cast<unsigned char>(idname[0]))) {
    return false;
  }
  for (const char c : idname) {
    if (!std::isalnum(static_cast<unsigned char>(c)) && c != '_') {
      return false;
    }
  }
  return true;
}

}  // namespace gs
