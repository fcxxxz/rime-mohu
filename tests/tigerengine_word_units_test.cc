// Deterministic word-boundary/context tests, independent of installed models.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>
#include "tigerengine.h"

namespace {
template <typename T>
void append(std::vector<uint8_t>& data, T value) {
  const size_t offset = data.size();
  data.resize(offset + sizeof(value));
  std::memcpy(data.data() + offset, &value, sizeof(value));
}

template <typename T>
void put(std::vector<uint8_t>& data, size_t offset, T value) {
  std::memcpy(data.data() + offset, &value, sizeof(value));
}

void context(std::vector<uint8_t>& data, uint64_t key, uint32_t target) {
  append(data, key);
  append(data, 0.1f);
  append(data, uint32_t{1});
  append(data, target);
  append(data, 0.8f);
}

std::vector<uint8_t> model(bool alternate_ids = false) {
  std::vector<std::string> words = {
    "<s>", "</s>", "现在", "在", "句", "剧", "输入法", "编辑",
    "词典", "直接", "无敌", "整"
  };
  if (alternate_ids) {
    std::swap(words[2], words[3]);
    std::swap(words[6], words[8]);
  }
  std::vector<uint8_t> data(120, 0);
  std::memcpy(data.data(), "MHKNM01", 7);
  put(data, 8, uint32_t{1});
  put(data, 12, uint32_t{120});
  put(data, 24, static_cast<uint32_t>(words.size()));
  put(data, 28, uint32_t{1});
  put(data, 32, uint32_t{64});
  put(data, 40, uint64_t{120});
  for (const auto& word : words) {
    append(data, static_cast<uint32_t>(word.size()));
    data.insert(data.end(), word.begin(), word.end());
  }
  put(data, 48, static_cast<uint64_t>(data.size()));
  for (size_t i = 0; i < words.size(); ++i)
    append(data, i == 5 ? 0.3f : i == 4 ? 0.1f : 0.01f);
  const uint64_t bi = data.size();
  put(data, 56, bi);
  context(data, 2, 4);  // 现在 favors 句; 在 has no such evidence.
  put(data, 64, static_cast<uint64_t>(data.size()));
  append(data, uint64_t{2});
  append(data, bi);
  const uint64_t tri = data.size();
  put(data, 72, tri);
  context(data, (uint64_t{6} << 32) | 7, 4);  // 输入法 | 编辑 -> 句
  context(data, (uint64_t{8} << 32) | 7, 5);  // 词典 | 编辑 -> 剧
  append(data, (uint64_t{6} << 32) | 7);
  append(data, tri);
  put(data, 88, uint32_t{1});
  put(data, 92, uint32_t{2});
  put(data, 96, uint32_t{1});
  put(data, 100, uint32_t{1});
  put(data, 80, static_cast<uint64_t>(data.size()));
  for (size_t i = 0; i < words.size(); ++i) {
    append(data, uint8_t{0});
    append(data, 1.0f);
  }
  put(data, 16, static_cast<uint64_t>(data.size()));
  return data;
}

std::string decode(int handle, const char* raw, bool full = true) {
  char output[65536] = {};
  const int count = full ? tiger_decode_full(handle, raw, 0, output, sizeof(output)) :
    tiger_decode(handle, raw, 0, output, sizeof(output), nullptr);
  if (count <= 0) return {};
  return output;
}

bool check(bool value, const char* message) {
  if (!value) std::printf("fail: %s\n", message);
  return value;
}
}  // namespace

int main() {
  const auto root = std::filesystem::temp_directory_path() /
    ("tiger-word-units-" + std::to_string(static_cast<long long>(
      std::filesystem::file_time_type::clock::now().time_since_epoch().count())));
  std::filesystem::create_directory(root);
  const auto model_path = root / "model.bin";
  const auto lexicon_path = root / "lexicon.txt";
  const auto bytes = model();
  {
    std::ofstream file(model_path, std::ios::binary);
    file.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    std::ofstream lexicon(lexicon_path);
    lexicon << "xmzl\t现在\t1\t100\nvgx\t整\t1\t100\n"
      "ju\t句\t1\t100\nju\t剧\t1\t100\n"
      "vijx\t直接\t1\t100\nwudi\t无敌\t1\t100\n"
      "xz\t现在\t1\t100\nvj\t直接\t2\t100\n"
      "vij\t直接\t1\t100\nwd\t无敌\t99\t100\n"
      "viwj\t直接\t99\t100\n";
  }
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.string().c_str(),
    lexicon_path.string().c_str(), 200, 1, error, sizeof(error));
  bool ok = check(handle >= 0, error);
  if (handle >= 0) {
    const auto complete = decode(handle, "xmzlvgxjuvijxwudi");
    ok &= check(complete.find("现在整句直接无敌\txmzl vgx ju vijx wudi\t") !=
      std::string::npos, "full dictionary words must remain sentence edges");
    for (const char* raw : {"xmzl", "xmzlvgx", "xmzlvgxju", "xmzlvgxjuvijxwudi", "xmzlvgxju"}) {
      const auto incremental = decode(handle, raw, false);
      ok &= check(incremental == decode(handle, raw),
        "word-unit incremental append/shrink matches a fresh decode");
    }
    ok &= check(decode(handle, "xzju").empty(), "disabled abbreviated edges stay disabled");
    ok &= check(decode(handle, "viwjju").empty(), "injected rank-99 words stay terminal-only");
    tiger_engine_set_abbrev_edges(handle, 1);
    tiger_engine_set_abbrev_strict(handle, 1);
    ok &= check(!decode(handle, "xzju").empty(), "enabled exact abbreviation is reachable");
    ok &= check(decode(handle, "vjju").empty(), "abbreviation rank limit is respected");
    ok &= check(decode(handle, "vijju").empty(), "strict abbreviation length is respected");
    tiger_engine_set_abbrev_edges(handle, 0);

    const auto baseline = decode(handle, "ju", false);
    ok &= check(baseline.find("剧\t") < baseline.find("句\t"), "fixture baseline prefers 剧");
    ok &= check(tiger_engine_set_decode_context(handle, "现在", 2) == 1,
      "word context is applied");
    const auto now = decode(handle, "ju", false);
    ok &= check(now.find("句\t") < now.find("剧\t"), "decode must condition on whole 现在");
    ok &= check(tiger_engine_set_decode_context(handle, "现在", 2) == 0,
      "identical word context is a no-op");
    tiger_engine_set_decode_context(handle, "在", 2);
    ok &= check(decode(handle, "ju", false) == baseline, "现在 is not interchangeable with 在");
    tiger_engine_set_decode_context(handle, "输入法编辑", 2);
    const auto editor = decode(handle, "ju", false);
    ok &= check(editor.find("句\t") < editor.find("剧\t"), "both complete context words are scored");
    ok &= check(tiger_engine_set_decode_context(handle, "词典编辑", 2) == 1,
      "same last two characters but different words invalidate decode cache");
    const auto dictionary = decode(handle, "ju", false);
    ok &= check(dictionary.find("剧\t") < dictionary.find("句\t"), "previous word changes ordering");
    tiger_engine_set_decode_context(handle, "输入法编辑", 1);
    ok &= check(decode(handle, "ju", false) == baseline, "one-word context excludes the previous word");
    ok &= check(tiger_engine_set_decode_context(handle, "", 2) == 1,
      "empty context clears word state");
    ok &= check(decode(handle, "ju", false) == baseline, "clear restores baseline byte-for-byte");
    ok &= check(tiger_engine_set_decode_context(handle, "abc123", 2) == 0,
      "CJK-free context stays cleared");
    const auto scorer_path = root / "scorer.bin";
    const auto scorer_bytes = model(true);
    {
      std::ofstream file(scorer_path, std::ios::binary);
      file.write(reinterpret_cast<const char*>(scorer_bytes.data()), scorer_bytes.size());
    }
    ok &= check(tiger_engine_load_word_scorer(handle, scorer_path.string().c_str()) == 0,
      "independent scorer with different word IDs loads");
    double scores[2] = {};
    tiger_engine_context_word_scores(handle, "现在", "句\n剧", 2, 2, scores);
    ok &= check(scores[0] < scores[1], "independent scorer uses its own vocabulary IDs");
    tiger_engine_set_decode_context(handle, "现在", 2);
    ok &= check(decode(handle, "ju", false) == now,
      "independent scorer must not corrupt the primary decode word IDs");
    tiger_engine_context_word_scores(handle, "现在", "句\n剧", 2, 2, scores);
    ok &= check(scores[0] < scores[1], "context cache remains model-specific after decoding");
    tiger_engine_set_decode_context(handle, "输入法编辑", 2);
    ok &= check(decode(handle, "ju", false) == editor,
      "two-word decode context stays in the primary vocabulary");
    tiger_engine_free(handle);
  }
  std::filesystem::remove_all(root);
  if (ok) std::printf("pass: word units, two-word decode context, abbreviation guards\n");
  return ok ? 0 : 1;
}
