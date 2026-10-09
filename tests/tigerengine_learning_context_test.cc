// Context learning must not export standalone word habits to unrelated phrases.
// Uses the installed V5 model; no personal data is read or written.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "tigerengine.h"

#ifndef MOHU_CONTEXT_GUARD_BASELINE
extern "C" int tiger_engine_set_learning_context_guard(int, int);
extern "C" int tiger_engine_context_char_scores_supported(
    int, const char*, const char*, int, double*, int*);
#endif

namespace {
int failures = 0;
void check(bool ok, const char* message) {
  if (!ok) { std::printf("FAIL: %s\n", message); ++failures; }
}
std::string path(const char* env, const char* suffix) {
  const char* value = std::getenv(env);
  if (value && *value) return value;
  const char* home = std::getenv("HOME");
  return std::string(home ? home : "") + suffix;
}
int create(const std::string& model, const std::string& lexicon) {
  char error[512] = {};
  int h = tiger_engine_create(model.c_str(), lexicon.c_str(), 200, 1, error, sizeof(error));
  if (h < 0) { std::printf("FAIL: create: %s\n", error); ++failures; return h; }
  tiger_engine_set_word_edge_weight(h, 0.5);
  tiger_engine_set_text_lexicon_weight(h, 6.5);
  tiger_engine_set_word_form_weight(h, 6.5);
  tiger_engine_set_composed_reading_prior_weight(h, 1.3);
#ifndef MOHU_CONTEXT_GUARD_BASELINE
  check(tiger_engine_set_learning_context_guard(h, 1) == 1, "enable guard");
  check(tiger_engine_set_learning_context_guard(h, 1) == 0, "guard toggle idempotent");
#endif
  return h;
}
std::string first(int h, const char* raw) {
  char out[1 << 20] = {};
  if (tiger_decode_full(h, raw, 0, out, sizeof(out)) <= 0) return {};
  const char* start = std::strchr(out, '\n');
  if (!start) return {};
  const char* end = std::strchr(++start, '\t');
  return end ? std::string(start, end - start) : "";
}
bool personal(int h, const char* raw, const std::string& text) {
  char out[1 << 20] = {};
  if (tiger_decode_full(h, raw, 0, out, sizeof(out)) <= 0) return true;
  const std::string output(out);
  const auto start = output.find("\n" + text + "\t");
  if (start == std::string::npos) return true;
  const auto end = output.find('\n', start + 1);
  const auto line = output.substr(start + 1, end - start - 1);
  return line.substr(line.rfind('\t') + 1) == "1";
}

std::vector<double> scores(int h, const char* context, const char* joined, int n) {
  std::vector<double> out(n);
  check(tiger_engine_context_char_scores(h, context, joined, n, out.data()) == n,
        "score candidates");
  return out;
}
void learn(int h, const char* text, int count) {
  for (int i = 0; i < count; ++i)
    check(tiger_engine_update_user_model(h, text) == 1, "observe text");
}
}

int main() {
  const std::string model = path("TIGER_NGRAM", "/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin");
  const std::string lexicon = path("TIGER_LEXICON", "/Library/Rime/mohu/data/zrm/mohu_zrm.lexicon.txt");
  for (const auto& value : {model, lexicon}) {
    FILE* f = std::fopen(value.c_str(), "rb");
    if (!f) { std::printf("skip: %s not found\n", value.c_str()); return 0; }
    std::fclose(f);
  }
  int h = create(model, lexicon);
  if (h < 0) return 1;
  auto before = scores(h, "编辑", "码表\n马标", 2);
  const auto cold_editor = first(h, "bmjimabc");
  check(!cold_editor.empty(), "cold whole sentence has candidates");
  learn(h, "马标", 30);
  learn(h, "编辑", 5);
  auto after = scores(h, "编辑", "码表\n马标", 2);
  check(std::abs((after[1] - after[0]) - (before[1] - before[0])) < 1e-6,
        "standalone 马标 must not bias unseen 编辑+码表/马标 context");
  check(first(h, "bmjimabc") == cold_editor, "standalone 马标 must not rewrite the cold sentence");
  check(tiger_engine_adjust_personal(h, "mabc", "马标", 10) == 1, "learn known injected word");
  check(first(h, "bmjimabc") == cold_editor, "known injected word edge must not leak into long input");
  check(tiger_engine_adjust_personal(h, "bmji", "编辑", 10) == 1, "learn common prefix word");
  check(!personal(h, "bmjimabc", "编辑马表"),
        "suppressed prefix learning must not mark unrelated siblings personal");
#ifndef MOHU_CONTEXT_GUARD_BASELINE
  double s[3] = {}; int support[3] = {};
  check(tiger_engine_context_char_scores_supported(h, "编辑", "码表\n马标\n马表", 3, s, support) == 3,
        "supported-score ABI batch");
  check(support[0] == 0 && support[1] == 0 && support[2] == 0,
        "编辑马 alone cannot prove 编辑马标/马表 collocation");
#endif
  // An actual complete-context observation must still influence the next word.
  auto context_before = scores(h, "编辑", "码表\n马标", 2);
  learn(h, "编辑码表", 15);
  auto context_after = scores(h, "编辑", "码表\n马标", 2);
  check(context_after[0] - context_after[1] > context_before[0] - context_before[1],
        "observed 编辑码表 must retain contextual learning");
#ifndef MOHU_CONTEXT_GUARD_BASELINE
  check(tiger_engine_context_char_scores_supported(h, "编辑", "码表\n马标", 2, s, support) == 2 &&
        support[0] == 1 && support[1] == 0, "real personal boundary evidence enables promotion");
  check(tiger_engine_context_char_scores_supported(h, "我想吃", "自助\n自主", 2, s, support) == 2 &&
        support[0] == 1, "real static collocation retains support");
  check(tiger_engine_context_char_scores_supported(h, "", "码表\n马标", 2, s, support) == 2 &&
        support[0] == 0 && support[1] == 0, "empty context has no boundary support");
  check(tiger_engine_context_char_scores_supported(h, "编辑", "码表", 2, s, support) == -1,
        "short candidate batch rejected");
  check(tiger_engine_context_char_scores_supported(-1, "编辑", "码表", 1, s, support) == -1,
        "bad handle rejected");
  check(tiger_engine_context_char_scores_supported(h, "编辑", "码表", 1, s, nullptr) == -1,
        "missing support output rejected");
  check(tiger_engine_set_learning_context_guard(h, 2) == -1, "invalid guard rejected");
#endif
  tiger_engine_free(h);

  h = create(model, lexicon);
  if (h < 0) return 1;
  check(first(h, "fzjnqmxnwjku") == "费尽千辛万苦", "cold whole sentence 费尽千辛万苦");
  learn(h, "费劲", 30);
  check(first(h, "fzjnqmxnwjku") == "费尽千辛万苦", "standalone 费劲 must not override following 千辛万苦");
  check(tiger_engine_adjust_personal(h, "fzjn", "费劲", 10) == 1, "learn existing word 费劲");
  check(first(h, "fzjnqmxnwjku") == "费尽千辛万苦", "internal 费劲 personal edge requires real context");
  check(first(h, "fzjn") == "费劲", "whole-input personal word remains usable");
  check(first(h, "fzjnxnsi") == "费尽心思", "other true collocation remains correct");
  // Explicitly selecting a complete phrase still overrides the automatic choice.
  check(tiger_engine_adjust_personal(h, "fzjnqmxnwjku", "费劲千辛万苦", 10) == 1,
        "whole-input phrase learning");
  check(first(h, "fzjnqmxnwjku") == "费劲千辛万苦", "explicit whole-phrase selection remains authoritative");
  tiger_engine_free(h);

  h = create(model, lexicon);
  if (h < 0) return 1;
  learn(h, "费劲猫", 10);
  check(tiger_engine_adjust_personal(h, "fzjnmc", "费劲猫", 10) == 1,
        "learn new word for incremental regression");
  char incremental[1 << 20] = {}, complete[1 << 20] = {};
  check(tiger_decode(h, "fzjnmc", 0, incremental, sizeof(incremental), nullptr) > 0,
        "decode learned prefix");
  check(tiger_decode(h, "fzjnmccj", 0, incremental, sizeof(incremental), nullptr) > 0,
        "append to learned prefix");
  check(tiger_decode_full(h, "fzjnmccj", 0, complete, sizeof(complete)) > 0,
        "decode complete input");
  check(std::strcmp(incremental, complete) == 0,
        "incremental and full decode agree on scores, confidence and personal flags");
  tiger_engine_free(h);
  std::printf("context learning: %d failures\n", failures);
  return failures ? 1 : 0;
}
