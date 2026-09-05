#include <cassert>
#include <cstdio>

extern "C" int tigerengine_mapping_ownership_probe();

int main() {
  const int result = tigerengine_mapping_ownership_probe();
  if (result != 0) {
    std::fprintf(stderr, "mapping ownership probe failed: %d\n", result);
    return 1;
  }
  return 0;
}
