// 词证据分歧门引擎测试：top1 接不成词拼装而 top2 接词典多字词 → 1；
// 两者皆词/皆非词/无分歧段 → 0。模型或词表缺失时打印 skip 并通过；
// TIGER_NGRAM / TIGER_LEXICON 可覆盖路径。
#include <cstdlib>
#include <cstdio>
#include <string>

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

  char error[512] = {};
  int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1,
                              error, sizeof(error));
  if (h < 0) {
    printf("fail: engine create: %s\n", error);
    return 1;
  }

  struct Case {
    const char* a;
    const char* b;
    int want;
    const char* label;
  };
  const Case cases[] = {
      // 只吃 已入 base 词典（权重 1319），词表注入（2026-09-20 方案 B）
      // 后分歧段有成词覆盖，词证据与排序不再抵触 → 正确不开门。
      // （历史上只吃不在码表、按非词开门；注入后此用例语义反转。）
      {"这个输入法只吃一口气输入一整句话", "这个输入法支持一口气输入一整句话", 0,
       "只吃(top1已入词典) 不开门"},
      // 支吃 仍是非词拼装，但三字词全码注入（2026-09-23）后分歧码点
      // 「吃」的 2–4 字窗口含词典词「吃一口」——按「分歧码点被词典词
      // 覆盖」的定义语义反转为不开门（同 只吃 入典先例）。开门路径由
      // 句哗/句话 用例保留。
      {"这个输入法支吃一口气输入一整句话", "这个输入法支持一口气输入一整句话", 0,
       "支吃(分歧点入词窗口「吃一口」) 不开门"},
      // 句哗 是真·非词拼装且句尾无 2–4 字窗口成词（句哗 不在典），
      // top2 分歧码点属词典词「句话」→ 开门。
      {"这个输入法支持一口气输入一整句哗", "这个输入法支持一口气输入一整句话", 1,
       "句哗(top1非词)/句话(top2词) 开门"},
      // 反序：top1=支持（词）→ 不开门
      {"这个输入法支持一口气输入一整句话", "这个输入法只吃一口气输入一整句话", 0,
       "支持(top1词) 不开门"},
      // 暴利 已入 base 词典（1519）→ top1=暴利 亦有成词覆盖，不开门
      // （历史上按非词开门，注入后语义反转）。
      {"你觉得什么是暴力行业", "你觉得什么是暴利行业", 0, "暴力(top1词) 不开门"},
      {"你觉得什么是暴利行业", "你觉得什么是暴力行业", 0, "暴利(top1已入词典) 不开门"},
      // 无分歧（前缀耗尽其一）
      {"支持", "支持", 0, "相同候选 不开门"},
  };
  bool ok = true;
  for (const Case& c : cases) {
    std::string joined = std::string(c.a) + "\n" + c.b;
    int flag = -1;
    if (tiger_engine_word_disagreement(h, joined.c_str(), 2, &flag) != 0 ||
        flag != c.want) {
      printf("fail: %s => got %d want %d\n", c.label, flag, c.want);
      ok = false;
    }
  }
  tiger_engine_free(h);
  if (!ok) return 1;
  printf("ok: word disagreement gate classification\n");
  return 0;
}
