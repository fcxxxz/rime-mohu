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

// 与 decode_candidates 同游标，但保留整行（text\tsegmented\tscore\t…）。
std::vector<std::string> decode_lines(int handle, const char* raw,
                                      size_t limit = 10) {
  static char out[1 << 22];
  const int rc = tiger_decode(handle, raw, 0, out, sizeof(out), nullptr);
  if (rc <= 0) return {};
  std::vector<std::string> lines;
  const char* p = out;
  const char* line_end = strchr(p, '\n');
  if (!line_end) return {};
  p = line_end + 1;
  while (*p && lines.size() < limit) {
    line_end = strchr(p, '\n');
    if (!line_end) line_end = p + strlen(p);
    lines.emplace_back(p, line_end - p);
    if (!*line_end) break;
    p = line_end + 1;
  }
  return lines;
}

// 取候选行第三列（score）；未命中返回 -1e9 哨兵。
double score_of(const std::vector<std::string>& lines, const std::string& text) {
  for (const std::string& line : lines) {
    if (line.compare(0, text.size(), text) != 0) continue;
    if (line.size() <= text.size() || line[text.size()] != '\t') continue;
    const char* segmented = line.c_str() + text.size() + 1;
    const char* tab = strchr(segmented, '\t');
    if (!tab) break;
    return strtod(tab + 1, nullptr);
  }
  return -1e9;
}

// pathmap 恰好一条边（root+终点两个边界、一个逗号）判定整段命中边。
bool has_single_edge(const std::vector<std::string>& lines, const std::string& text) {
  for (const std::string& line : lines) {
    if (line.compare(0, text.size(), text) != 0) continue;
    if (line.size() <= text.size() || line[text.size()] != '\t') continue;
    const size_t last = line.rfind('\t');
    if (last == std::string::npos) break;
    const size_t prev = line.rfind('\t', last - 1);
    if (prev == std::string::npos) break;
    const std::string map = line.substr(prev + 1, last - prev - 1);
    return map.find(',') != std::string::npos &&
           map.find(',', map.find(',') + 1) == std::string::npos;
  }
  return false;
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

  // 个人词整段命中先验回归：用户词整段命中边获得与静态词边同权的先验，
  // 经由共享个人子边（请+跟打 搭乘「跟打」提交 boost）的组合路径不享受
  // 先验——否则先验同样被组合搭乘、两边抵消。线上场景（qygfda，晴跟打
  // c=6 vs 跟打 c=9）旧逻辑 boost 封顶 12 也压不过组合；这里断言机制
  // 本身：整词个人边恰好 +weight、组合分数逐字节不动，不依赖边缘名次。
  const std::string kTaught = "晴跟打";
  const std::string kFreeRide = "请跟打";
  const char* kPersonalRows = "qygfda\t晴跟打\t12\ngfda\t跟打\t9\n";
  if (tiger_engine_set_personal_lexicon(h, kPersonalRows) != 0) {
    printf("fail: apply personal rows\n");
    return 1;
  }
  std::vector<std::string> taught_off = decode_lines(h, "qygfda", 8);
  double taught_score_off = score_of(taught_off, kTaught);
  double ride_score_off = score_of(taught_off, kFreeRide);
  if (taught_score_off <= -1e8 || ride_score_off <= -1e8 ||
      !has_single_edge(taught_off, kTaught)) {
    printf("fail: whole-input personal candidate missing at weight 0\n");
    return 1;
  }
  if (tiger_engine_set_word_edge_weight(h, 1.5) != 1) {
    printf("fail: enable prior for personal regression\n");
    return 1;
  }
  std::vector<std::string> taught_on = decode_lines(h, "qygfda", 8);
  double taught_score_on = score_of(taught_on, kTaught);
  double ride_score_on = score_of(taught_on, kFreeRide);
  if (fabs((taught_score_on - taught_score_off) - 1.5) > 1e-4) {
    printf("fail: whole-input personal edge must gain exactly the prior\n");
    return 1;
  }
  if (fabs(ride_score_on - ride_score_off) > 1e-9) {
    printf("fail: free-riding composition must not gain the prior\n");
    return 1;
  }

  // 文本词典先验回归：tsyige 的「同一个」（ts+一个，词表只有其简码
  // tyg 条目）对阵「统一个」（统一+个，不成词组合）。特定上文（实现）
  // 下字符三元让「统一个」以 ~0.13 nats 反超——硬币差；词典文本证据
  // 一票翻回。机制断言（成词候选恰好 +weight、不成词候选不动）不依赖
  // 边缘名次，翻转断言在基线未翻转的环境自动跳过。
  {
    const std::string kReal = "同一个";
    const std::string kGlue = "统一个";
    if (tiger_engine_set_decode_context(h, "实现", 2) != 1) {
      printf("fail: set decode context for text lexicon regression\n");
      return 1;
    }
    std::vector<std::string> lex_off = decode_lines(h, "tsyige", 8);
    double real_off = score_of(lex_off, kReal);
    double glue_off = score_of(lex_off, kGlue);
    if (real_off <= -1e8 || glue_off <= -1e8) {
      printf("skip: tsyige candidate pair missing\n");
    } else {
      if (tiger_engine_set_text_lexicon_weight(h, 1.5) != 1) {
        printf("fail: enable text lexicon prior\n");
        return 1;
      }
      std::vector<std::string> lex_on = decode_lines(h, "tsyige", 8);
      double real_on = score_of(lex_on, kReal);
      double glue_on = score_of(lex_on, kGlue);
      if (fabs((real_on - real_off) - 1.5) > 1e-4) {
        printf("fail: dictionary-text candidate must gain exactly the prior\n");
        return 1;
      }
      if (fabs(glue_on - glue_off) > 1e-9) {
        printf("fail: non-word composition must not gain the prior\n");
        return 1;
      }
      if (glue_off > real_off && real_on <= glue_on) {
        printf("fail: text lexicon prior must flip 同一个 over 统一个\n");
        return 1;
      }
      if (tiger_engine_set_text_lexicon_weight(h, 0.0) != 1) {
        printf("fail: disable text lexicon prior\n");
        return 1;
      }
    }
    if (tiger_engine_set_decode_context(h, "", 2) < 0) {
      printf("fail: clear decode context\n");
      return 1;
    }
  }

  // 非法权重拒绝：负数与 >4。
  if (tiger_engine_set_word_edge_weight(h, -0.5) != -1 ||
      tiger_engine_set_word_edge_weight(h, 4.5) != -1 ||
      tiger_engine_set_text_lexicon_weight(h, -0.5) != -1 ||
      tiger_engine_set_text_lexicon_weight(h, 4.5) != -1) {
    printf("fail: out-of-range weights must be rejected\n");
    return 1;
  }

  tiger_engine_free(h);
  printf("ok: word edge prior flips 支持 over 只吃 and stays reversible\n");
  printf("ok: text lexicon prior votes 同一个 over 统一个\n");
  return 0;
}
