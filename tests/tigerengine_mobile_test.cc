#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "tigerengine.h"

namespace {

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
  (void)argv;
  if (argc > 1 && std::strcmp(argv[1], "unknown") == 0) return run_dispatch() == 0 ? 0 : 1;
  return run_dispatch();
}
