#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include "tigerengine.h"

namespace {

std::string write_file(const std::string& suffix, const std::vector<uint8_t>& data) {
  std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) + suffix;
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  assert(stream);
  stream.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
  assert(stream);
  return path;
}

void expect_create_rejects_without_signal(const std::vector<uint8_t>& model, const char* name) {
  const std::string model_path = write_file(std::string("-") + name + ".bin", model);
  const std::string lexicon_path = write_file(std::string("-") + name + ".txt", {'a', '\t', 0xe7, 0x94, 0xb2, '\n'});
  const pid_t child = fork();
  assert(child >= 0);
  if (child == 0) {
    char error[512] = {};
    const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                           error, sizeof(error));
    if (handle >= 0) tiger_engine_free(handle);
    _exit(handle < 0 ? 0 : 1);
  }
  int status = 0;
  assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status));
  assert(WEXITSTATUS(status) == 0);
}

void put_u32(std::vector<uint8_t>* data, size_t offset, uint32_t value) {
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

void put_u64(std::vector<uint8_t>* data, size_t offset, uint64_t value) {
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

template <typename T>
void append_value(std::vector<uint8_t>* data, const T& value) {
  const size_t offset = data->size();
  data->resize(offset + sizeof(value));
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

std::string write_many_candidate_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-many-candidates.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  for (int index = 0; index < 25; ++index)
    stream << "a\tcandidate" << index << "\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_final_adjustment_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-final-adjustment.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  // The last entry is common (no isolation penalty), while the first twenty
  // are deliberately rare.  Its pre-EOS score is slightly lower, so it is
  // the 21st state before final adjustments but should enter the exposed
  // top-20 after the +2 isolation difference.
  for (int index = 0; index < 20; ++index)
    stream << "a\t" << static_cast<char>('a' + index) << "\t1\t5000\n";
  stream << "a\tZ\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_many_early_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-many-early.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  for (int index = 0; index < 25; ++index)
    stream << "ab\t" << static_cast<char>('a' + index) << "\t1\t1\n";
  stream << "xy\tz\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_many_consensus_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-many-consensus.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "ab\t共\t1\t1\n";
  for (int index = 0; index < 25; ++index)
    stream << "cd\t" << static_cast<char>('a' + index) << "\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_single_candidate_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-single-candidate.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "a\tcandidate\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_overflowing_rank_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-overflowing-rank.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "a\tcandidate\t999999999999999999999999\t999999999999999999999999\n";
  assert(stream);
  return path;
}

std::string write_sentence_segmentation_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-sentence-segmentation.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "m\t没有\t1\t1\n";
  stream << "jm\t紧密\t1\t1\n";
  stream << "jh\t结合\t1\t1\n";
  stream << "mj\t满\t1\t1\n";
  stream << "mj\t慢\t2\t2\n";
  stream << "mjh\t慢\t1\t2\n";
  stream << "mjh\t每句话\t2\t20001\n";
  stream << "mjmj\t慢慢\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_incremental_phrase_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-incremental-phrase.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  // The two-key phrase is valid only when "ab" is the complete input.  When
  // the composition grows to "abcd", the full rebuild must use the ordinary
  // two-key edge instead of carrying that terminal phrase into the sentence.
  stream << "ab\t甲\t1\t1\n";
  stream << "cd\t丁\t1\t1\n";
  stream << "ab\t甲乙\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_intermediate_prune_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-intermediate-prune.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  // Two alternatives at the first edge fan out to eight alternatives at the
  // middle edge.  A beam of two must prune the middle bucket; the truncation
  // proof has to reach the final edge even though that bucket is never
  // normalized again before emit().
  stream << "ab\tA\t1\t1\n";
  stream << "ab\tZ\t1\t1\n";
  for (char value = 'b'; value <= 'i'; ++value)
    stream << "cd\t" << value << "\t1\t1\n";
  stream << "ef\tc\t1\t1\n";
  assert(stream);
  return path;
}

std::string write_many_candidate_model() {
  std::vector<uint8_t> model;
  model.insert(model.end(), {'T', 'C', 'S', 'K', 'N', 'M', '0', '1'});
  append_value<uint32_t>(&model, 1);  // version
  append_value<uint32_t>(&model, 1);  // unigram count
  append_value<uint32_t>(&model, 0);  // unknown unigram key
  append_value<float>(&model, 0.1f);  // unknown unigram probability
  append_value<uint64_t>(&model, 0);  // bigram count
  append_value<int32_t>(&model, 0);   // bigram context count
  append_value<uint64_t>(&model, 0);  // trigram count
  append_value<uint64_t>(&model, 0);  // trigram context count
  return write_file("-many-candidates.bin", model);
}

std::string write_final_adjustment_model() {
  std::vector<uint8_t> model;
  model.insert(model.end(), {'T', 'C', 'S', 'K', 'N', 'M', '0', '1'});
  append_value<uint32_t>(&model, 1);  // version
  append_value<uint32_t>(&model, 22); // unigram count (unknown + a..t + Z)
  append_value<uint32_t>(&model, 0);  // unknown unigram key
  append_value<float>(&model, 0.1f);  // unknown unigram probability
  for (uint32_t key = static_cast<uint32_t>('a'); key <= static_cast<uint32_t>('t'); ++key) {
    append_value<uint32_t>(&model, key);
    append_value<float>(&model, 0.2f);
  }
  append_value<uint32_t>(&model, static_cast<uint32_t>('Z'));
  append_value<float>(&model, 0.05f);
  append_value<uint64_t>(&model, 0);  // bigram count
  append_value<int32_t>(&model, 0);   // bigram context count
  append_value<uint64_t>(&model, 0);  // trigram count
  append_value<uint64_t>(&model, 0);  // trigram context count
  return write_file("-final-adjustment.bin", model);
}

std::string write_nonfinite_model() {
  std::vector<uint8_t> model;
  model.insert(model.end(), {'T', 'C', 'S', 'K', 'N', 'M', '0', '1'});
  append_value<uint32_t>(&model, 1);
  append_value<uint32_t>(&model, 1);
  append_value<uint32_t>(&model, 0);
  append_value<float>(&model, std::numeric_limits<float>::quiet_NaN());
  append_value<uint64_t>(&model, 0);
  append_value<int32_t>(&model, 0);
  append_value<uint64_t>(&model, 0);
  append_value<uint64_t>(&model, 0);
  return write_file("-nonfinite.bin", model);
}

std::vector<uint8_t> make_mhknm01_model() {
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

std::string write_mhknm01_model() {
  return write_file("-mhknm01.bin", make_mhknm01_model());
}

std::string write_mhknm01_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-mhknm01.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "ab\t甲\t1\t1\n";
  assert(stream);
  return path;
}

void expect_mhknm01_loads_and_decodes() {
  const std::string model_path = write_mhknm01_model();
  const std::string lexicon_path = write_mhknm01_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[4096] = {};
  assert(tiger_decode_full(handle, "ab", 0, output, sizeof(output)) == 1);
  assert(std::strstr(output, "\n甲\t") != nullptr);
  tiger_engine_free(handle);
}

void expect_malformed_mhknm01_rejected() {
  std::vector<uint8_t> model = make_mhknm01_model();
  put_u64(&model, 80, model.size() + 1);  // embedding section outside the file
  expect_create_rejects_without_signal(model, "mhknm01-bad-embedding");
}

void expect_final_output_limit() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_many_candidate_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[4096] = {};
  double elapsed = 0;
  const int count = tiger_decode(handle, "a", 0, output, sizeof(output), &elapsed);
  assert(count == 20);
  int truncated = 0;
  int early_truncated = 0;
  int uses_incomplete = 0;
  int prefers_incomplete = 0;
  int final_count = 0;
  int early_count = 0;
  assert(std::sscanf(output, "%d %d %d %d %d %d", &truncated, &early_truncated,
                     &uses_incomplete, &prefers_incomplete, &final_count,
                     &early_count) == 6);
  assert(truncated == 1);
  assert(final_count == 20);
  tiger_engine_free(handle);
}

void expect_final_adjustment_is_applied_before_top20_limit() {
  const std::string model_path = write_final_adjustment_model();
  const std::string lexicon_path = write_final_adjustment_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "a", 0, output, sizeof(output), &elapsed) == 20);
  assert(std::strstr(output, "\nZ\t") != nullptr);
  tiger_engine_free(handle);
}

void expect_early_output_cap_is_marked_truncated() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_many_early_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "abx", 1, output, sizeof(output), &elapsed) >= 0);
  int truncated = 0;
  int early_truncated = 0;
  int uses_incomplete = 0;
  int prefers_incomplete = 0;
  int final_count = 0;
  int early_count = 0;
  int visible_consensus = 0;
  int consensus_complete = 0;
  size_t consensus_bytes = 0;
  size_t consensus_raw = 0;
  assert(std::sscanf(output, "%d %d %d %d %d %d %d %zu %zu %d",
                     &truncated, &early_truncated, &uses_incomplete,
                     &prefers_incomplete, &final_count, &early_count,
                     &consensus_complete, &consensus_bytes, &consensus_raw,
                     &visible_consensus) == 10);
  assert(uses_incomplete == 1);
  assert(early_truncated == 1);
  assert(early_count == 20);
  assert(consensus_complete == 1);
  assert(consensus_bytes == 0 && consensus_raw == 0);
  assert(visible_consensus == 1);
  tiger_engine_free(handle);
}

void expect_truncated_output_exposes_safe_consensus_summary() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_many_consensus_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "abcd", 0, output, sizeof(output), &elapsed) == 20);
  int truncated = 0;
  int early_truncated = 0;
  int uses_incomplete = 0;
  int prefers_incomplete = 0;
  int final_count = 0;
  int early_count = 0;
  int consensus_complete = 0;
  size_t consensus_bytes = 0;
  size_t consensus_raw = 0;
  int visible_consensus = 0;
  assert(std::sscanf(output, "%d %d %d %d %d %d %d %zu %zu %d",
                     &truncated, &early_truncated, &uses_incomplete,
                     &prefers_incomplete, &final_count, &early_count,
                     &consensus_complete, &consensus_bytes, &consensus_raw,
                     &visible_consensus) == 10);
  assert(truncated == 1 && final_count == 20 && early_count == 0);
  assert(consensus_complete == 1);
  assert(consensus_bytes == 3);  // UTF-8 byte length of "共"
  assert(consensus_raw == 2);
  assert(visible_consensus == 0);
  tiger_engine_free(handle);
}

void expect_raw_length_rejected() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_single_candidate_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  const std::string long_raw(129, 'a');
  char output[1 << 20] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, long_raw.c_str(), 0, output, sizeof(output), &elapsed) < 0);
  assert(tiger_decode_full(handle, long_raw.c_str(), 0, output, sizeof(output)) < 0);
  tiger_engine_free(handle);
}

void expect_overflowing_lexicon_numbers_use_defaults() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_overflowing_rank_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[4096] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "a", 0, output, sizeof(output), &elapsed) == 1);
  // Invalid/overflowing rank values fall back to rank 1 instead of invoking
  // undefined signed conversion behavior.
  assert(std::strstr(output, "\t1\t") != nullptr);
  tiger_engine_free(handle);
}

void expect_nonfinite_model_rejected() {
  const std::string model_path = write_nonfinite_model();
  const std::string lexicon_path = write_single_candidate_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[4096] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "a", 0, output, sizeof(output), &elapsed) < 0);
  assert(std::strstr(output, "nan") == nullptr);
  tiger_engine_free(handle);
}

void expect_multi_key_input_rejects_single_key_edges() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_sentence_segmentation_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);

  char output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "m", 0, output, sizeof(output), &elapsed) == 1);
  assert(std::strstr(output, "\n没有\t") != nullptr);

  std::memset(output, 0, sizeof(output));
  assert(tiger_decode_full(handle, "mjh", 0, output, sizeof(output)) == 2);
  assert(std::strstr(output, "\n每句话\t") != nullptr);

  for (const char* raw : {"mj", "mjm", "mjmj", "mjmjh"})
    assert(tiger_decode(handle, raw, 0, output, sizeof(output), &elapsed) >= 0);
  assert(std::strstr(output, "\n慢慢\t") != nullptr);
  assert(std::strstr(output, "没有紧密结合") == nullptr);
  assert(std::strstr(output, "每句话") == nullptr);

  std::memset(output, 0, sizeof(output));
  assert(tiger_decode_full(handle, "mjmjh", 0, output, sizeof(output)) >= 1);
  assert(std::strstr(output, "\n慢慢\t") != nullptr);
  assert(std::strstr(output, "没有紧密结合") == nullptr);
  assert(std::strstr(output, "每句话") == nullptr);
  tiger_engine_free(handle);
}

void expect_incremental_extension_matches_full_rebuild() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_incremental_phrase_lexicon();
  char error[512] = {};
  const int incremental = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                              error, sizeof(error));
  const int fresh = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                        error, sizeof(error));
  assert(incremental >= 0 && fresh >= 0);

  char incremental_output[8192] = {};
  char fresh_output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode(incremental, "ab", 0, incremental_output,
                      sizeof(incremental_output), &elapsed) >= 1);
  assert(tiger_decode(incremental, "abcd", 0, incremental_output,
                      sizeof(incremental_output), &elapsed) >= 1);
  assert(tiger_decode_full(fresh, "abcd", 0, fresh_output, sizeof(fresh_output)) >= 1);
  // A terminal phrase from the shorter composition must not leak into the
  // longer sentence.  Comparing the complete serialized result also covers
  // pathmap and score metadata, not only the displayed text.
  assert(std::strcmp(incremental_output, fresh_output) == 0);
  assert(std::strstr(incremental_output, "甲乙丁") == nullptr);
  assert(std::strstr(incremental_output, "甲丁") != nullptr);

  // The inverse transition must also rebuild: the phrase suppressed while
  // the input was longer becomes a valid complete candidate after backspace.
  assert(tiger_decode(incremental, "ab", 0, incremental_output,
                      sizeof(incremental_output), &elapsed) >= 1);
  assert(tiger_decode_full(fresh, "ab", 0, fresh_output, sizeof(fresh_output)) >= 1);
  assert(std::strcmp(incremental_output, fresh_output) == 0);
  assert(std::strstr(incremental_output, "甲乙") != nullptr);
  tiger_engine_free(incremental);
  tiger_engine_free(fresh);
}

void expect_intermediate_prune_propagates_truncation() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_intermediate_prune_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 2, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode_full(handle, "abcdef", 1, output, sizeof(output)) >= 1);
  int truncated = 0;
  int early_truncated = 0;
  int uses_incomplete = 0;
  int prefers_incomplete = 0;
  int final_count = 0;
  int early_count = 0;
  int consensus_complete = 0;
  size_t consensus_bytes = 0;
  size_t consensus_raw = 0;
  int visible_consensus = 0;
  assert(std::sscanf(output, "%d %d %d %d %d %d %d %zu %zu %d",
                     &truncated, &early_truncated, &uses_incomplete,
                     &prefers_incomplete, &final_count, &early_count,
                     &consensus_complete, &consensus_bytes, &consensus_raw,
                     &visible_consensus) == 10);
  assert(truncated == 1);
  assert(consensus_complete == 0);
  assert(visible_consensus == 1);
  tiger_engine_free(handle);
}

void expect_invalid_input_clears_incremental_state() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_incremental_phrase_lexicon();
  const pid_t child = fork();
  assert(child >= 0);
  if (child == 0) {
    char error[512] = {};
    const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                           error, sizeof(error));
    if (handle < 0) _exit(2);
    char output[8192] = {};
    double elapsed = 0;
    // Move from a valid composition through numeric/empty input and then
    // back to letters.  None of the invalid raws may leave a reusable state
    // frontier behind.
    if (tiger_decode(handle, "ab", 1, output, sizeof(output), &elapsed) < 0)
      _exit(3);
    if (tiger_decode(handle, "", 1, output, sizeof(output), &elapsed) < 0)
      _exit(4);
    if (tiger_decode(handle, "33", 1, output, sizeof(output), &elapsed) < 0)
      _exit(5);
    // "33cd" has no valid path because the numeric prefix is not a code.  A
    // stale bucket from "ab" would incorrectly produce the "丁" continuation.
    const int count = tiger_decode(handle, "33cd", 1, output, sizeof(output), &elapsed);
    if (count != 0 || std::strstr(output, "甲") != nullptr)
      _exit(6);
    tiger_engine_free(handle);
    _exit(0);
  }
  int status = 0;
  assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status));
  assert(WEXITSTATUS(status) == 0);
}

void expect_include_early_is_part_of_decode_cache_identity() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_incremental_phrase_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[8192] = {};
  double elapsed = 0;
  assert(tiger_decode(handle, "abc", 1, output, sizeof(output), &elapsed) >= 0);
  int truncated = 0;
  int early_truncated = 0;
  int uses_incomplete = 0;
  int prefers_incomplete = 0;
  int final_count = 0;
  int early_count = 0;
  assert(std::sscanf(output, "%d %d %d %d %d %d", &truncated, &early_truncated,
                     &uses_incomplete, &prefers_incomplete, &final_count,
                     &early_count) == 6);
  assert(early_count > 0);

  // The same normalized raw with early disabled must not reuse the previous
  // result or expose its incomplete candidates.
  assert(tiger_decode(handle, "abc", 0, output, sizeof(output), &elapsed) >= 0);
  assert(std::sscanf(output, "%d %d %d %d %d %d", &truncated, &early_truncated,
                     &uses_incomplete, &prefers_incomplete, &final_count,
                     &early_count) == 6);
  assert(early_count == 0 && uses_incomplete == 0);
  tiger_engine_free(handle);
}

void expect_final_candidates_include_pathmaps() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_sentence_segmentation_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  char output[8192] = {};
  assert(tiger_decode_full(handle, "mjh", 0, output, sizeof(output)) >= 1);
  const char* line_start = std::strchr(output, '\n');
  assert(line_start != nullptr && line_start[1] != '\0');
  ++line_start;
  const char* line_end = std::strchr(line_start, '\n');
  assert(line_end != nullptr);
  int tabs = 0;
  const char* fifth_tab = nullptr;
  for (const char* cursor = line_start; cursor < line_end; ++cursor) {
    if (*cursor == '\t') {
      ++tabs;
      if (tabs == 5) fifth_tab = cursor;
    }
  }
  assert(tabs == 5 && fifth_tab != nullptr);
  assert(fifth_tab + 1 < line_end && fifth_tab[1] != '\t');
  tiger_engine_free(handle);
}

std::string write_personal_overlay_lexicon() {
  const std::string path = "/tmp/mohu-tiger-safety-" + std::to_string(getpid()) +
                           "-personal-overlay.txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "ab\t甲\t1\t1\n";
  stream << "cd\t丁\t1\t1\n";
  stream << "ef\t戊\t1\t1\n";
  assert(stream);
  return path;
}

void expect_personal_overlay_replacement_and_internal_edges() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_personal_overlay_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);

  char output[8192] = {};
  assert(tiger_engine_set_personal_lexicon(handle, "abcd\t甲乙\t8\n") == 0);
  assert(tiger_decode_full(handle, "abcd", 0, output, sizeof(output)) >= 1);
  assert(std::strstr(output, "甲乙") != nullptr);

  std::memset(output, 0, sizeof(output));
  assert(tiger_decode_full(handle, "abcdef", 0, output, sizeof(output)) >= 1);
  assert(std::strstr(output, "甲乙") != nullptr);

  // Replacing the snapshot with an empty payload must remove the old edge and
  // invalidate any cached result built with the previous overlay.
  assert(tiger_engine_set_personal_lexicon(handle, "") == 0);
  std::memset(output, 0, sizeof(output));
  assert(tiger_decode_full(handle, "abcd", 0, output, sizeof(output)) >= 1);
  assert(std::strstr(output, "甲乙") == nullptr);

  // Malformed rows are ignored without making a valid snapshot update fail.
  assert(tiger_engine_set_personal_lexicon(handle,
                                           "odd\trow\t1\nxy\t坏\\xff\t2\n") == 0);
  assert(tiger_engine_set_personal_lexicon(handle, nullptr) < 0);
  tiger_engine_free(handle);
  assert(tiger_engine_set_personal_lexicon(handle, "abcd\t甲乙\t1\n") < 0);
}

void expect_personal_overlay_large_payload_is_accepted() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_single_candidate_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);
  std::string large_payload;
  for (int index = 0; index < 6000; ++index) {
    std::string text;
    for (int repeat = 0; repeat < 62; ++repeat) text += "\xe7\x94\xb2";
    text += std::to_string(index);
    large_payload += "abcd\t" + text + "\t1\n";
  }
  assert(large_payload.size() > (1u << 20));
  assert(tiger_engine_set_personal_lexicon(handle, large_payload.c_str()) == 0);
  tiger_engine_free(handle);
}

void expect_stale_engine_handles_are_rejected() {
  const std::string model_path = write_many_candidate_model();
  const std::string lexicon_path = write_single_candidate_lexicon();
  char error[512] = {};
  const int first = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                        error, sizeof(error));
  assert(first >= 0);
  tiger_engine_free(first);
  const int second = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(second != first);
  char output[4096] = {};
  double elapsed = 0;
  assert(tiger_decode(first, "a", 0, output, sizeof(output), &elapsed) < 0);
  assert(tiger_engine_set_personal_lexicon(first, "ab\t甲乙\t1\n") < 0);
  tiger_engine_free(second);
}

void expect_null_api_rejected() {
  const std::string model_path = write_file("-null-api.bin", {
      'T', 'C', 'S', 'K', 'N', 'M', '0', '1',
      1, 0, 0, 0, 1, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
  });
  const std::string lexicon_path = write_file("-null-api.txt", {'a', '\t', 0xe7, 0x94, 0xb2, '\n'});
  const pid_t child = fork();
  assert(child >= 0);
  if (child == 0) {
    char error[512] = {};
    if (tiger_engine_create(nullptr, lexicon_path.c_str(), 200, 1, error, sizeof(error)) >= 0)
      _exit(1);
    if (tiger_engine_create(model_path.c_str(), nullptr, 200, 1, error, sizeof(error)) >= 0)
      _exit(1);
    const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                           error, sizeof(error));
    if (handle < 0) _exit(2);
    char output[256] = {};
    double elapsed = 0;
    if (tiger_decode(handle, nullptr, 0, output, sizeof(output), &elapsed) >= 0)
      _exit(1);
    if (tiger_decode(handle, "a", 0, nullptr, sizeof(output), &elapsed) >= 0)
      _exit(1);
    if (tiger_decode_full(handle, nullptr, 0, output, sizeof(output)) >= 0)
      _exit(1);
    if (tiger_decode_full(handle, "a", 0, nullptr, sizeof(output)) >= 0)
      _exit(1);
    if (tiger_engine_set_personal_lexicon(handle, nullptr) >= 0)
      _exit(1);
    tiger_engine_free(handle);
    _exit(0);
  }
  int status = 0;
  assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status));
  assert(WEXITSTATUS(status) == 0);
}


}  // namespace

int main() {
  expect_final_output_limit();
  expect_final_adjustment_is_applied_before_top20_limit();
  expect_early_output_cap_is_marked_truncated();
  expect_truncated_output_exposes_safe_consensus_summary();
  expect_raw_length_rejected();
  expect_overflowing_lexicon_numbers_use_defaults();
  expect_nonfinite_model_rejected();
  expect_mhknm01_loads_and_decodes();
  expect_malformed_mhknm01_rejected();
  expect_multi_key_input_rejects_single_key_edges();
  expect_incremental_extension_matches_full_rebuild();
  expect_intermediate_prune_propagates_truncation();
  expect_invalid_input_clears_incremental_state();
  expect_include_early_is_part_of_decode_cache_identity();
  expect_final_candidates_include_pathmaps();
  expect_personal_overlay_replacement_and_internal_edges();
  expect_personal_overlay_large_payload_is_accepted();
  expect_stale_engine_handles_are_rejected();
  expect_null_api_rejected();

  // Legacy: a negative unigram count previously wrapped to a huge size_t.
  std::vector<uint8_t> negative_count(64, 0);
  std::memcpy(negative_count.data(), "TCSKNM01", 8);
  put_u32(&negative_count, 8, 1);
  put_u32(&negative_count, 12, UINT32_MAX);
  expect_create_rejects_without_signal(negative_count, "legacy-negative-count");

  // Mobile: a formally ordered but out-of-file unigram offset must be rejected.
  std::vector<uint8_t> bad_offset(104, 0);
  std::memcpy(bad_offset.data(), "TCSKNM02", 8);
  put_u32(&bad_offset, 8, 1);       // version
  put_u32(&bad_offset, 12, 104);    // header size
  put_u64(&bad_offset, 16, bad_offset.size());
  put_u32(&bad_offset, 24, 64);     // index stride
  put_u32(&bad_offset, 32, 1);       // unigram count
  put_u32(&bad_offset, 40, 4096);    // unigram offset outside file
  put_u32(&bad_offset, 44, 0);       // reserved/alignment
  put_u32(&bad_offset, 48, 0);       // bi context count
  put_u32(&bad_offset, 52, 0);       // bi index count
  put_u64(&bad_offset, 56, 64);      // bi blocks
  put_u64(&bad_offset, 64, 64);      // bi index
  put_u32(&bad_offset, 72, 0);       // tri context count
  put_u32(&bad_offset, 80, 0);       // tri index count
  put_u64(&bad_offset, 84, 80);      // tri blocks
  put_u64(&bad_offset, 92, 96);      // tri index
  expect_create_rejects_without_signal(bad_offset, "mobile-bad-offset");

  // Mobile: a page successor count that runs past the block section is rejected at create time.
  std::vector<uint8_t> bad_successor(256, 0);
  std::memcpy(bad_successor.data(), "TCSKNM02", 8);
  put_u32(&bad_successor, 8, 1);
  put_u32(&bad_successor, 12, 104);
  put_u64(&bad_successor, 16, bad_successor.size());
  put_u32(&bad_successor, 24, 64);
  put_u32(&bad_successor, 32, 1);
  put_u32(&bad_successor, 40, 104);
  put_u32(&bad_successor, 48, 1);
  put_u32(&bad_successor, 52, 1);
  put_u64(&bad_successor, 56, 120);
  put_u64(&bad_successor, 64, 152);
  put_u32(&bad_successor, 72, 0);
  put_u32(&bad_successor, 80, 0);
  put_u64(&bad_successor, 88, 168);
  put_u64(&bad_successor, 96, 208);
  put_u64(&bad_successor, 104, 1);  // unigram key/logp bytes
  put_u64(&bad_successor, 120, 0);  // context key
  put_u32(&bad_successor, 128, 0);  // lambda bits
  put_u32(&bad_successor, 132, UINT32_MAX);
  put_u64(&bad_successor, 152, 0);  // index key
  put_u64(&bad_successor, 160, 120);  // page offset
  expect_create_rejects_without_signal(bad_successor, "mobile-bad-successor");

  std::puts("tigerengine safety tests passed");
  return 0;
}
