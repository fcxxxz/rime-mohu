// 用户调频层引擎测试：真实模型上的候选翻转、快照导出/导入回环、
// 权重开关、损坏快照整体拒绝。模型或词表缺失时打印 skip 并通过，
// 便于无模型环境跑 `make test`；TIGER_NGRAM / TIGER_LEXICON 可覆盖路径。
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <string>
#include <vector>

#include "tigerengine.h"

namespace {

std::string env_or(const char* name, const std::string& fallback) {
  const char* value = getenv(name);
  return value && *value ? value : fallback;
}

std::string home_path(const char* suffix) {
  const char* home = getenv("HOME");
  return std::string(home ? home : "") + suffix;
}

std::vector<std::string> decode_candidates(int handle, const char* raw) {
  static char out[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, out, sizeof(out), nullptr);
  if (rc <= 0) return {};
  std::vector<std::string> texts;
  const char* p = out;
  const char* line_end = strchr(p, '\n');
  if (!line_end) return {};
  p = line_end + 1;
  while (*p && texts.size() < 5) {
    line_end = strchr(p, '\n');
    if (!line_end) line_end = p + strlen(p);
    const char* tab = static_cast<const char*>(memchr(p, '\t', line_end - p));
    if (!tab) break;
    texts.emplace_back(p, tab - p);
    if (!*line_end) break;
    p = line_end + 1;
  }
  return texts;
}

}  // namespace

int main() {
  const std::string model = env_or("TIGER_NGRAM",
      home_path("/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin"));
  const std::string lexicon = env_or("TIGER_LEXICON",
      home_path("/Library/Rime/mohu/data/zrm/mohu_zrm.lexicon.txt"));
  for (const std::string* path : {&model, &lexicon}) {
    FILE* probe = fopen(path->c_str(), "rb");
    if (!probe) {
      printf("skip: %s not found\n", path->c_str());
      return 0;
    }
    fclose(probe);
  }

  char error[512] = {};
  const int h1 = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                                     error, sizeof(error));
  if (h1 < 0) {
    printf("fail: engine create: %s\n", error);
    return 1;
  }

  const std::vector<std::string> baseline = decode_candidates(h1, "ufqyhfmimh");
  if (baseline.size() < 2 || baseline[0].empty() || baseline[1].empty() ||
      baseline[0] == baseline[1]) {
    printf("skip: need two distinct candidates for the flip check\n");
    tiger_engine_free(h1);
    return 0;
  }
  const std::string& first = baseline[0];
  const std::string& second = baseline[1];

  // 反复喂入次选文本 → 用户层应把它推为首选。
  for (int i = 0; i < 150; ++i) {
    if (tiger_engine_update_user_model(h1, second.c_str()) != 1) {
      printf("fail: update_user_model rc\n");
      return 1;
    }
  }
  std::vector<std::string> fed = decode_candidates(h1, "ufqyhfmimh");
  if (fed.empty() || fed[0] != second) {
    printf("fail: feeding the runner-up must flip the ranking\n");
    return 1;
  }

  // 静态权重 1.0 关闭用户层 → 完全回到基线首选。
  if (tiger_engine_set_user_model_weight(h1, 1.0) != 1) {
    printf("fail: set weight\n");
    return 1;
  }
  fed = decode_candidates(h1, "ufqyhfmimh");
  if (fed.empty() || fed[0] != first) {
    printf("fail: weight 1.0 must restore the pure static ranking\n");
    return 1;
  }
  tiger_engine_set_user_model_weight(h1, 0.85);

  // 快照回环：导出 → 新引擎导入 → 行为一致。
  size_t blob_size = 0;
  char* blob = tiger_engine_user_model_export(h1, &blob_size);
  if (!blob || blob_size == 0) {
    printf("fail: export\n");
    return 1;
  }
  const int h2 = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                                     error, sizeof(error));
  if (h2 < 0) {
    printf("fail: second engine create: %s\n", error);
    return 1;
  }
  if (tiger_engine_user_model_import(h2, blob, blob_size) != 1) {
    printf("fail: import\n");
    return 1;
  }
  const std::vector<std::string> restored = decode_candidates(h2, "ufqyhfmimh");
  if (restored.empty() || restored[0] != second) {
    printf("fail: import must restore the fed ranking\n");
    return 1;
  }

  // 损坏（截断）快照必须被整体拒绝，且引擎保持可用。
  if (tiger_engine_user_model_import(h2, blob, blob_size / 2) != -1) {
    printf("fail: truncated snapshot must be rejected\n");
    return 1;
  }
  const std::vector<std::string> after_corrupt = decode_candidates(h2, "ufqyhfmimh");
  if (after_corrupt.empty() || after_corrupt[0] != second) {
    printf("fail: engine must survive a corrupt import\n");
    return 1;
  }

  free(blob);
  tiger_engine_free(h1);
  tiger_engine_free(h2);
  printf("tigerengine user model tests passed (flip: %s <- %s)\n",
         second.c_str(), first.c_str());
  return 0;
}
