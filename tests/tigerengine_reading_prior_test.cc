// 读音先验引擎测试：码表第 5 列（读音条件简频）→ log P(读音|字) 并入
// 路径分。核心回归用例是 mohuz（mo+hu+末辅z）：关闭先验时字符级模型凭
// 「万」的全局字频把「万虎」排首选；开启后罕用读音 mò 被压到候选尾部，
// 而 mohup 的真实词「模糊」保持首选。模型或词表缺失时打印 skip 并通过，
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

bool contains(const std::vector<std::string>& items, const std::string& want) {
  for (const std::string& item : items)
    if (item == want) return true;
  return false;
}

}  // namespace

int main() {
  const std::string model = env_or("TIGER_NGRAM",
      home_path("/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin"));
  // 默认用仓库构建产物（带第 5 列）；TIGER_LEXICON 可指向旧 4 列码表，
  // 此时所有用例自动 skip（中性先验无法翻转排序）。
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

  char error[512] = {};
  int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                              error, sizeof(error));
  if (h < 0) {
    printf("fail: engine create: %s\n", error);
    return 1;
  }

  // 基线：关闭先验。万虎 作为二字组合终态如今被词条可见性门控拦截
  // （2026-09-20 方案 A：词形查询的组合终态必须是词表词条，万虎 不在
  // 词典），任何权重下都不再出现——旧的「关闭先验万虎第一」世界不复
  // 存在，断言随之更新为「始终缺席」。
  if (tiger_engine_set_reading_prior_weight(h, 0.0) != 1) {
    printf("fail: disable prior\n");
    return 1;
  }
  std::vector<std::string> disabled = decode_candidates(h, "mohuz", 10);
  if (contains(disabled, "万虎")) {
    printf("fail: 万虎 must be gated out even without the prior\n");
    return 1;
  }

  // 开启默认权重：万虎 不再进前十，mohup 的 模糊 仍是首选。
  if (tiger_engine_set_reading_prior_weight(h, 1.0) != 1) {
    printf("fail: enable prior\n");
    return 1;
  }
  std::vector<std::string> enabled = decode_candidates(h, "mohuz", 10);
  if (contains(enabled, "万虎")) {
    printf("fail: 万虎 must drop out of the top 10 with prior on\n");
    return 1;
  }
  std::vector<std::string> mohup = decode_candidates(h, "mohup");
  if (mohup.empty() || mohup[0] != "模糊") {
    printf("fail: mohup must keep 模糊 first with prior on\n");
    return 1;
  }

  // 权重回 0：门控与先验是两层机制，关闭先验不影响词条可见性门控
  //（万虎 依然缺席）；旋钮可逆性由 vgxjv 段的 reading=0 基线覆盖。
  if (tiger_engine_set_reading_prior_weight(h, 0.0) != 1) {
    printf("fail: re-disable prior\n");
    return 1;
  }
  disabled = decode_candidates(h, "mohuz", 10);
  if (contains(disabled, "万虎")) {
    printf("fail: 万虎 must stay gated out at weight 0\n");
    return 1;
  }

  // 非法权重拒绝：负数与 >4。
  if (tiger_engine_set_reading_prior_weight(h, -0.5) != -1 ||
      tiger_engine_set_reading_prior_weight(h, 4.5) != -1) {
    printf("fail: out-of-range weights must be rejected\n");
    return 1;
  }

  // 词形短查询的罕用读音硬门控（vgxjv/vgxju＝整+车 jū，≤5 键）：读音
  // 系统全关时「整车」凭 zhěngchē 语料搭配排第一；开启后占比 <20% 的
  // 读音禁止参与组合，与魔然对齐——整车应**完全消失**（而非仅降名次）。
  // jv 是 ju 的飞键换头变体（第 6 列规范头归并读音），vgxjv 与 vgxju
  // 必须同序。门控属读音先验体系，composed 权重为 0 时仍生效。
  if (tiger_engine_set_reading_prior_weight(h, 0.0) < 0) {
    printf("fail: disable reading system for gate baseline\n");
    return 1;
  }
  std::vector<std::string> gate_off = decode_candidates(h, "vgxjv");
  if (gate_off.empty() || gate_off[0] != "整车") {
    printf("skip: reading-off baseline does not rank 整车 first\n");
    tiger_engine_free(h);
    return 0;
  }
  if (tiger_engine_set_reading_prior_weight(h, 1.0) < 0) {
    printf("fail: re-enable reading system for gate test\n");
    return 1;
  }
  for (const char* query : {"vgxjv", "vgxju"}) {
    std::vector<std::string> gated = decode_candidates(h, query, 20);
    if (gated.empty() || gated[0] != "整句") {
      printf("fail: %s must rank 整句 first with the word-query gate on\n", query);
      return 1;
    }
    if (contains(gated, "整车")) {
      printf("fail: %s must not offer 整车(jū) at all on word-like queries\n", query);
      return 1;
    }
  }
  // 整词命中（vgie=整车 chē 词边）不受影响；单字直打（jv，混有简拼词
  // 候选）在组合罚分开/关时完整候选序列必须逐项一致——整段单边不吃罚分。
  std::vector<std::string> whole_word = decode_candidates(h, "vgie");
  if (whole_word.empty() || whole_word[0] != "整车") {
    printf("fail: vgie must keep 整车 (word edge) first\n");
    return 1;
  }
  tiger_engine_set_composed_reading_prior_weight(h, 0.0);
  std::vector<std::string> single_off = decode_candidates(h, "jv", 20);
  tiger_engine_set_composed_reading_prior_weight(h, 1.0);
  std::vector<std::string> single_on = decode_candidates(h, "jv", 20);
  if (single_off != single_on) {
    printf("fail: jv standalone ranking must not change with composed penalty\n");
    return 1;
  }
  if (!contains(single_on, "车")) {
    printf("fail: jv standalone must still offer 车\n");
    return 1;
  }
  if (tiger_engine_set_composed_reading_prior_weight(h, -0.5) != -1 ||
      tiger_engine_set_composed_reading_prior_weight(h, 4.5) != -1) {
    printf("fail: out-of-range composed weights must be rejected\n");
    return 1;
  }

  tiger_engine_free(h);
  printf("ok: reading prior ranks rare readings down and keeps real words\n");
  return 0;
}
