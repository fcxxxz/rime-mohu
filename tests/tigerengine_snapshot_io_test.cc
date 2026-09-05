#include <cassert>
#include <chrono>
#include <filesystem>
#include <cstdlib>
#include <cstring>
#include <string>

#include "tigerengine.h"

int main() {
  const auto nonce = std::chrono::high_resolution_clock::now().time_since_epoch().count();
  const auto root = std::filesystem::temp_directory_path() /
                    ("mohu-tiger-file-replace-" + std::to_string(nonce));
  const auto unicode_root = root / std::filesystem::u8path("\xE7\x94\xA8\xE6\x88\xB7");
  std::filesystem::create_directories(unicode_root);

  const auto destination = unicode_root / "snapshot.bin";
  const std::string destination_utf8 = destination.u8string();
  const char old_snapshot[] = "old snapshot";
  const char new_snapshot[] = "new snapshot\0with binary data";

  assert(tiger_atomic_write_snapshot_file(destination_utf8.c_str(), old_snapshot,
                                          sizeof(old_snapshot) - 1) == 0);
  assert(tiger_atomic_write_snapshot_file(destination_utf8.c_str(), new_snapshot,
                                          sizeof(new_snapshot) - 1) == 0);

  size_t size = 0;
  char* blob = tiger_read_snapshot_file(destination_utf8.c_str(), &size);
  assert(blob != nullptr);
  assert(size == sizeof(new_snapshot) - 1);
  assert(std::memcmp(blob, new_snapshot, size) == 0);
  std::free(blob);

  const auto missing = unicode_root / "missing" / "snapshot.bin";
  assert(tiger_atomic_write_snapshot_file(missing.u8string().c_str(), old_snapshot,
                                          sizeof(old_snapshot) - 1) == -1);
  blob = tiger_read_snapshot_file(destination_utf8.c_str(), &size);
  assert(blob != nullptr && size == sizeof(new_snapshot) - 1);
  assert(std::memcmp(blob, new_snapshot, size) == 0);
  std::free(blob);

  for (const auto& entry : std::filesystem::directory_iterator(unicode_root)) {
    assert(entry.path() == destination);
  }

  std::filesystem::remove_all(root);
  return 0;
}
