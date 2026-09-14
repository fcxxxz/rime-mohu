#pragma once

#include <onnxruntime_cxx_api.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace mohu::semantic {

constexpr int64_t kContextChars = 96;
constexpr int64_t kCandidateChars = 16;
constexpr int64_t kMaxCandidates = 20;
constexpr int64_t kPad = 0;
constexpr int64_t kUnk = 1;

inline bool next_utf8(const std::string& text, size_t* offset,
                      std::string* token) {
  if (!offset || !token || *offset >= text.size()) return false;
  const unsigned char lead = static_cast<unsigned char>(text[*offset]);
  size_t width = 1;
  if ((lead & 0xE0) == 0xC0) width = 2;
  else if ((lead & 0xF0) == 0xE0) width = 3;
  else if ((lead & 0xF8) == 0xF0) width = 4;
  if (*offset + width > text.size()) return false;
  for (size_t i = 1; i < width; ++i) {
    const unsigned char byte = static_cast<unsigned char>(text[*offset + i]);
    if ((byte & 0xC0) != 0x80) return false;
  }
  *token = text.substr(*offset, width);
  *offset += width;
  return true;
}

class Scorer {
 public:
  bool load(const std::string& model_path, const std::string& vocab_path,
            std::string* error) {
    try {
      if (!load_vocab(vocab_path, error)) return false;
      env_ = std::make_unique<Ort::Env>(ORT_LOGGING_LEVEL_ERROR,
                                        "mohu-semantic");
      Ort::SessionOptions options;
      options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
      // 语义推理在 filter 阶段（解码之后）短时运行，与 beam 解码不并发
      // 争用；单线程会把 C3（K=5）推到 ~32ms，双线程实测 ~18ms。
      // MOHU_SEMANTIC_THREADS 可覆盖（1=旧行为）。
      int threads = 2;
      if (const char* env_threads = getenv("MOHU_SEMANTIC_THREADS")) {
        int value = atoi(env_threads);
        if (value >= 1 && value <= 8) threads = value;
      }
      options.SetIntraOpNumThreads(threads);
      options.SetInterOpNumThreads(1);
      session_ = std::make_unique<Ort::Session>(*env_, model_path.c_str(), options);
      return true;
    } catch (const Ort::Exception& ex) {
      if (error) *error = ex.what();
      session_.reset();
      env_.reset();
      return false;
    }
  }

  bool loaded() const { return session_ != nullptr; }

  std::vector<float> score(const std::string& context,
                           const std::vector<std::string>& candidates,
                           const std::vector<double>& native_scores,
                           std::string* error) const {
    if (!session_ || candidates.empty() || candidates.size() > kMaxCandidates ||
        candidates.size() != native_scores.size()) {
      if (error) *error = "semantic scorer input invalid";
      return {};
    }
    try {
      const int64_t count = static_cast<int64_t>(candidates.size());
      std::vector<int64_t> context_ids(kContextChars, kPad);
      const auto encoded_context = encode(context, kContextChars);
      std::copy(encoded_context.begin(), encoded_context.end(), context_ids.begin());

      std::vector<int64_t> candidate_ids(
          static_cast<size_t>(count * kCandidateChars), kPad);
      std::vector<uint8_t> candidate_mask(static_cast<size_t>(count), 1);
      std::vector<int64_t> native_rank(static_cast<size_t>(count));
      std::vector<float> normalized_score(static_cast<size_t>(count));
      std::vector<float> syllable_count(static_cast<size_t>(count));
      std::vector<float> char_len(static_cast<size_t>(count));
      std::vector<float> has_score(static_cast<size_t>(count), 1.0f);
      for (int64_t i = 0; i < count; ++i) {
        const auto ids = encode(candidates[static_cast<size_t>(i)],
                                kCandidateChars);
        std::copy(ids.begin(), ids.end(),
                  candidate_ids.begin() + i * kCandidateChars);
        native_rank[static_cast<size_t>(i)] = i + 1;
        normalized_score[static_cast<size_t>(i)] = static_cast<float>(
            (native_scores[static_cast<size_t>(i)] - score_mean_) / score_std_);
        char_len[static_cast<size_t>(i)] =
            static_cast<float>(codepoint_count(candidates[static_cast<size_t>(i)]));
        syllable_count[static_cast<size_t>(i)] =
            std::max(1.0f, std::floor(char_len[static_cast<size_t>(i)] / 2.0f));
      }

      Ort::MemoryInfo memory = Ort::MemoryInfo::CreateCpu(
          OrtArenaAllocator, OrtMemTypeDefault);
      std::array<int64_t, 2> context_shape{1, kContextChars};
      std::array<int64_t, 3> candidate_shape{1, count, kCandidateChars};
      std::array<int64_t, 2> feature_shape{1, count};
      std::vector<Ort::Value> inputs;
      inputs.reserve(8);
      inputs.push_back(Ort::Value::CreateTensor<int64_t>(
          memory, context_ids.data(), context_ids.size(), context_shape.data(), 2));
      inputs.push_back(Ort::Value::CreateTensor<int64_t>(
          memory, candidate_ids.data(), candidate_ids.size(), candidate_shape.data(), 3));
      inputs.push_back(Ort::Value::CreateTensor<bool>(
          memory, reinterpret_cast<bool*>(candidate_mask.data()),
          candidate_mask.size(), feature_shape.data(), 2));
      inputs.push_back(Ort::Value::CreateTensor<int64_t>(
          memory, native_rank.data(), native_rank.size(), feature_shape.data(), 2));
      inputs.push_back(Ort::Value::CreateTensor<float>(
          memory, normalized_score.data(), normalized_score.size(),
          feature_shape.data(), 2));
      inputs.push_back(Ort::Value::CreateTensor<float>(
          memory, syllable_count.data(), syllable_count.size(),
          feature_shape.data(), 2));
      inputs.push_back(Ort::Value::CreateTensor<float>(
          memory, char_len.data(), char_len.size(), feature_shape.data(), 2));
      inputs.push_back(Ort::Value::CreateTensor<float>(
          memory, has_score.data(), has_score.size(), feature_shape.data(), 2));

      static constexpr const char* kInputs[] = {
          "context_ids", "candidate_ids", "candidate_mask", "native_rank",
          "native_score", "syllable_count", "char_len", "has_score"};
      static constexpr const char* kOutputs[] = {"scores"};
      auto outputs = session_->Run(Ort::RunOptions{nullptr}, kInputs,
                                   inputs.data(), inputs.size(), kOutputs, 1);
      const float* values = outputs[0].GetTensorData<float>();
      std::vector<float> result(values, values + count);
      if (!std::all_of(result.begin(), result.end(),
                       [](float value) { return std::isfinite(value); })) {
        if (error) *error = "semantic scorer returned non-finite scores";
        return {};
      }
      return result;
    } catch (const Ort::Exception& ex) {
      if (error) *error = ex.what();
      return {};
    }
  }

 private:
  bool load_vocab(const std::string& path, std::string* error) {
    std::ifstream stream(path);
    if (!stream) {
      if (error) *error = "semantic vocabulary open failed";
      return false;
    }
    std::string line;
    if (!std::getline(stream, line)) {
      if (error) *error = "semantic vocabulary header missing";
      return false;
    }
    std::istringstream header(line);
    std::string magic;
    if (!std::getline(header, magic, '\t') ||
        magic != "MOHU_SEMANTIC_VOCAB_V1" ||
        !(header >> score_mean_) || header.get() != '\t' ||
        !(header >> score_std_) || !std::isfinite(score_mean_) ||
        !std::isfinite(score_std_) || score_std_ <= 0.0) {
      if (error) *error = "semantic vocabulary header invalid";
      return false;
    }
    vocab_.clear();
    while (std::getline(stream, line)) {
      const size_t tab = line.rfind('\t');
      if (tab == std::string::npos || tab == 0 || tab + 1 >= line.size()) continue;
      char* end = nullptr;
      const long value = std::strtol(line.c_str() + tab + 1, &end, 10);
      if (!end || *end != '\0' || value < 0) continue;
      vocab_[line.substr(0, tab)] = static_cast<int64_t>(value);
    }
    if (vocab_.empty()) {
      if (error) *error = "semantic vocabulary empty";
      return false;
    }
    return true;
  }

  std::vector<int64_t> encode(const std::string& text, int64_t limit) const {
    std::vector<int64_t> ids;
    ids.reserve(static_cast<size_t>(limit));
    size_t offset = 0;
    while (offset < text.size() && ids.size() < static_cast<size_t>(limit)) {
      std::string token;
      if (!next_utf8(text, &offset, &token)) break;
      const auto found = vocab_.find(token);
      ids.push_back(found == vocab_.end() ? kUnk : found->second);
    }
    return ids;
  }

  static int64_t codepoint_count(const std::string& text) {
    int64_t count = 0;
    size_t offset = 0;
    while (offset < text.size()) {
      std::string token;
      if (!next_utf8(text, &offset, &token)) break;
      ++count;
    }
    return count;
  }

  std::unique_ptr<Ort::Env> env_;
  std::unique_ptr<Ort::Session> session_;
  std::unordered_map<std::string, int64_t> vocab_;
  double score_mean_ = 0.0;
  double score_std_ = 1.0;
};

}  // namespace mohu::semantic
