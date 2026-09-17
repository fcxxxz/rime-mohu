// Minimal native engine probe: decode one raw query and print the ranked
// candidates. Usage: probe_decode <model.bin> <lexicon.txt> <query> [beam]
#include <cstdio>
#include <cstring>
#include <string>

#include "tigerengine.h"

int main(int argc, char** argv) {
  if (argc < 4) {
    std::fprintf(stderr, "usage: probe_decode <model.bin> <lexicon.txt> <query> [beam=200]\n");
    return 2;
  }
  const int beam = argc > 4 ? std::atoi(argv[4]) : 200;
  char error[512] = {};
  const int handle = tiger_engine_create(argv[1], argv[2], beam, 1, error, sizeof(error));
  if (handle < 0) {
    std::fprintf(stderr, "engine create failed: %s\n",
                 error[0] ? error : tiger_last_error());
    return 1;
  }
  std::string output(1 << 20, '\0');
  const int rc = tiger_decode_full(handle, argv[3], 0, output.data(),
                                   static_cast<int>(output.size()));
  if (rc < 0) {
    std::fprintf(stderr, "decode failed: rc=%d %s\n", rc, tiger_last_error());
    tiger_engine_free(handle);
    return 1;
  }
  std::printf("%s", output.c_str());
  std::fprintf(stderr, "[candidates: %d]\n", rc);
  tiger_engine_free(handle);
  return 0;
}
