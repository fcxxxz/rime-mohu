// 词级上下文候选评分（跨候选调频）引擎测试：真实模型上验证
// tiger_engine_load_word_scorer / tiger_engine_context_word_scores 的
// 可用性语义（无 scorer 报错、坏路径不伤引擎）、方向性（上文抬升续写
// 概率更高的候选）、OOV=-20、批量计数、重复调用确定性、行数不足报错，
// 以及 MHCTN01 单文件容器词层（word_scorer=packed）与显式加载评分一致。
// 模型缺失时打印 skip 并通过；TIGER_NGRAM / TIGER_WORD_NGRAM /
// TIGER_LEXICON / TIGER_MERGED 可覆盖路径（TIGER_MERGED 缺省时只跳过
// 容器分支）。
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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

bool file_exists(const std::string& path) {
  FILE* probe = fopen(path.c_str(), "rb");
  if (!probe) return false;
  fclose(probe);
  return true;
}

// '\n' 连接候选后调用批量评分；返回 C ABI 返回值。
int word_scores(int handle, const char* context,
                const std::vector<std::string>& cands, int window_words,
                double* out) {
  std::string joined;
  for (size_t i = 0; i < cands.size(); ++i) {
    if (i) joined += '\n';
    joined += cands[i];
  }
  return tiger_engine_context_word_scores(handle, context, joined.c_str(),
                                          (int)cands.size(), window_words,
                                          out);
}

bool status_contains(int handle, const char* needle) {
  char status[2048] = {0};
  if (tiger_status(handle, status, sizeof(status)) != 0) return false;
  return strstr(status, needle) != nullptr;
}

// status 中 key= 后的数值（key 形如 "word_vocab="），缺失返回 -1。
long status_number(int handle, const char* key) {
  char status[2048] = {0};
  if (tiger_status(handle, status, sizeof(status)) != 0) return -1;
  const char* p = strstr(status, key);
  if (!p) return -1;
  return atol(p + strlen(key));
}

// decode 输出首个候选的文本（首行 flags 之后第一行的第一列）。
bool decode_first_text(int handle, const char* raw, std::string* out) {
  static char buf[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, buf, sizeof(buf), nullptr);
  if (rc <= 0) return false;
  const char* p = strchr(buf, '\n');
  if (!p) return false;
  p += 1;
  const char* e = strchr(p, '\t');
  if (!e) e = p + strlen(p);
  if (e <= p) return false;
  out->assign(p, (size_t)(e - p));
  return true;
}

}  // namespace

int main() {
  const std::string model = env_or("TIGER_NGRAM",
      home_path("/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin"));
  const std::string word_model = env_or("TIGER_WORD_NGRAM",
      "research/lm_sentence_compare/trainwork/mohu_lm_train_copy/mohu-word-kn4.bin");
  const std::string lexicon = env_or("TIGER_LEXICON",
      "tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt");
  const std::string merged = env_or("TIGER_MERGED",
      "/tmp/mohu-sentence-ngram-v6-merged.bin");
  for (const std::string* path : {&model, &word_model, &lexicon}) {
    if (!file_exists(*path)) {
      printf("skip: %s not found\n", path->c_str());
      return 0;
    }
  }

  char err[512] = {0};
  int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                              err, sizeof(err));
  if (h < 0) {
    printf("fail: create %s\n", err);
    return 1;
  }

  // 1) 未加载 scorer：status 报 off，评分接口必须报错而不是给假分。
  double s[4] = {0};
  if (!status_contains(h, "word_scorer=off")) {
    printf("fail: fresh engine must report word_scorer=off\n");
    return 1;
  }
  if (!status_contains(h, "word_vocab=")) {
    printf("fail: status must expose word_vocab\n");
    return 1;
  }
  if (word_scores(h, "我想吃", {"自助", "自主"}, 2, s) != -1) {
    printf("fail: context_word_scores without a scorer must return -1\n");
    return 1;
  }

  // 4) 坏句柄：load 与评分都报 -1。
  if (tiger_engine_load_word_scorer(-1, word_model.c_str()) != -1) {
    printf("fail: load_word_scorer on bad handle must return -1\n");
    return 1;
  }
  if (tiger_engine_context_word_scores(12345, "我想吃", "自助", 1, 2, s) != -1) {
    printf("fail: context_word_scores on bad handle must return -1\n");
    return 1;
  }
  // 4) 坏路径：load 失败返回 -1，引擎本体必须存活（decode 照常）。
  if (tiger_engine_load_word_scorer(h, "/nonexistent/mohu-word.bin") != -1) {
    printf("fail: load_word_scorer on bad path must return -1\n");
    return 1;
  }
  if (tiger_engine_load_word_scorer(h, nullptr) != -1) {
    printf("fail: load_word_scorer without a path must return -1\n");
    return 1;
  }
  if (!status_contains(h, "word_scorer=off")) {
    printf("fail: failed load must leave the engine without a word scorer\n");
    return 1;
  }
  {
    static char out[1 << 22];
    if (tiger_decode(h, "nihkma", 0, out, sizeof(out), nullptr) <= 0) {
      printf("fail: engine must stay decodable after a bad scorer load\n");
      return 1;
    }
  }

  // 2) 显式加载成功：status 转 explicit，词表可见。
  if (tiger_engine_load_word_scorer(h, word_model.c_str()) != 0) {
    printf("fail: load_word_scorer %s: %s\n", word_model.c_str(),
           tiger_last_error());
    return 1;
  }
  if (!status_contains(h, "word_scorer=explicit")) {
    printf("fail: status must report word_scorer=explicit after load\n");
    return 1;
  }
  if (status_number(h, "word_vocab=") <= 0) {
    printf("fail: explicit scorer must expose a non-empty word_vocab\n");
    return 1;
  }

  // 2) 方向性：上文应抬升续写概率更高的候选（logP 数值更大）。
  if (word_scores(h, "我想吃", {"自助", "自主"}, 2, s) != 2) {
    printf("fail: batch of 2 must report 2\n");
    return 1;
  }
  printf("我想吃 自助=%.3f 自主=%.3f\n", s[0], s[1]);
  if (!(s[0] > s[1])) {
    printf("fail: 我想吃 must prefer 自助 over 自主\n");
    return 1;
  }
  if (word_scores(h, "和谷歌", {"助理", "主力"}, 2, s) != 2) {
    printf("fail: batch of 2 must report 2\n");
    return 1;
  }
  printf("和谷歌 助理=%.3f 主力=%.3f\n", s[0], s[1]);
  if (!(s[0] > s[1])) {
    printf("fail: 和谷歌 must prefer 助理 over 主力\n");
    return 1;
  }
  if (word_scores(h, "汽车保养只更换", {"机油", "既有"}, 2, s) != 2) {
    printf("fail: batch of 2 must report 2\n");
    return 1;
  }
  printf("汽车保养只更换 机油=%.3f 既有=%.3f\n", s[0], s[1]);
  if (!(s[0] > s[1])) {
    printf("fail: 汽车保养只更换 must prefer 机油 over 既有\n");
    return 1;
  }

  // 3) OOV=-20、批量计数、重复调用确定性（含切换上文后切回）。
  const std::vector<std::string> trio = {"自助", "自主", "龘龘靐"};
  double first[3] = {0};
  if (word_scores(h, "我想吃", trio, 2, first) != 3) {
    printf("fail: batch of 3 must report 3\n");
    return 1;
  }
  if (first[2] != -20.0) {
    printf("fail: OOV candidate must score exactly -20 (got %f)\n", first[2]);
    return 1;
  }
  double again[3] = {0};
  if (word_scores(h, "我想吃", trio, 2, again) != 3 ||
      memcmp(first, again, sizeof(first)) != 0) {
    printf("fail: repeated identical calls must be bit-identical\n");
    return 1;
  }
  {
    double other[2] = {0};
    if (word_scores(h, "和谷歌", {"助理", "主力"}, 2, other) != 2) {
      printf("fail: context switch batch failed\n");
      return 1;
    }
    double back[3] = {0};
    if (word_scores(h, "我想吃", trio, 2, back) != 3 ||
        memcmp(first, back, sizeof(first)) != 0) {
      printf("fail: scores must be deterministic after a context switch\n");
      return 1;
    }
  }

  // 6) candidates 行数少于 candidate_count：必须报 -1。
  if (tiger_engine_context_word_scores(h, "我想吃", "自助\n自主", 3, 2, s) != -1) {
    printf("fail: fewer candidate lines than count must return -1\n");
    return 1;
  }
  // 末行允许不带换行符（恰好 count 行）。
  if (tiger_engine_context_word_scores(h, "我想吃", "自助\n自主\n机油", 3, 2,
                                       s) != 3) {
    printf("fail: trailing newline is optional for the last candidate\n");
    return 1;
  }

  // 5) MHCTN01 容器：word_scorer=packed、评分与显式加载一致、解码首候选
  //    与纯字符模型一致（词层不得改变解码路径）。
  if (file_exists(merged)) {
    char merged_err[512] = {0};
    const int hm = tiger_engine_create(merged.c_str(), lexicon.c_str(), 200, 1,
                                       merged_err, sizeof(merged_err));
    if (hm < 0) {
      printf("fail: create merged container %s: %s\n", merged.c_str(),
             merged_err);
      return 1;
    }
    if (!status_contains(hm, "word_scorer=packed")) {
      printf("fail: container status must report word_scorer=packed\n");
      return 1;
    }
    if (status_number(hm, "word_vocab=") <= 0) {
      printf("fail: container must expose a non-empty word_vocab\n");
      return 1;
    }
    double packed[2] = {0};
    if (word_scores(hm, "我想吃", {"自助", "自主"}, 2, packed) != 2) {
      printf("fail: container word scoring batch failed\n");
      return 1;
    }
    double explicit_scores[2] = {0};
    if (word_scores(h, "我想吃", {"自助", "自主"}, 2, explicit_scores) != 2) {
      printf("fail: explicit word scoring batch failed\n");
      return 1;
    }
    printf("packed 自助=%.3f 自主=%.3f (explicit %.3f/%.3f)\n", packed[0],
           packed[1], explicit_scores[0], explicit_scores[1]);
    if (std::fabs(packed[0] - explicit_scores[0]) >= 1e-6 ||
        std::fabs(packed[1] - explicit_scores[1]) >= 1e-6) {
      printf("fail: container word scores must match the explicit scorer\n");
      return 1;
    }
    std::string merged_first, char_first;
    if (!decode_first_text(hm, "nihkma", &merged_first) ||
        !decode_first_text(h, "nihkma", &char_first)) {
      printf("fail: decode on both engines must produce candidates\n");
      return 1;
    }
    if (merged_first != char_first) {
      printf("fail: container decode first candidate (%s) must equal the "
             "char model's (%s)\n", merged_first.c_str(), char_first.c_str());
      return 1;
    }
    tiger_engine_free(hm);
  } else {
    printf("skip: %s not found (container branch)\n", merged.c_str());
  }

  tiger_engine_free(h);
  printf("pass: word scorer context scoring\n");
  return 0;
}
