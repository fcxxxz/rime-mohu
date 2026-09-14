// 词边先验引擎测试：静态多字词作长句句中内部边，每边加有界分——
// 「词典里有这个词」在路径分中投票（librime entry_weight+Query 的加法
// 融合结构在 native 侧的对应物）。核心回归用例：
// vegeuurufaviiiyikbqiuuruyivgjuhw（这个输入法支持一口气输入一整句话）。
// 「支持」是 viii 的 rank-1 词条而「只吃」不是词，字符三元的局部优势
// （+6.18）会被「只吃一口」的跨词界粘连反杀（−7.26），净输 1.09；
// weight=1.5 的词条加票翻回「支持」。0 = 逐字节旧行为。模型或词表
// 缺失时打印 skip 并通过；TIGER_NGRAM / TIGER_LEXICON 可覆盖路径。
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

std::string repo_path(const char* suffix) {
  const char* cwd = getenv("MOHU_REPO");
  return std::string(cwd && *cwd ? cwd : ".") + suffix;
}

std::vector<std::string> decode_candidates(int handle, const char* raw,
                                           size_t limit = 10) {
  static char out[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, out, sizeof(out), nullptr);
  if (rc <= 0) return {};
  std::vector<std::string> texts;
  const char* p = out;
  const char* line_end = strchr(p, '\n');
  if (!line_end) return {};
  p = line_end + 1;
  while (*p && texts.size() < limit) {
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
      repo_path("/tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt"));
  for (const std::string* path : {&model, &lexicon}) {
    FILE* probe = fopen(path->c_str(), "rb");
    if (!probe) {
      printf("skip: %s not found\n", path->c_str());
      return 0;
    }
    fclose(probe);
  }

  const char* kRaw = "vegeuurufaviiiyikbqiuuruyivgjuhw";
  const std::string kWordPath = "这个输入法支持一口气输入一整句话";
  const std::string kGluePath = "这个输入法只吃一口气输入一整句话";

  char error[512] = {};
  int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                              error, sizeof(error));
  if (h < 0) {
    printf("fail: engine create: %s\n", error);
    return 1;
  }

  // 基线（weight=0，引擎默认）：粘连路径首选，词路径次选。
  std::vector<std::string> baseline = decode_candidates(h, kRaw, 5);
  if (baseline.size() < 2 || baseline[0] != kGluePath ||
      baseline[1] != kWordPath) {
    printf("skip: baseline does not show the glue-path flip\n");
    tiger_engine_free(h);
    return 0;
  }

  // 开启 1.5：词条加票翻案，两路径互换，候选集合不变。
  if (tiger_engine_set_word_edge_weight(h, 1.5) != 1) {
    printf("fail: enable word edge prior\n");
    return 1;
  }
  std::vector<std::string> enabled = decode_candidates(h, kRaw, 5);
  if (enabled.size() < 2 || enabled[0] != kWordPath ||
      enabled[1] != kGluePath) {
    printf("fail: word edge prior must flip 支持 above 只吃\n");
    return 1;
  }

  // 整段词查询输入（≤4 键无内部词边可用）在任何权重下逐字节不变。
  std::vector<std::string> word_lookup_off = decode_candidates(h, "viii", 5);
  if (tiger_engine_set_word_edge_weight(h, 0.0) != 1) {
    printf("fail: re-disable word edge prior\n");
    return 1;
  }
  std::vector<std::string> word_lookup_on = decode_candidates(h, "viii", 5);
  if (word_lookup_off != word_lookup_on || word_lookup_off.empty() ||
      word_lookup_off[0] != "支持") {
    printf("fail: whole-input word lookup must stay byte-identical\n");
    return 1;
  }

  // weight 回 0：恢复旧排序（旋钮可逆）。
  std::vector<std::string> restored = decode_candidates(h, kRaw, 5);
  if (restored != baseline) {
    printf("fail: weight 0 must restore the old ranking\n");
    return 1;
  }

  // 非法权重拒绝：负数与 >4。
  if (tiger_engine_set_word_edge_weight(h, -0.5) != -1 ||
      tiger_engine_set_word_edge_weight(h, 4.5) != -1) {
    printf("fail: out-of-range weights must be rejected\n");
    return 1;
  }

  tiger_engine_free(h);
  printf("ok: word edge prior flips 支持 over 只吃 and stays reversible\n");
  return 0;
}
