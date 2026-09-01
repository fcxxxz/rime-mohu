// Decode latency benchmark for the native sentence engine.
//
// Usage:
//   bench_decode <model.bin> <lexicon.txt> [beam] [all_ranks] [iterations] [personal_rows]
//
// Reports per-input P50/P95/P99 decode latency and an incremental typing
// pass that mirrors real keystroke sequences.  Not part of `make test`;
// run manually against an installed model when tuning latency work.

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include "tigerengine.h"

namespace {

constexpr int kOutputCapacity = 1 << 20;

double percentile(std::vector<double>& samples, double p) {
  if (samples.empty()) return 0.0;
  std::sort(samples.begin(), samples.end());
  const double index = p * static_cast<double>(samples.size() - 1);
  const size_t lo = static_cast<size_t>(index);
  const size_t hi = std::min(lo + 1, samples.size() - 1);
  const double frac = index - static_cast<double>(lo);
  return samples[lo] * (1.0 - frac) + samples[hi] * frac;
}

void report(const char* label, std::vector<double> samples) {
  if (samples.empty()) {
    printf("%-38s n=0 (no samples)\n", label);
    return;
  }
  printf("%-38s n=%3zu  P50=%7.3f  P95=%7.3f  P99=%7.3f  max=%7.3f ms\n",
         label, samples.size(), percentile(samples, 0.50),
         percentile(samples, 0.95), percentile(samples, 0.99),
         samples.back());
}

int parse_int_arg(const char* text, int fallback) {
  if (text == nullptr || *text == '\0') return fallback;
  char* end = nullptr;
  const long value = std::strtol(text, &end, 10);
  if (end == text || *end != '\0' || value < 0 || value > 1000000000L) {
    return fallback;
  }
  return static_cast<int>(value);
}

// Synthetic personal rows for snapshot-cost measurement.  Codes and texts are
// unique so the engine accepts every row exactly once.  Generates rows for
// indices [first, first + count).
std::string build_personal_rows(int first, int count) {
  static const std::string words[] = {
      "晴跟打练习软件", "手拿把掐很稳", "大模型重排序", "候选窗口渲染",
      "双拼整句解码", "个人词快照", "词组内部边", "束搜索剪枝",
  };
  std::string payload;
  for (int i = first; i < first + count; ++i) {
    const std::string code = std::string(1, static_cast<char>('a' + (i / 17576) % 26)) +
                             static_cast<char>('a' + (i / 676) % 26) +
                             static_cast<char>('a' + (i / 26) % 26) +
                             static_cast<char>('a' + i % 26) + "qy";
    payload += code + "\t" + words[i % 8] + std::to_string(i) + "\t" +
               std::to_string(1 + (i % 97)) + "\n";
  }
  return payload;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3) {
    std::cerr << "usage: bench_decode <model.bin> <lexicon.txt> "
                 "[beam=200] [all_ranks=1] [iterations=50] [personal_rows=0]\n";
    return 2;
  }
  const char* model = argv[1];
  const char* lexicon = argv[2];
  const int beam = argc > 3 ? parse_int_arg(argv[3], 200) : 200;
  const int all_ranks = argc > 4 ? parse_int_arg(argv[4], 1) : 1;
  const int iterations = argc > 5 ? parse_int_arg(argv[5], 50) : 50;
  const int personal_rows = argc > 6 ? parse_int_arg(argv[6], 0) : 0;
  if (iterations <= 0) {
    std::cerr << "iterations must be positive\n";
    return 2;
  }

  char error[512] = {};
  const int handle = tiger_engine_create(model, lexicon, beam, all_ranks,
                                         error, sizeof(error));
  if (handle < 0) {
    std::cerr << "engine create failed: "
              << (error[0] ? std::string(error) : std::string(tiger_last_error()))
              << "\n";
    return 1;
  }

  if (personal_rows > 0) {
    const std::string base = build_personal_rows(0, personal_rows);
    const std::string grown = base + build_personal_rows(personal_rows, 200);
    std::vector<double> full, noop, growth, shrink;
    auto timed_apply = [&](const std::string& payload, std::vector<double>& into) {
      const auto start = std::chrono::steady_clock::now();
      if (tiger_engine_set_personal_lexicon(handle, payload.c_str()) != 0) {
        std::cerr << "personal lexicon injection failed: "
                  << tiger_last_error() << "\n";
        tiger_engine_free(handle);
        exit(1);
      }
      into.push_back(std::chrono::duration<double, std::milli>(
                         std::chrono::steady_clock::now() - start)
                         .count());
    };
    timed_apply(base, full);      // 首次应用（增量路径的冷启动）
    timed_apply(base, noop);      // 无增长 no-op
    timed_apply(base, noop);
    timed_apply(grown, growth);   // 纯增长 +200 行
    timed_apply(base, shrink);    // 收缩 → 全量重建回退
    report("set_personal full apply", full);
    report("set_personal no-op", noop);
    report("set_personal growth +200", growth);
    report("set_personal shrink rebuild", shrink);

    // 事务路径：append 按整行块分片（512 行/块），commit 原子切换。
    // 逐块计时 append，单独计时 commit。
    auto txn_bench = [&](const std::string& payload, const char* label) {
      std::vector<std::string> chunks;
      {
        size_t start = 0, rows = 0;
        std::string chunk;
        while (start < payload.size()) {
          const size_t nl = payload.find('\n', start);
          chunk.append(payload, start, nl - start + 1);
          start = nl + 1;
          if (++rows >= 512) {
            chunks.push_back(chunk);
            chunk.clear();
            rows = 0;
          }
        }
        if (!chunk.empty()) chunks.push_back(chunk);
      }
      if (tiger_engine_personal_begin(handle) != 0) {
        std::cerr << "txn begin failed: " << tiger_last_error() << "\n";
        tiger_engine_free(handle);
        exit(1);
      }
      double append_total = 0.0, worst_chunk = 0.0;
      for (const std::string& chunk : chunks) {
        const auto a0 = std::chrono::steady_clock::now();
        if (tiger_engine_personal_append(handle, chunk.c_str()) != 0) {
          std::cerr << "txn append failed: " << tiger_last_error() << "\n";
          tiger_engine_free(handle);
          exit(1);
        }
        const double d = std::chrono::duration<double, std::milli>(
                             std::chrono::steady_clock::now() - a0)
                             .count();
        append_total += d;
        if (d > worst_chunk) worst_chunk = d;
      }
      const auto c0 = std::chrono::steady_clock::now();
      if (tiger_engine_personal_commit(handle) != 0) {
        std::cerr << "txn commit failed: " << tiger_last_error() << "\n";
        tiger_engine_free(handle);
        exit(1);
      }
      const double commit_ms = std::chrono::duration<double, std::milli>(
                                    std::chrono::steady_clock::now() - c0)
                                    .count();
      printf("%-38s n=%3zu  append_total=%8.2f  worst_chunk=%6.3f  commit=%8.2f ms\n",
             label, chunks.size(), append_total, worst_chunk, commit_ms);
    };
    txn_bench(base, "txn commit no-change");
    txn_bench(grown, "txn commit growth (+200)");
    txn_bench(base, "txn commit shrink rebuild");
  }

  static const char* kRaws[] = {
      "najqmz",                // 6 keys
      "najqmzuf",              // 8
      "najqmzufme",            // 10
      "najqmzufmeke",          // 12
      "najqmzufmekeyb",        // 14
      "yiyjjqkjiuuisbgb",      // 16
      "nibuykzljyufnzhkle",    // 18
      "najqmzufmekeybyudele",  // 20
  };

  std::vector<char> output(kOutputCapacity, 0);
  for (const char* raw : kRaws) {
    // Warm caches; the first decode on a new shape pays one-time costs.
    for (int i = 0; i < 3; ++i) {
      double ms = 0;
      tiger_decode(handle, raw, 0, output.data(), kOutputCapacity, &ms);
    }
    std::vector<double> samples;
    samples.reserve(iterations);
    for (int i = 0; i < iterations; ++i) {
      double ms = 0;
      const int rc = tiger_decode(handle, raw, 0, output.data(),
                                  kOutputCapacity, &ms);
      if (rc <= 0) {
        std::cerr << "decode failed for " << raw << ": " << tiger_last_error()
                  << "\n";
        tiger_engine_free(handle);
        return 1;
      }
      samples.push_back(ms);
    }
    report(("decode len=" + std::to_string(std::strlen(raw))).c_str(), samples);
  }

  // Incremental typing: decode each prefix once per round, mirroring the
  // per-keystroke latency a user actually experiences.
  const std::string full = "najqmzufmekeybyudele";
  const int rounds = std::max(3, iterations / 5);
  std::vector<std::vector<double>> steps(full.size() / 2);
  for (int r = 0; r < rounds; ++r) {
    for (size_t len = 6; len <= full.size(); len += 2) {
      double ms = 0;
      const std::string prefix = full.substr(0, len);
      tiger_decode(handle, prefix.c_str(), 0, output.data(), kOutputCapacity,
                   &ms);
      steps[(len - 6) / 2].push_back(ms);
    }
  }
  std::cout << "\nincremental typing on \"" << full << "\" (" << rounds
            << " rounds, per-step stats):\n";
  for (size_t i = 0; i < steps.size(); ++i) {
    std::vector<double>& s = steps[i];
    report(("step len=" + std::to_string(6 + i * 2)).c_str(), s);
  }

  tiger_engine_free(handle);
  std::cout << "\nbench complete\n";
  return 0;
}
