// 跨候选左上文引擎测试：真实模型上 set_decode_context 的三态语义、
// 上下文双向改变候选分数、清除后逐分回到基线。模型或词表缺失时打印
// skip 并通过；TIGER_NGRAM / TIGER_LEXICON 可覆盖路径。
// 语义与 librime 对齐：传整段最近上屏文本，尾部窗口由引擎按
// window_chars（默认 2）截取。
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <map>
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

std::map<std::string, double> decode_scores(int handle, const char* raw) {
  static char out[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, out, sizeof(out), nullptr);
  std::map<std::string, double> scores;
  if (rc <= 0) return scores;
  const char* p = strchr(out, '\n');
  if (!p) return scores;
  p += 1;
  int n = 0;
  while (*p && n < 8) {
    const char* e = strchr(p, '\n');
    if (!e) e = p + strlen(p);
    std::vector<std::string> fields;
    const char* q = p;
    while (q < e) {
      const char* t = (const char*)memchr(q, '\t', e - q);
      if (!t || t >= e) { fields.emplace_back(q, e - q); break; }
      fields.emplace_back(q, t - q);
      q = t + 1;
    }
    if (fields.size() >= 3) {
      scores[fields[0]] = atof(fields[2].c_str());
      ++n;
    }
    if (!*e) break;
    p = e + 1;
  }
  return scores;
}

}  // namespace

int main() {
  const std::string model = env_or("TIGER_NGRAM",
      home_path("/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin"));
  const std::string lexicon = env_or("TIGER_LEXICON",
      "tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt");
  for (const std::string* path : {&model, &lexicon}) {
    FILE* probe = fopen(path->c_str(), "rb");
    if (!probe) {
      printf("skip: %s not found\n", path->c_str());
      return 0;
    }
    fclose(probe);
  }

  char err[512] = {0};
  const int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                                    err, sizeof(err));
  if (h < 0) {
    printf("fail: create %s\n", err);
    return 1;
  }

  auto base = decode_scores(h, "ubji");
  if (!base.count("手机") || !base.count("收集")) {
    printf("fail: baseline missing 手机/收集\n");
    return 1;
  }
  printf("baseline 手机=%.3f 收集=%.3f\n", base["手机"], base["收集"]);

  // 三态：首次应用=1，重复同一文本=0（逐键零开销），空指针=-1。
  if (tiger_engine_set_decode_context(h, "他说：他向来。", 0) != 1) {
    printf("fail: first context apply\n");
    return 1;
  }
  if (tiger_engine_set_decode_context(h, "他说：他向来。", 0) != 0) {
    printf("fail: same context must be a no-change\n");
    return 1;
  }
  if (tiger_engine_set_decode_context(h, nullptr, 0) != -1) {
    printf("fail: null text must error\n");
    return 1;
  }

  // 「向来」上文应显著抬升 收集、压制 手机（log 分数为负值，升=数值变大）。
  auto toward = decode_scores(h, "ubji");
  if (!toward.count("手机") || !toward.count("收集")) {
    printf("fail: contextual decode missing rows\n");
    return 1;
  }
  printf("context(..向来) 手机=%.3f 收集=%.3f\n", toward["手机"], toward["收集"]);
  if (!(toward["收集"] - base["收集"] > 0.5)) {
    printf("fail: 收集 must improve under 向来 context\n");
    return 1;
  }
  if (!(toward["手机"] < base["手机"])) {
    printf("fail: 手机 must be penalized under 向来 context\n");
    return 1;
  }

  // 「士兵」上文反向：收集 不升反降。
  if (tiger_engine_set_decode_context(h, "士兵", 0) != 1) {
    printf("fail: switch context\n");
    return 1;
  }
  auto away = decode_scores(h, "ubji");
  if (!(away["收集"] < base["收集"])) {
    printf("fail: 收集 must drop under 士兵 context\n");
    return 1;
  }

  // 清除后逐分回到基线。
  if (tiger_engine_set_decode_context(h, "", 0) != 1) {
    printf("fail: clear context apply\n");
    return 1;
  }
  auto cleared = decode_scores(h, "ubji");
  if (cleared["手机"] != base["手机"] || cleared["收集"] != base["收集"]) {
    printf("fail: cleared scores must match baseline exactly\n");
    return 1;
  }

  // 无汉字文本等价清除（无变化）。
  if (tiger_engine_set_decode_context(h, "abc123", 0) != 0) {
    printf("fail: cjk-free text is a no-op after clear\n");
    return 1;
  }

  // 窗口=1：只条件尾部 1 字，语义可用。
  if (tiger_engine_set_decode_context(h, "他向来", 1) != 1) {
    printf("fail: window=1 apply\n");
    return 1;
  }

  tiger_engine_free(h);
  printf("pass: decode context ordering\n");
  return 0;
}
