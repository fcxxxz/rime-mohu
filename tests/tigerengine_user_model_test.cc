// 用户调频层引擎测试：真实模型上的候选翻转、快照导出/导入回环、
// 权重开关、损坏快照整体拒绝。模型或词表缺失时打印 skip 并通过，
// 便于无模型环境跑 `make test`；TIGER_NGRAM / TIGER_LEXICON 可覆盖路径。
#include <cmath>
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

// 取指定文本在菜单中的分值（用于层开关/快照回环的精确断言）。
bool score_of(int handle, const char* raw, const std::string& want,
              double* out) {
  static char out_buf[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, out_buf, sizeof(out_buf), nullptr);
  if (rc <= 0) return false;
  const char* p = strchr(out_buf, '\n');
  if (!p) return false;
  p += 1;
  while (*p) {
    const char* line_end = strchr(p, '\n');
    if (!line_end) line_end = p + strlen(p);
    const char* tab = static_cast<const char*>(memchr(p, '\t', line_end - p));
    if (!tab) break;
    if (want == std::string(p, tab - p)) {
      // 行格式：text \t segmented \t score \t confidence \t maxrank \t …
      const char* s = tab + 1;
      const char* s_tab = static_cast<const char*>(memchr(s, '\t', line_end - s));
      if (!s_tab) return false;
      s = s_tab + 1;
      s_tab = static_cast<const char*>(memchr(s, '\t', line_end - s));
      if (!s_tab) return false;
      *out = atof(std::string(s, s_tab - s).c_str());
      return true;
    }
    if (!*line_end) break;
    p = line_end + 1;
  }
  return false;
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

  double second_baseline_score = 0.0;
  if (!score_of(h1, "ufqyhfmimh", second, &second_baseline_score)) {
    printf("fail: baseline score lookup for the runner-up\n");
    return 1;
  }

  // Personal lexical edges are updated synchronously and become preferred
  // after repeated selections, without requiring a full snapshot refresh.
  for (int i = 0; i < 5; ++i) {
    if (tiger_engine_adjust_personal(h1, "jmkyfu", "简快符", 1) != 1) {
      printf("fail: personal edge delta\n");
      return 1;
    }
  }
  const std::vector<std::string> learned_word = decode_candidates(h1, "jmkyfu");
  if (learned_word.empty() || learned_word[0] != "简快符") {
    printf("fail: repeated personal edge deltas must promote the learned word\n");
    return 1;
  }

  // 反复喂入次选文本 → 用户层应把它推为首选。
  for (int i = 0; i < 150; ++i) {
    if (tiger_engine_update_user_model(h1, second.c_str()) != 1) {
      printf("fail: update_user_model rc\n");
      return 1;
    }
  }
  // 2026-10-02 BOS 预算后新契约：神情/申请 只差头两字，纯 trigram 喂入
  // 不再靠 BOS 锚定翻首选（词级独立提交的锚定证据被封顶），学习效果改由
  // 分数断言承载；生产双通道翻转（整段个人边）见文件末尾。
  std::vector<std::string> fed = decode_candidates(h1, "ufqyhfmimh");
  if (fed.empty() || fed[0] != first) {
    printf("fail: head-only trigram anchoring must not flip the ranking\n");
    return 1;
  }

  double second_fed_score = 0.0;
  if (!score_of(h1, "ufqyhfmimh", second, &second_fed_score) ||
      second_fed_score <= second_baseline_score + 0.5) {
    printf("fail: feeding must measurably lift the runner-up score (%.3f -> %.3f)\n",
           second_baseline_score, second_fed_score);
    return 1;
  }
  // 静态权重 1.0 关闭用户层 → 分数精确回到基线、排序回到基线首选。
  if (tiger_engine_set_user_model_weight(h1, 1.0) != 1) {
    printf("fail: set weight\n");
    return 1;
  }
  double second_static_score = 0.0;
  if (!score_of(h1, "ufqyhfmimh", second, &second_static_score) ||
      fabs(second_static_score - second_baseline_score) > 1e-6) {
    printf("fail: weight 1.0 must restore the exact static score (%.6f vs %.6f)\n",
           second_static_score, second_baseline_score);
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
  if (restored.empty() || restored[0] != first) {
    printf("fail: import must keep the protected ranking\n");
    return 1;
  }
  double h1_score = 0.0, h2_score = 0.0;
  if (!score_of(h1, "ufqyhfmimh", second, &h1_score) ||
      !score_of(h2, "ufqyhfmimh", second, &h2_score) ||
      fabs(h1_score - h2_score) > 1e-6) {
    printf("fail: import must restore the fed scores (%.6f vs %.6f)\n",
           h1_score, h2_score);
    return 1;
  }

  // 损坏（截断）快照必须被整体拒绝，且引擎保持可用。
  if (tiger_engine_user_model_import(h2, blob, blob_size / 2) != -1) {
    printf("fail: truncated snapshot must be rejected\n");
    return 1;
  }
  const std::vector<std::string> after_corrupt = decode_candidates(h2, "ufqyhfmimh");
  if (after_corrupt.empty() || after_corrupt[0] != first) {
    printf("fail: engine must survive a corrupt import\n");
    return 1;
  }

  // 反学习：按喂入次数对冲扣减（同文本同窗口的 update 逆操作）→
  // 用户层翻案撤销，回到基线首选。删词即反学习，不需正确词再硬喂对冲。
  if (tiger_engine_forget_text(h1, second.c_str(), 150) != 1) {
    printf("fail: forget rc\n");
    return 1;
  }
  const std::vector<std::string> forgotten = decode_candidates(h1, "ufqyhfmimh");
  if (forgotten.empty() || forgotten[0] != first) {
    printf("fail: forgetting the fed counts must restore the baseline ranking\n");
    return 1;
  }
  // 非法参数拒绝：空 times 与超界。
  if (tiger_engine_forget_text(h1, second.c_str(), 0) != -1 ||
      tiger_engine_forget_text(h1, second.c_str(), 1000001) != -1) {
    printf("fail: out-of-range forget times must be rejected\n");
    return 1;
  }

  // 过度反学习：times 大于存量时地板 0，不得回绕成反向加成。两段式删词
  // 若两按都扣，正是这条路径（现由 already_forgotten 挡掉，此处守住底线）。
  if (tiger_engine_forget_text(h1, second.c_str(), 150) != 1) {
    printf("fail: over-forget rc\n");
    return 1;
  }
  const std::vector<std::string> over = decode_candidates(h1, "ufqyhfmimh");
  if (over.empty() || over[0] != first) {
    printf("fail: over-forgetting must floor at zero, not invert the ranking\n");
    return 1;
  }

  // 从未喂过的文本：窗口不存在，扣减应为空操作，排序不变。
  if (tiger_engine_forget_text(h1, "翾鬻齾", 10) != 1) {
    printf("fail: forget unfed text rc\n");
    return 1;
  }
  const std::vector<std::string> unfed = decode_candidates(h1, "ufqyhfmimh");
  if (unfed.empty() || unfed[0] != first) {
    printf("fail: forgetting unfed text must not change the ranking\n");
    return 1;
  }

  // 反学习后快照回环：新引擎导入反学习后的快照仍停在基线，即重启不会
  // 从旧快照复活（只改内存不落盘的话这里会翻回 second）。
  size_t forgotten_size = 0;
  char* forgotten_blob = tiger_engine_user_model_export(h1, &forgotten_size);
  if (!forgotten_blob || forgotten_size == 0) {
    printf("fail: export after forget\n");
    return 1;
  }
  const int h3 = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                                     error, sizeof(error));
  if (h3 < 0) {
    printf("fail: third engine create: %s\n", error);
    return 1;
  }
  if (tiger_engine_user_model_import(h3, forgotten_blob, forgotten_size) != 1) {
    printf("fail: import after forget\n");
    return 1;
  }
  const std::vector<std::string> reforgotten = decode_candidates(h3, "ufqyhfmimh");
  if (reforgotten.empty() || reforgotten[0] != first) {
    printf("fail: forgetting must survive an export/import round trip\n");
    return 1;
  }

  // 生产提交双通道契约：整段个人边（提交同步 adjust_personal，整段命中
  // 保持全额 boost）+ trigram 必须能把次选推为首选——BOS 预算只封锚定
  // 通道，不封真实学习闭环。
  if (tiger_engine_adjust_personal(h1, "ufqyhfmimh", second.c_str(), 1) != 1) {
    printf("fail: whole-input personal edge apply\n");
    return 1;
  }
  const std::vector<std::string> promoted = decode_candidates(h1, "ufqyhfmimh");
  if (promoted.empty() || promoted[0] != second) {
    printf("fail: production dual-channel commit must promote the learned text\n");
    return 1;
  }

  free(blob);
  free(forgotten_blob);
  tiger_engine_free(h3);
  tiger_engine_free(h1);
  tiger_engine_free(h2);
  printf("tigerengine user model tests passed (dual-channel promote: %s <- %s)\n",
         second.c_str(), first.c_str());
  return 0;
}
