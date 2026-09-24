// 词形整段命中权威引擎测试：输入恰被一条全码词典词（≥3 字）整段覆盖（如
// bagerf→八个人 354 / 把个人 173）时，同码先后由码内词典权重占比决定；
// 权重 0 回退旧行为；二字辅码词形与长句解码在开关两态下逐字节不变
// （注入行不作内部边）。模型或词表缺失时打印 skip 并通过；TIGER_NGRAM
// / TIGER_LEXICON 可覆盖路径。
#include <cstdlib>
#include <cstdio>
#include <cstring>
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

std::string decode_to_string(int handle, const char* raw) {
  std::string output(1 << 20, '\0');
  const int rc = tiger_decode_full(handle, raw, 0, output.data(),
                                   static_cast<int>(output.size()));
  if (rc < 0) return "";
  output.resize(std::strlen(output.c_str()));
  return output;
}

// 解码输出首行是引擎元数据，候选从第二行起：text\tsegmented\tscore...
std::string nth_text(const std::string& decoded, int index) {
  size_t pos = decoded.find('\n');
  for (int i = 0; i < index && pos != std::string::npos; ++i)
    pos = decoded.find('\n', pos + 1);
  if (pos == std::string::npos) return "";
  size_t tab = decoded.find('\t', pos + 1);
  if (tab == std::string::npos) return "";
  return decoded.substr(pos + 1, tab - (pos + 1));
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
  // mohu_zrm.schema.yaml tiger/ 段线上权重（word_form 由用例切换）。
  tiger_engine_set_reading_prior_weight(h, 1.0);
  tiger_engine_set_composed_reading_prior_weight(h, 1.3);
  tiger_engine_set_word_edge_weight(h, 1.5);
  tiger_engine_set_text_lexicon_weight(h, 6.5);
  tiger_engine_set_user_model_weight(h, 1.0);

  bool ok = true;

  // ① 开启：八个人(354) 压过 把个人(173)，同码先后跟随词表权重。
  tiger_engine_set_word_form_weight(h, 6.5);
  std::string on = decode_to_string(h, "bagerf");
  if (nth_text(on, 0) != "八个人" || nth_text(on, 1) != "把个人") {
    printf("fail: bagerf(word_form=6.5) top=%s second=%s\n",
           nth_text(on, 0).c_str(), nth_text(on, 1).c_str());
    ok = false;
  } else {
    printf("pass: bagerf(word_form=6.5) 八个人 > 把个人\n");
  }

  // ①' 长词整段命中：yewufgyuyewuqy 的「也无风雨也无晴」（base 权重
  //     113，全码注入）须压过「业务+风雨+也+无+晴」组合——源表词「业务」
  //     的内部词边 +1.5 曾反杀名句 0.18 nats。
  std::string poem = decode_to_string(h, "yewufgyuyewuqy");
  if (nth_text(poem, 0) != "也无风雨也无晴") {
    printf("fail: yewufgyuyewuqy(word_form=6.5) top=%s\n",
           nth_text(poem, 0).c_str());
    ok = false;
  } else {
    printf("pass: yewufgyuyewuqy(word_form=6.5) 也无风雨也无晴\n");
  }

  // ② 开关两态零回归面：长句（含三字词做前缀的输入）与二字辅码词形
  //    逐字节一致——整段命中只在输入恰为全码长度时触发，注入行不进
  //    句中内部边。
  tiger_engine_set_word_form_weight(h, 0);
  const char* invariance[] = {
      "vegeuurufaviiiyikbqiuuruyivgjuhw",  // 这个输入法支持一口气输入一整句话
      "bagerfdcdcle",                       // 三字词全码做长输入前缀
      "yikbqiui",                           // 三字词全码做长输入前缀
      "vgxju",                              // 二字末辅（用户简码整句）
      "vsmc",                               // 二字末辅
  };
  for (const char* raw : invariance) {
    std::string off = decode_to_string(h, raw);
    tiger_engine_set_word_form_weight(h, 6.5);
    std::string on2 = decode_to_string(h, raw);
    tiger_engine_set_word_form_weight(h, 0);
    if (off != on2 || off.empty()) {
      printf("fail: %s differs between word_form 0/6.5\n", raw);
      ok = false;
    } else {
      printf("pass: %s identical off/on (%s)\n", raw, nth_text(off, 0).c_str());
    }
  }

  tiger_engine_free(h);
  if (!ok) return 1;
  printf("word-form authority: all checks passed\n");
  return 0;
}
