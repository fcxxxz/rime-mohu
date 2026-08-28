// 对拍工具：用 C 引擎跑与 bench_tiger.lua 相同的逐键基准，输出同格式结果。
// 用法: nativetest <model> <lexicon> <keystream文件> <输出文件>
// 编译: clang++ -O2 -std=c++17 nativetest.cc -L. -ltigerengine \
//         -Wl,-rpath,@executable_path -o nativetest
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <chrono>

#include "tigerengine.h"

int main(int argc, char** argv) {
  if (argc < 5) {
    fprintf(stderr, "usage: nativetest <model> <lexicon> <keys> <out>\n");
    return 2;
  }
  char err[512] = {0};
  int h = tiger_engine_create(argv[1], argv[2], 200, 1, err, sizeof(err));
  if (h < 0) {
    fprintf(stderr, "engine create failed: %s\n", err);
    return 3;
  }
  FILE* fin = fopen(argv[3], "r");
  FILE* fout = fopen(argv[4], "w");
  if (!fin || !fout) { fprintf(stderr, "io error\n"); return 4; }
  char line[4096];
  static char out[1 << 20];
  while (fgets(line, sizeof(line), fin)) {
    size_t n = strlen(line);
    while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
    char* tab = strchr(line, '\t');
    if (!tab) continue;
    *tab = 0;
    const char* sid = line;
    std::string keys = tab + 1;
    std::string parts;
    double total = 0, max_single = 0;
    for (size_t i = 1; i <= keys.size(); ++i) {
      std::string raw = keys.substr(0, i);
      double ms = 0;
      int rc = tiger_decode(h, raw.c_str(), 0, out, sizeof(out), &ms);
      if (rc < 0) { fprintf(stderr, "decode failed: %s\n", tiger_last_error()); return 5; }
      total += ms;
      if (ms > max_single) max_single = ms;
      const char* nl = strchr(out, '\n');
      if (nl) {
        const char* t2 = strchr(nl + 1, '\t');
        if (t2) parts.append(nl + 1, t2 - (nl + 1));
      }
      if (i < keys.size()) parts.push_back('\x1f');
    }
    fprintf(fout, "%s\t%s\t%zu\t%.3f\t%.3f\n", sid, parts.c_str(), keys.size(), total, max_single);
  }
  fclose(fin);
  fclose(fout);
  return 0;
}
