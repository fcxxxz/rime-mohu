#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif

#include "tigerengine.h"

namespace {

class EnvGuard {
 public:
  explicit EnvGuard(const char* name) : name_(name) {
#ifdef _WIN32
    char value[4096] = {};
    const DWORD length = GetEnvironmentVariableA(name, value, sizeof(value));
    if (length > 0 && length < sizeof(value)) {
      had_value_ = true;
      old_value_.assign(value, length);
    }
#else
    const char* value = std::getenv(name);
    if (value) {
      had_value_ = true;
      old_value_ = value;
    }
#endif
  }
  ~EnvGuard() {
#ifdef _WIN32
    _putenv_s(name_.c_str(), had_value_ ? old_value_.c_str() : "");
#else
    if (had_value_) setenv(name_.c_str(), old_value_.c_str(), 1);
    else unsetenv(name_.c_str());
#endif
  }
  void set(const char* value) {
#ifdef _WIN32
    _putenv_s(name_.c_str(), value ? value : "");
#else
    if (value) setenv(name_.c_str(), value, 1);
    else unsetenv(name_.c_str());
#endif
  }

 private:
  std::string name_;
  std::string old_value_;
  bool had_value_ = false;
};

template <typename T>
void append_value(std::vector<uint8_t>* data, const T& value) {
  const size_t offset = data->size();
  data->resize(offset + sizeof(value));
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

void put_u32(std::vector<uint8_t>* data, size_t offset, uint32_t value) {
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

void put_u64(std::vector<uint8_t>* data, size_t offset, uint64_t value) {
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

std::string write_bytes(const std::vector<uint8_t>& data, const char* suffix) {
  char raw[L_tmpnam] = {};
  if (!std::tmpnam(raw)) return {};
  const std::string path = std::string(raw) + suffix;
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  if (!stream) return {};
  stream.write(reinterpret_cast<const char*>(data.data()),
               static_cast<std::streamsize>(data.size()));
  return stream ? path : std::string();
}

std::string write_lexicon() {
  char raw[L_tmpnam] = {};
  if (!std::tmpnam(raw)) return {};
  const std::string path = std::string(raw) + ".txt";
  std::ofstream stream(path, std::ios::trunc);
  if (!stream) return {};
  stream << "a\t甲\t1\t1\n";
  return stream ? path : std::string();
}

std::vector<uint8_t> make_legacy_model() {
  std::vector<uint8_t> model;
  model.insert(model.end(), {'T', 'C', 'S', 'K', 'N', 'M', '0', '1'});
  append_value<int32_t>(&model, 1);  // version
  append_value<int32_t>(&model, 2);  // unigram count
  append_value<uint32_t>(&model, 0);
  append_value<float>(&model, 0.1f);
  append_value<uint32_t>(&model, 0x7532u);
  append_value<float>(&model, 0.9f);
  append_value<uint64_t>(&model, 0);  // bigram count
  append_value<int32_t>(&model, 0);   // bigram context count
  append_value<uint64_t>(&model, 0);  // trigram count
  append_value<uint64_t>(&model, 0);  // trigram context count
  return model;
}

std::vector<uint8_t> make_mobile_model(bool bad_successor = false) {
  // Header (104 bytes), two unigrams, one bigram page and an empty trigram
  // section.  The single context's successor is the character 甲.
  std::vector<uint8_t> model(160, 0);
  std::memcpy(model.data(), "TCSKNM02", 8);
  put_u32(&model, 8, 1);       // version
  put_u32(&model, 12, 104);    // header size
  put_u64(&model, 16, model.size());
  put_u32(&model, 24, 64);     // index stride
  put_u32(&model, 32, 2);      // unigram count
  put_u32(&model, 40, 104);    // unigram offset
  put_u32(&model, 48, 1);      // bigram context count
  put_u32(&model, 52, 1);      // bigram index count = ceil(1/64)
  put_u64(&model, 56, 120);    // bigram blocks
  put_u64(&model, 64, 144);    // bigram index
  put_u32(&model, 72, 0);      // trigram context count
  put_u32(&model, 80, 0);      // trigram index count
  put_u64(&model, 88, 160);    // trigram blocks
  put_u64(&model, 96, 160);    // trigram index
  put_u32(&model, 104, 0);
  put_u32(&model, 108, 0x7532u);
  float p0 = 0.1f, p1 = 0.9f;
  std::memcpy(model.data() + 112, &p0, sizeof(p0));
  std::memcpy(model.data() + 116, &p1, sizeof(p1));
  put_u64(&model, 120, 0);      // BOS context key
  float lambda = 0.0f;
  std::memcpy(model.data() + 128, &lambda, sizeof(lambda));
  put_u32(&model, 132, bad_successor ? UINT32_MAX : 1);
  put_u32(&model, 136, 0x7532u); // successor character
  std::memcpy(model.data() + 140, &p1, sizeof(p1));
  put_u64(&model, 144, 0);      // index key
  put_u64(&model, 152, 120);    // page offset
  return model;
}

std::vector<uint8_t> make_mobile_model_with_unused_bad_page() {
  constexpr uint32_t kStride = 64;
  constexpr uint32_t kContexts = 65;
  constexpr uint64_t kBlocks = 120;
  constexpr uint64_t kFirstPageBytes = 16 + 8 + (kStride - 1) * 16;
  constexpr uint64_t kSecondPage = kBlocks + kFirstPageBytes;
  constexpr uint64_t kIndex = kSecondPage + 16;
  constexpr uint64_t kTri = kIndex + 2 * 16;
  std::vector<uint8_t> model(static_cast<size_t>(kTri), 0);
  std::memcpy(model.data(), "TCSKNM02", 8);
  put_u32(&model, 8, 1);
  put_u32(&model, 12, 104);
  put_u64(&model, 16, model.size());
  put_u32(&model, 24, kStride);
  put_u32(&model, 32, 2);
  put_u32(&model, 40, 104);
  put_u32(&model, 48, kContexts);
  put_u32(&model, 52, 2);
  put_u64(&model, 56, kBlocks);
  put_u64(&model, 64, kIndex);
  put_u32(&model, 72, 0);
  put_u32(&model, 80, 0);
  put_u64(&model, 88, kTri);
  put_u64(&model, 96, kTri);
  put_u32(&model, 104, 0);
  put_u32(&model, 108, 0x7532u);
  float p0 = 0.1f, p1 = 0.9f;
  std::memcpy(model.data() + 112, &p0, sizeof(p0));
  std::memcpy(model.data() + 116, &p1, sizeof(p1));
  // First page: 64 ordered contexts.  Only key 0 has one valid successor;
  // the remaining records have zero successors.
  uint64_t at = kBlocks;
  for (uint32_t i = 0; i < kStride; ++i) {
    put_u64(&model, static_cast<size_t>(at), i);
    float lambda = 0.0f;
    std::memcpy(model.data() + at + 8, &lambda, sizeof(lambda));
    put_u32(&model, static_cast<size_t>(at + 12), i == 0 ? 1u : 0u);
    if (i == 0) {
      put_u32(&model, static_cast<size_t>(at + 16), 0x7532u);
      std::memcpy(model.data() + at + 20, &p1, sizeof(p1));
      at += 24;
    } else {
      at += 16;
    }
  }
  // Second page is never touched by key 0; its successor count is malformed.
  put_u64(&model, static_cast<size_t>(kSecondPage), 64);
  put_u32(&model, static_cast<size_t>(kSecondPage + 12), UINT32_MAX);
  put_u64(&model, static_cast<size_t>(kIndex), 0);
  put_u64(&model, static_cast<size_t>(kIndex + 8), kBlocks);
  put_u64(&model, static_cast<size_t>(kIndex + 16), 64);
  put_u64(&model, static_cast<size_t>(kIndex + 24), kSecondPage);
  return model;
}

std::vector<uint8_t> make_mobile_metadata_fixture(const char* kind) {
  std::vector<uint8_t> model = make_mobile_model(false);
  if (std::strcmp(kind, "count") == 0) {
    put_u32(&model, 52, 0);  // non-zero contexts require one index page
  } else if (std::strcmp(kind, "key") == 0) {
    model = make_mobile_model_with_unused_bad_page();
    put_u64(&model, 1168, 64);  // first index key
    put_u64(&model, 1184, 0);   // second key descends
  } else if (std::strcmp(kind, "overlap") == 0) {
    put_u64(&model, 152, 144);  // page has no 16-byte record in the block
  } else if (std::strcmp(kind, "stride") == 0) {
    put_u32(&model, 24, 8);
  } else if (std::strcmp(kind, "zero") == 0) {
    put_u32(&model, 48, 0);
    put_u32(&model, 52, 1);
  } else if (std::strcmp(kind, "header") == 0) {
    put_u32(&model, 12, 103);
  } else if (std::strcmp(kind, "size") == 0) {
    put_u64(&model, 16, model.size() + 1);
  } else if (std::strcmp(kind, "order") == 0) {
    put_u64(&model, 56, 145);  // block starts after its index
  } else if (std::strcmp(kind, "tri-count") == 0) {
    put_u32(&model, 80, 1);  // zero trigram contexts cannot have an index page
  }
  return model;
}

std::vector<uint8_t> make_word_model() {
  std::vector<uint8_t> model(120, 0);
  std::memcpy(model.data(), "MHKNM01", 7);
  put_u32(&model, 8, 1);       // version
  put_u32(&model, 12, 120);    // header size
  put_u32(&model, 24, 3);      // vocabulary count
  put_u32(&model, 28, 1);      // embedding dimension
  put_u32(&model, 32, 64);     // index stride
  put_u64(&model, 40, 120);    // vocabulary offset
  for (const char* word : {"<s>", "</s>", "甲"}) {
    const uint32_t length = static_cast<uint32_t>(std::strlen(word));
    append_value<uint32_t>(&model, length);
    model.insert(model.end(), word, word + length);
  }
  const uint64_t uni_offset = model.size();
  for (float probability : {0.1f, 0.2f, 0.9f}) append_value<float>(&model, probability);
  const uint64_t empty_section = model.size();
  const uint64_t embedding_offset = model.size();
  for (int index = 0; index < 3; ++index) {
    model.push_back(index == 2 ? 1 : 0);
    append_value<float>(&model, 1.0f);
  }
  put_u64(&model, 16, model.size());  // file size
  put_u64(&model, 48, uni_offset);
  put_u64(&model, 56, empty_section);  // bigram blocks
  put_u64(&model, 64, empty_section);  // bigram index
  put_u64(&model, 72, empty_section);  // trigram blocks
  put_u64(&model, 80, embedding_offset);
  return model;
}

std::vector<uint8_t> make_container(const std::vector<uint8_t>& character,
                                    const std::vector<uint8_t>& word) {
  const uint64_t char_offset = 64;
  const uint64_t word_offset = char_offset + character.size();
  const uint64_t total_size = word_offset + word.size();
  std::vector<uint8_t> container(64, 0);
  std::memcpy(container.data(), "MHCTN01\0", 8);
  put_u32(&container, 8, 1);
  put_u32(&container, 12, 64);
  put_u64(&container, 16, total_size);
  put_u32(&container, 24, 3);  // character + word sections
  put_u64(&container, 32, char_offset);
  put_u64(&container, 40, character.size());
  put_u64(&container, 48, word_offset);
  put_u64(&container, 56, word.size());
  container.insert(container.end(), character.begin(), character.end());
  container.insert(container.end(), word.begin(), word.end());
  return container;
}

bool status_has(int handle, const char* text) {
  char status[2048] = {};
  return tiger_status(handle, status, sizeof(status)) == 0 &&
         std::strstr(status, text) != nullptr;
}

bool decode_first(int handle, std::string* text) {
  char output[8192] = {};
  if (tiger_decode_full(handle, "a", 0, output, sizeof(output)) <= 0) return false;
  const char* first = std::strchr(output, '\n');
  if (!first) return false;
  ++first;
  const char* end = std::strchr(first, '\t');
  if (!end || end == first) return false;
  text->assign(first, static_cast<size_t>(end - first));
  return true;
}

bool create_and_check(const std::string& model, const std::string& lexicon,
                      const char* format, const char* scorer,
                      bool decode_required) {
  char error[512] = {};
  const int handle = tiger_engine_create(model.c_str(), lexicon.c_str(), 32, 1,
                                         error, sizeof(error));
  if (handle < 0) {
    std::fprintf(stderr, "create %s failed: %s\n", format, error);
    return false;
  }
  const bool status_ok = status_has(handle, format) && status_has(handle, scorer);
  std::string first;
  const bool decode_ok = !decode_required || (decode_first(handle, &first) && first == "甲");
  tiger_engine_free(handle);
  if (!status_ok || !decode_ok) {
    std::fprintf(stderr, "unexpected %s status/decode\n", format);
    return false;
  }
  return true;
}

bool expect_rejects(const std::string& model, const std::string& lexicon) {
  char error[512] = {};
  const int handle = tiger_engine_create(model.c_str(), lexicon.c_str(), 32, 1,
                                         error, sizeof(error));
  if (handle >= 0) {
    tiger_engine_free(handle);
    return false;
  }
  return error[0] != '\0';
}

bool expect_default_bad_page(const std::string& model, const std::string& lexicon) {
  char error[512] = {};
  const int handle = tiger_engine_create(model.c_str(), lexicon.c_str(), 32, 1,
                                         error, sizeof(error));
  if (handle < 0) {
    std::fprintf(stderr, "default lazy create rejected: %s\n", error);
    return false;
  }
  char output[8192] = {};
  const int first = tiger_decode_full(handle, "a", 0, output, sizeof(output));
  const std::string first_error = tiger_last_error();
  std::memset(output, 0, sizeof(output));
  double elapsed = 0.0;
  const int second = tiger_decode(handle, "a", 0, output, sizeof(output), &elapsed);
  const std::string second_error = tiger_last_error();
  std::memset(output, 0, sizeof(output));
  const int third = tiger_decode_full(handle, "a", 0, output, sizeof(output));
  const std::string third_error = tiger_last_error();
  tiger_engine_free(handle);
  return first < 0 && second < 0 && third < 0 && !first_error.empty() &&
         first_error == second_error && second_error == third_error &&
         first_error.find("invalid n-gram page") != std::string::npos;
}

bool expect_unused_bad_page_is_lazy(const std::string& model,
                                    const std::string& lexicon) {
  char error[512] = {};
  const int handle = tiger_engine_create(model.c_str(), lexicon.c_str(), 32, 1,
                                         error, sizeof(error));
  if (handle < 0) return false;
  char status[2048] = {};
  const bool loaded = tiger_status(handle, status, sizeof(status)) == 0 &&
                      std::strstr(status, "format=TCSKNM02") != nullptr;
  const std::string stale = tiger_last_error();
  tiger_engine_free(handle);
  return loaded && stale.empty();
}

bool expect_valid_create_clears_error(const std::string& model,
                                      const std::string& lexicon) {
  char error[512] = {};
  const int handle = tiger_engine_create(model.c_str(), lexicon.c_str(), 32, 1,
                                         error, sizeof(error));
  if (handle < 0) return false;
  const bool cleared = std::string(tiger_last_error()).empty();
  tiger_engine_free(handle);
  return cleared;
}

int run_lazy() {
  const std::string lexicon = write_lexicon();
  const std::string bad_path = write_bytes(make_mobile_model(true), ".bad-mobile.bin");
  const std::string valid_path = write_bytes(make_mobile_model(false), ".valid-mobile.bin");
  const std::string unused_path = write_bytes(make_mobile_model_with_unused_bad_page(),
                                              ".unused-bad-mobile.bin");
  if (lexicon.empty() || bad_path.empty() || valid_path.empty() || unused_path.empty()) return 2;
  EnvGuard strict("MOHU_TIGER_STRICT_VALIDATE");
  strict.set(nullptr);
  int result = 0;
  if (!expect_default_bad_page(bad_path, lexicon)) result = 3;
  strict.set("0");
  if (!result && !expect_default_bad_page(bad_path, lexicon)) result = 4;
  strict.set("");
  if (!result && !expect_default_bad_page(bad_path, lexicon)) result = 5;
  strict.set("yes");
  if (!result && !expect_default_bad_page(bad_path, lexicon)) result = 6;
  strict.set("1");
  if (!result && !expect_rejects(bad_path, lexicon)) result = 7;
  strict.set(nullptr);
  if (!result && !expect_unused_bad_page_is_lazy(unused_path, lexicon)) result = 8;
  if (!result && !expect_valid_create_clears_error(valid_path, lexicon)) result = 9;
  std::remove(lexicon.c_str());
  std::remove(bad_path.c_str());
  std::remove(valid_path.c_str());
  std::remove(unused_path.c_str());
  return result;
}

int run_metadata() {
  const std::string lexicon = write_lexicon();
  if (lexicon.empty()) return 2;
  int result = 0;
  for (const char* kind : {"count", "key", "overlap", "stride", "zero",
                           "header", "size", "order", "tri-count"}) {
    const std::string path = write_bytes(make_mobile_metadata_fixture(kind), ".metadata.bin");
    if (path.empty() || !expect_rejects(path, lexicon)) result = 3;
    std::remove(path.c_str());
  }
  std::remove(lexicon.c_str());
  return result;
}

int run_dispatch() {
  const std::string lexicon = write_lexicon();
  const std::vector<uint8_t> character = make_legacy_model();
  const std::vector<uint8_t> word = make_word_model();
  const std::string character_path = write_bytes(character, ".char.bin");
  const std::string word_path = write_bytes(word, ".word.bin");
  const std::string container_path = write_bytes(make_container(character, word), ".container.bin");
  const std::string unknown_path = write_bytes({'N', 'O', 'T', 'A', 'M', 'O', 'D', 'L'}, ".unknown.bin");
  if (lexicon.empty() || character_path.empty() || word_path.empty() ||
      container_path.empty() || unknown_path.empty()) return 2;

  int result = 0;
  if (!create_and_check(character_path, lexicon, "format=TCSKNM01", "word_scorer=off", true))
    result = 3;
  if (!result && !create_and_check(word_path, lexicon, "format=MHKNM01", "word_scorer=primary", true))
    result = 4;
  if (!result && !create_and_check(container_path, lexicon, "format=TCSKNM01", "word_scorer=packed", true))
    result = 5;
  for (int attempt = 0; !result && attempt < 3; ++attempt) {
    if (!expect_rejects(unknown_path, lexicon)) result = 6;
  }
  std::remove(lexicon.c_str());
  std::remove(character_path.c_str());
  std::remove(word_path.c_str());
  std::remove(container_path.c_str());
  std::remove(unknown_path.c_str());
  return result;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc > 1 && std::strcmp(argv[1], "lazy") == 0) return run_lazy();
  if (argc > 1 && std::strcmp(argv[1], "metadata") == 0) return run_metadata();
  if (argc > 1 && std::strcmp(argv[1], "unknown") == 0) return run_dispatch() == 0 ? 0 : 1;
  return run_dispatch();
}
