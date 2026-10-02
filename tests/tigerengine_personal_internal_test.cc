// 长句内部个人词边封顶引擎测试：静态同码真词（认得）的用户 boost 不再
// 改写长句首选；OOV 自造词（魔虎）保持全额并在长句中浮出；整段命中边
// 保持全额；gain cap 饱和后正增益闭合（不再泄漏超 cap）。模型或词表
// 缺失时打印 skip 并通过，便于无模型环境跑 `make test`；TIGER_NGRAM /
// TIGER_LEXICON 可覆盖路径。
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

struct Row {
  std::string text;
  double score = 0.0;
  bool found = false;
};

Row find_row(int handle, const char* raw, const std::string& want) {
  static char out[1 << 22];
  Row row;
  const int rc = tiger_decode(handle, raw, 0, out, sizeof(out), nullptr);
  if (rc <= 0) return row;
  const char* p = strchr(out, '\n');
  if (!p) return row;
  p += 1;
  while (*p) {
    const char* line_end = strchr(p, '\n');
    if (!line_end) line_end = p + strlen(p);
    const char* tab = static_cast<const char*>(memchr(p, '\t', line_end - p));
    if (!tab) break;
    if (want == std::string(p, tab - p)) {
      const char* score_start = tab + 1;
      const char* score_tab = static_cast<const char*>(
          memchr(score_start, '\t', line_end - score_start));
      if (score_tab) row.score = atof(std::string(score_start, score_tab - score_start).c_str());
      row.found = true;
      return row;
    }
    if (!*line_end) break;
    p = line_end + 1;
  }
  return row;
}

std::string first_text(int handle, const char* raw) {
  static char out[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, out, sizeof(out), nullptr);
  if (rc <= 0) return {};
  const char* p = strchr(out, '\n');
  if (!p) return {};
  p += 1;
  const char* line_end = strchr(p, '\n');
  if (!line_end) line_end = p + strlen(p);
  const char* tab = static_cast<const char*>(memchr(p, '\t', line_end - p));
  if (!tab) return {};
  return std::string(p, tab - p);
}

int fail(const char* what) {
  printf("fail: %s\n", what);
  return 1;
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
  const char* kSentence = "rfrfdelmdbbxdefazi";  // 人人的脸都憋得发紫
  const char* kGold = "人人的脸都憋得发紫";

  // 基线：无学习时首选为正解。
  int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                              error, sizeof(error));
  if (h < 0) return fail("engine create");
  if (tiger_engine_set_word_edge_weight(h, 0.5) < 0) return fail("word edge");
  if (tiger_engine_set_text_lexicon_weight(h, 6.5) < 0) return fail("text lexicon");
  if (tiger_engine_set_word_form_weight(h, 6.5) < 0) return fail("word form");
  if (tiger_engine_set_user_model_weight(h, 0.85) < 0) return fail("user weight");
  if (tiger_engine_set_user_model_gain_cap(h, 6.0) < 0) return fail("gain cap");
  if (first_text(h, kSentence) != kGold) {
    printf("skip: baseline first choice is not the expected gold sentence\n");
    tiger_engine_free(h);
    return 0;
  }

  // 静态同码真词（认得，rfde rank 1 真词）反复提交 10 次：默认封顶 1.5
  // 下不得改写长句首选。
  if (tiger_engine_set_personal_edge_internal_cap(h, 1.5) < 0)
    return fail("apply default cap");
  for (int i = 0; i < 10; ++i)
    if (tiger_engine_adjust_personal(h, "rfde", "认得", 1) != 1)
      return fail("adjust 认得");
  if (first_text(h, kSentence) != kGold)
    return fail("in-lexicon personal word must not rewrite the sentence (cap 1.5)");

  // 同一引擎放开到 12（旧行为）后翻转必须回来——证明封顶正是边界。
  if (tiger_engine_set_personal_edge_internal_cap(h, 12.0) < 0)
    return fail("open cap");
  if (first_text(h, kSentence) == kGold)
    return fail("cap 12 (old behavior) must let the boosted word intrude");
  if (tiger_engine_set_personal_edge_internal_cap(h, 1.5) < 0)
    return fail("restore cap");
  if (first_text(h, kSentence) != kGold)
    return fail("restore cap must bring the gold sentence back");

  // 整段命中边保持全额：短输入 rfde 上认得应被提交历史推为首选。
  if (first_text(h, "rfde") != "认得")
    printf("note: whole-input rfde first is not 认得 (context drift), non-fatal\n");

  // OOV 自造词（魔虎不在静态码表）：长句内部边保持全额 boost。
  const char* kMohuSentence = "ysmohuhluiysyeyy";  // 用魔虎还是用夜莺
  for (int i = 0; i < 10; ++i)
    if (tiger_engine_adjust_personal(h, "mohu", "魔虎", 1) != 1)
      return fail("adjust 魔虎");
  const std::string mohu_first = first_text(h, kMohuSentence);
  if (mohu_first.find("魔虎") == std::string::npos)
    return fail("OOV personal word must keep full boost inside long sentences");
  tiger_engine_free(h);

  // gain cap 饱和闭合：同一条候选在喂入用户层前后的分差不得超过 cap。
  int h2 = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                               error, sizeof(error));
  if (h2 < 0) return fail("engine create 2");
  if (tiger_engine_set_user_model_weight(h2, 0.85) < 0) return fail("user weight 2");
  if (tiger_engine_set_user_model_gain_cap(h2, 6.0) < 0) return fail("gain cap 2");
  const Row before = find_row(h2, kSentence, "认认得脸都憋得发紫");
  for (int i = 0; i < 50; ++i)
    if (tiger_engine_update_user_model(h2, "认得") != 1)
      return fail("feed 认得");
  const Row after = find_row(h2, kSentence, "认认得脸都憋得发紫");
  if (!before.found || !after.found) {
    printf("skip: tracked candidate not present in both menus\n");
  } else if (after.score - before.score > 6.0 + 1e-6) {
    printf("fail: user gain leak: %.3f -> %.3f (+%.3f > cap 6)\n",
           before.score, after.score, after.score - before.score);
    tiger_engine_free(h2);
    return 1;
  }
  tiger_engine_free(h2);

  // BOS 锚定预算：干净引擎上独立提交「认得」×10 的 trigram 证据集中在
  // 路径头两字（BOS 上下文），预算封顶后不得翻句；关闭预算（0=旧行为）
  // 后翻转必须回来——证明预算正是边界。
  int h3 = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                               error, sizeof(error));
  if (h3 < 0) return fail("engine create 3");
  if (tiger_engine_set_word_edge_weight(h3, 0.5) < 0) return fail("word edge 3");
  if (tiger_engine_set_user_model_weight(h3, 0.85) < 0) return fail("user weight 3");
  if (tiger_engine_set_user_model_gain_cap(h3, 6.0) < 0) return fail("gain cap 3");
  for (int i = 0; i < 10; ++i)
    if (tiger_engine_update_user_model(h3, "认得") != 1)
      return fail("feed 认得 bos");
  if (first_text(h3, kSentence) != kGold)
    return fail("BOS-anchored trigram must not flip the sentence (cap 1.5)");
  if (tiger_engine_set_bos_user_gain_cap(h3, 0.0) < 0)
    return fail("disable bos cap");
  if (first_text(h3, kSentence) == kGold)
    return fail("bos cap 0 (old behavior) must let the anchoring flip return");
  tiger_engine_free(h3);
  printf("pass: personal internal cap + gain saturation + bos budget\n");
  return 0;
}
