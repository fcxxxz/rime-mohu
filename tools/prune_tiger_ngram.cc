// prune_tiger_ngram.cc — 剪枝 TCSKNM02 字符三元模型（魔虎 flash）
//
// 输入输出均为 TCSKNM02（tigerengine.cc load_mobile 直接可读），引擎零改动：
//   - 按后继概率阈值（tau）、相对回退优势（ratio）、每上下文条数上限（topk）
//     过滤 bigram / trigram 后继；
//   - 被剪后继的概率质量并回上下文 λ（λ' = 1 − Σ_kept p，与引擎
//     P = tri.prob + λ_tri × (bi.prob + λ_bi × uni) 的回退公式严格配套）；
//   - 后继全空的上下文整条删除；页索引（stride 64）按剪后记录重建。
//
// ratio 判据（Stolcke 剪枝的局部近似）：保留后继当且仅当
//     p ≥ ratio × (λ + p) × P_backoff(c)
// 其中 trigram 的 P_backoff 用剪枝后的 bigram 计算（与运行时一致），
// bigram 的 P_backoff = P_uni(c)。ratio=1 表示「不劣于把质量摊回回退」。
//
// 用法:
//   prune_tiger_ngram <in.bin> --stats
//   prune_tiger_ngram <in.bin> <out.bin> [--bi-tau F] [--bi-ratio F] [--bi-topk N]
//                                      [--tri-tau F] [--tri-ratio F] [--tri-topk N]
//                                      [--tri-ctx-mass F] [--bi-ctx-mass F] [--no-addback]
//                                      [--tri-floor N] [--bi-floor N] [--format f16]
//   ctx-mass：上下文全部后继概率和低于阈值时整条删除（≈纯回退上下文）；
//   floor：每上下文按概率无条件保留的头部条数，tau/ratio 只作用于其后尾部；
//   --quantize-f16：全部概率舍入到 f16 精度（仍按 f32 存储——量化影响实验；
//   真正的 f16 存储格式需要引擎改造）；
//   --quantize-mant N：后继概率舍入到 f16 尾数 N 位（N=10 即 f16，N=2 ≈ e5m2
//   有效位；量化影响模拟，体积不变）；
//   --quantize-log8 LO：后继概率按 [LO,0] 自然对数网格 256 级舍入——模拟
//   1 字节对数存储的精度影响（体积不变）；
//   --format f16：输出 TCSKNM03（后继概率以 f16 存储，6B/条，体积 -25%；
//   unigram/λ 仍为 f32；需要 2026-09-16 起 libtigerengine 支持 03 的版本）；
//   --format u8：输出 TCSKNM04（后继概率以 u8 对数码存储，5B/条，体积
//   -37.5%；p = exp(-14 + k×14/255)，均匀 ±2.8% 相对误差，模拟与全量
//   验证结论见 docs/reports/2026-09-16-v5-f16-quantize.md §7；unigram/λ
//   仍为 f32；需要支持 04 的引擎）；
//   --no-addback：被剪质量直接丢弃、λ 原样保留（对照实验用）。

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace {

constexpr uint64_t kShift = 2097152;  // 2^21
constexpr uint32_t kStride = 64;

inline uint32_t rd_u32(const std::vector<uint8_t>& d, uint64_t off) {
  uint32_t v;
  std::memcpy(&v, d.data() + off, 4);
  return v;
}
inline uint64_t rd_u64(const std::vector<uint8_t>& d, uint64_t off) {
  uint64_t v;
  std::memcpy(&v, d.data() + off, 8);
  return v;
}
inline float rd_f32(const std::vector<uint8_t>& d, uint64_t off) {
  float v;
  std::memcpy(&v, d.data() + off, 4);
  return v;
}
inline int32_t rd_i32(const std::vector<uint8_t>& d, uint64_t off) {
  int32_t v;
  std::memcpy(&v, d.data() + off, 4);
  return v;
}

void append_u32(std::vector<uint8_t>& out, uint32_t v) {
  for (int i = 0; i < 4; ++i) out.push_back((v >> (8 * i)) & 0xFF);
}
void append_u16(std::vector<uint8_t>& out, uint16_t v) {
  for (int i = 0; i < 2; ++i) out.push_back((v >> (8 * i)) & 0xFF);
}
void append_u8(std::vector<uint8_t>& out, uint8_t v) { out.push_back(v); }
void append_u64(std::vector<uint8_t>& out, uint64_t v) {
  for (int i = 0; i < 8; ++i) out.push_back((v >> (8 * i)) & 0xFF);
}
void append_f32(std::vector<uint8_t>& out, float v) {
  uint32_t bits;
  std::memcpy(&bits, &v, 4);
  append_u32(out, bits);
}

// %.3g 风格的紧凑数值输出（日志用）
std::string g3(double v) {
  std::ostringstream os;
  os << std::setprecision(3) << v;
  return os.str();
}

// 把 f32 舍入到 f16 精度再返回（量化实验用；概率值域 [0,1] 安全）。
// 依赖编译器 _Float16（Apple arm64 / x86 AVX512-FP16 均支持）。
inline float roundtrip_f16(float v) {
#if defined(__FLT16_MAX__)
  return static_cast<float>(static_cast<_Float16>(v));
#else
  (void)v;
  std::cerr << "--quantize-f16 needs _Float16 support\n";
  std::exit(2);
#endif
}

// 量化实验：f16 指数域 + 只保留 bits 位尾数（round-to-nearest，双精度
// 中间量）。bits=10 即 f16 本身；bits=2 等价 e5m2 的有效位。次正规段
// 网格步长取 2^(-14-bits)，与正规段在 2^-14 处衔接。
inline float quant_mant(float v, int bits) {
  const double x = static_cast<double>(v);
  if (x <= 0) return v;
  int e = 0;
  std::frexp(x, &e);                     // x ∈ [2^(e-1), 2^e)
  double step = std::ldexp(1.0, (e - 1) - bits);
  const double floor_step = std::ldexp(1.0, -14 - bits);
  if (step < floor_step) step = floor_step;
  double q = std::floor(x / step + 0.5) * step;
  if (q > 65504.0) q = 65504.0;
  return static_cast<float>(q);
}

// 量化实验：概率按 [lo, 0]（自然对数）均匀网格舍入到 256 级，即
// 1 字节对数存储的模拟；相对误差均匀 ≈ e^(q/2)−1。
inline float quant_log8(float v, double lo) {
  const double x = static_cast<double>(v);
  if (x <= 0) return v;
  double lv = std::log(x);
  if (lv < lo) lv = lo;
  const double q = (0.0 - lo) / 255.0;
  long k = std::lround((lv - lo) / q);
  if (k < 0) k = 0;
  if (k > 255) k = 255;
  return static_cast<float>(std::exp(lo + k * q));
}

inline uint16_t f16_bits(float v) {
#if defined(__FLT16_MAX__)
  const _Float16 h = static_cast<_Float16>(v);
  uint16_t bits;
  std::memcpy(&bits, &h, 2);
  return bits;
#else
  (void)v;
  std::cerr << "--format f16 needs _Float16 support\n";
  std::exit(2);
#endif
}

// TCSKNM04 的概率码：p = exp(-14 + k×14/255)（与引擎 succ_prob_table 及
// quant_log8(v, -14) 的取值路径逐位一致——都经 f32 舍入）。
inline uint8_t log8_code(float v) {
  const double x = static_cast<double>(v);
  const double lo = -14.0;
  const double q = 14.0 / 255.0;
  double lv = std::log(x > 0 ? x : std::exp(lo));
  if (lv < lo) lv = lo;
  long k = std::lround((lv - lo) / q);
  if (k < 0) k = 0;
  if (k > 255) k = 255;
  return static_cast<uint8_t>(k);
}

struct Succ {
  uint32_t target;
  float prob;
};

struct CtxRecord {
  uint64_t key;
  float lambda_ = 0;
  std::vector<Succ> succ;  // 按 target 升序
};

struct Options {
  bool stats_only = false;
  bool addback = true;  // 剪掉的质量是否加回 λ（--no-addback 关闭）
  bool quantize_f16 = false;  // 概率舍入到 f16 精度（仍按 f32 存储，量化实验用）
  bool format_f16 = false;    // 输出 TCSKNM03（后继概率 f16，6B/条，-25%）
  bool format_u8 = false;     // 输出 TCSKNM04（后继概率 u8 对数码，5B/条，-37.5%）
  int succ_quant_mant = 0;    // >0：后继概率舍入到 f16 尾数保留 bits 位（模拟更低精度）
  double succ_quant_log8_lo = 0;  // <0：后继概率按 [lo,0] ln 网格 256 级舍入（模拟 1 字节）
  double bi_tau = 0, tri_tau = 0;
  double bi_ratio = 0, tri_ratio = 0;  // 0 = 关闭
  double bi_ctx_mass = 0, tri_ctx_mass = 0;  // 上下文直接质量和下限
  long bi_topk = 0, tri_topk = 0;      // 0 = 不限
  long bi_floor = 0, tri_floor = 0;    // tau/ratio 之外无条件保留的头部条数
};

struct Sections {
  uint32_t uni_count = 0;
  uint64_t uni_off = 0;
  uint64_t bi_ctx = 0, bi_blocks_off = 0, bi_index_off = 0;
  uint64_t tri_ctx = 0, tri_blocks_off = 0, tri_index_off = 0;
};

bool parse_header(const std::vector<uint8_t>& d, Sections& s, std::string& err) {
  if (d.size() < 104 || std::memcmp(d.data(), "TCSKNM02", 8) != 0) {
    err = "not a TCSKNM02 file";
    return false;
  }
  uint64_t p = 8;
  const uint32_t version = rd_u32(d, p); p += 4;
  const uint32_t header_size = rd_u32(d, p); p += 4;
  const uint64_t file_size = rd_u64(d, p); p += 8;
  const uint32_t stride = rd_u32(d, p); p += 4;
  p += 4;                       // reserved
  s.uni_count = rd_u32(d, p); p += 4;
  p += 4;                       // reserved
  s.uni_off = rd_u32(d, p); p += 4;
  p += 4;                       // reserved
  s.bi_ctx = rd_u32(d, p); p += 4;
  p += 4;                       // bi_index_count
  s.bi_blocks_off = rd_u64(d, p); p += 8;
  s.bi_index_off = rd_u64(d, p); p += 8;
  s.tri_ctx = rd_u32(d, p); p += 4;
  p += 4;                       // reserved
  p += 4;                       // tri_index_count
  p += 4;                       // reserved
  s.tri_blocks_off = rd_u64(d, p); p += 8;
  s.tri_index_off = rd_u64(d, p); p += 8;
  if (p != 104) {
    err = "header walk mismatch";
    return false;
  }
  if (version != 1 || header_size != 104 || file_size != d.size() ||
      stride != kStride || s.uni_count == 0 || s.uni_off < 104 ||
      s.bi_blocks_off < s.uni_off || s.bi_index_off < s.bi_blocks_off ||
      s.tri_blocks_off < s.bi_index_off || s.tri_index_off < s.tri_blocks_off ||
      s.tri_index_off > d.size()) {
    err = "invalid TCSKNM02 header";
    return false;
  }
  return true;
}

// 顺序走读 blocks 段（记录在段内背靠背连续，与页划分无关）
bool walk_section(const std::vector<uint8_t>& d, uint64_t begin, uint64_t end,
                  uint64_t expect_ctx, std::vector<CtxRecord>& out,
                  std::string& err) {
  uint64_t pos = begin;
  while (pos + 16 <= end) {
    CtxRecord r;
    r.key = rd_u64(d, pos);
    r.lambda_ = rd_f32(d, pos + 8);
    const int32_t n = rd_i32(d, pos + 12);
    if (n < 0 || pos + 16 + static_cast<uint64_t>(n) * 8 > end) {
      err = "corrupt successor record";
      return false;
    }
    r.succ.resize(n);
    for (int32_t i = 0; i < n; ++i) {
      const uint64_t at = pos + 16 + static_cast<uint64_t>(i) * 8;
      r.succ[i].target = rd_u32(d, at);
      r.succ[i].prob = rd_f32(d, at + 4);
    }
    pos += 16 + static_cast<uint64_t>(n) * 8;
    out.push_back(std::move(r));
  }
  if (pos != end || out.size() != expect_ctx) {
    err = "section walk mismatch";
    return false;
  }
  return true;
}

double uni_prob(const std::vector<float>& uni, uint32_t cp) {
  return cp < kShift ? static_cast<double>(uni[cp]) : static_cast<double>(uni[0]);
}

// 剪枝核心：tau / ratio / topk / floor。返回保留的后继表（仍按 target 升序），
// 并通过 dropped_mass 累加被剪后继的概率。floor：每上下文按概率保留的头部条数，
// 头部成员不受 tau/ratio 影响（topk 与 floor 同时给出时 topk 必须 ≥ floor）。
// λ 不在这里改写——V5 的 λ 是插值权重而非 1−Σp（实测存在 n=1、Σp=0.325、
// λ=1.0 的记录），调用方必须原样保留未剪上下文的 λ，仅对剪过的上下文做
// λ' = λ + Σ_dropped。
std::vector<Succ> prune_record(const CtxRecord& r, double tau, double ratio,
                               long topk, long floor, double lambda_now,
                               const std::function<double(uint32_t)>& backoff,
                               double* dropped_mass) {
  const size_t n = r.succ.size();
  double protect_p = 0.0;
  if (floor > 0) {
    if (static_cast<size_t>(floor) >= n) {
      protect_p = std::numeric_limits<double>::infinity();
    } else {
      std::vector<float> ps;
      ps.reserve(n);
      for (const Succ& s : r.succ) ps.push_back(s.prob);
      std::nth_element(ps.begin(), ps.begin() + (floor - 1), ps.end(),
                       std::greater<float>());
      protect_p = ps[floor - 1];
    }
  }
  std::vector<uint8_t> keep(n, 1);
  size_t kept = n;
  if (tau > 0) {
    for (size_t i = 0; i < n; ++i) {
      const double p = static_cast<double>(r.succ[i].prob);
      if (p < tau && p < protect_p) {
        keep[i] = 0;
        *dropped_mass += p;
        --kept;
      }
    }
  }
  if (ratio > 0) {
    for (size_t i = 0; i < n; ++i) {
      if (!keep[i]) continue;
      const double p = static_cast<double>(r.succ[i].prob);
      const double lambda_after = lambda_now + p;  // 本条剪掉后的近似 λ
      if (p < ratio * lambda_after * backoff(r.succ[i].target) && p < protect_p) {
        keep[i] = 0;
        *dropped_mass += p;
        --kept;
      }
    }
  }
  if (topk > 0 && kept > static_cast<size_t>(topk)) {
    // 按 (prob 降序, target 升序) 选前 topk
    std::vector<uint32_t> order(n);
    for (size_t i = 0; i < n; ++i) order[i] = static_cast<uint32_t>(i);
    std::partial_sort(order.begin(), order.begin() + topk, order.end(),
                      [&](uint32_t x, uint32_t y) {
                        const float px = r.succ[x].prob, py = r.succ[y].prob;
                        if (px != py) return px > py;
                        return r.succ[x].target < r.succ[y].target;
                      });
    std::vector<uint8_t> top_keep(n, 0);
    for (long i = 0; i < topk; ++i) top_keep[order[i]] = 1;
    for (size_t i = 0; i < n; ++i) {
      if (keep[i] && !top_keep[i]) {
        keep[i] = 0;
        *dropped_mass += static_cast<double>(r.succ[i].prob);
        --kept;
      }
    }
  }
  std::vector<Succ> out;
  out.reserve(kept);
  for (size_t i = 0; i < n; ++i) {
    if (keep[i]) out.push_back(r.succ[i]);
  }
  return out;
}

void report_stats(const char* name, const std::vector<CtxRecord>& ctxs) {
  std::vector<double> probs;
  probs.reserve(1 << 22);
  std::vector<size_t> sizes;
  sizes.reserve(ctxs.size());
  for (const auto& r : ctxs) {
    sizes.push_back(r.succ.size());
    for (const Succ& s : r.succ) probs.push_back(static_cast<double>(s.prob));
  }
  std::sort(probs.begin(), probs.end());
  std::sort(sizes.begin(), sizes.end());
  auto pct = [](const std::vector<double>& v, double q) {
    return v.empty() ? 0.0 : v[static_cast<size_t>(q * (v.size() - 1))];
  };
  auto spct = [](const std::vector<size_t>& v, double q) {
    return v.empty() ? 0 : v[static_cast<size_t>(q * (v.size() - 1))];
  };
  std::cerr << name << ": succ prob p50=" << g3(pct(probs, 0.50))
            << " p90=" << g3(pct(probs, 0.90))
            << " p99=" << g3(pct(probs, 0.99))
            << " p99.9=" << g3(pct(probs, 0.999))
            << " min=" << g3(probs.empty() ? 0.0 : probs.front())
            << " | ctx size p50=" << spct(sizes, 0.50)
            << " p90=" << spct(sizes, 0.90)
            << " p99=" << spct(sizes, 0.99)
            << " max=" << (sizes.empty() ? 0 : sizes.back()) << "\n";
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "usage: " << argv[0]
              << " <in.bin> [--stats | <out.bin> [options]]\n"
              << "  --bi-tau F --bi-ratio F --bi-topk N\n"
              << "  --tri-tau F --tri-ratio F --tri-topk N\n";
    return 2;
  }
  Options opt;
  const char* in_path = argv[1];
  const char* out_path = nullptr;
  for (int i = 2; i < argc; ++i) {
    const std::string a = argv[i];
    auto next_double = [&](double* out) -> bool {
      if (i + 1 >= argc) return false;
      char* endp = nullptr;
      const double v = std::strtod(argv[i + 1], &endp);
      if (endp == argv[i + 1] || *endp != '\0') return false;
      *out = v;
      ++i;
      return true;
    };
    double v = 0;
    if (a == "--stats") {
      opt.stats_only = true;
    } else if (a == "--no-addback") {
      opt.addback = false;
    } else if (a == "--quantize-f16") {
      opt.quantize_f16 = true;
    } else if (a == "--quantize-mant" && next_double(&v)) {
      opt.succ_quant_mant = static_cast<int>(v);
    } else if (a == "--quantize-log8" && next_double(&v)) {
      opt.succ_quant_log8_lo = v;
    } else if (a == "--format" && i + 1 < argc &&
               std::string(argv[i + 1]) == "f16") {
      opt.format_f16 = true;
      ++i;
    } else if (a == "--format" && i + 1 < argc &&
               std::string(argv[i + 1]) == "u8") {
      opt.format_u8 = true;
      ++i;
    } else if (a == "--bi-tau" && next_double(&v)) {
      opt.bi_tau = v;
    } else if (a == "--bi-ratio" && next_double(&v)) {
      opt.bi_ratio = v;
    } else if (a == "--bi-topk" && next_double(&v)) {
      opt.bi_topk = static_cast<long>(v);
    } else if (a == "--bi-ctx-mass" && next_double(&v)) {
      opt.bi_ctx_mass = v;
    } else if (a == "--tri-tau" && next_double(&v)) {
      opt.tri_tau = v;
    } else if (a == "--tri-ratio" && next_double(&v)) {
      opt.tri_ratio = v;
    } else if (a == "--tri-topk" && next_double(&v)) {
      opt.tri_topk = static_cast<long>(v);
    } else if (a == "--tri-floor" && next_double(&v)) {
      opt.tri_floor = static_cast<long>(v);
    } else if (a == "--bi-floor" && next_double(&v)) {
      opt.bi_floor = static_cast<long>(v);
    } else if (a == "--tri-ctx-mass" && next_double(&v)) {
      opt.tri_ctx_mass = v;
    } else if (!out_path && !a.empty() && a[0] != '-') {
      out_path = argv[i];
    } else {
      std::cerr << "bad arg " << a << "\n";
      return 2;
    }
  }
  if (!opt.stats_only && !out_path) {
    std::cerr << "need <out.bin> or --stats\n";
    return 2;
  }

  std::ifstream in(in_path, std::ios::binary);
  if (!in) {
    std::cerr << "cannot open " << in_path << "\n";
    return 3;
  }
  std::vector<uint8_t> data((std::istreambuf_iterator<char>(in)),
                            std::istreambuf_iterator<char>());
  if (data.empty()) {
    std::cerr << "empty input " << in_path << "\n";
    return 3;
  }

  Sections sec;
  std::string err;
  if (!parse_header(data, sec, err)) {
    std::cerr << err << "\n";
    return 4;
  }

  // ---- unigram 表（key=0 首条为 OOV 地板）----
  std::vector<float> uni(kShift, 0.0f);
  {
    const float floor_p = rd_f32(data, sec.uni_off + 4);
    for (uint64_t i = 0; i < sec.uni_count; ++i) {
      const uint64_t off = sec.uni_off + i * 8;
      const uint32_t key = rd_u32(data, off);
      if (key != 0 && key < kShift) uni[key] = rd_f32(data, off + 4);
    }
    for (uint64_t cp = 0; cp < kShift; ++cp) {
      if (uni[cp] <= 0) uni[cp] = floor_p;
    }
  }

  // ---- 读入 bigram / trigram 记录 ----
  std::vector<CtxRecord> bi_ctxs, tri_ctxs;
  if (!walk_section(data, sec.bi_blocks_off, sec.bi_index_off, sec.bi_ctx,
                    bi_ctxs, err) ||
      !walk_section(data, sec.tri_blocks_off, sec.tri_index_off, sec.tri_ctx,
                    tri_ctxs, err)) {
    std::cerr << err << "\n";
    return 4;
  }
  uint64_t bi_succ0 = 0, tri_succ0 = 0;
  for (const auto& r : bi_ctxs) bi_succ0 += r.succ.size();
  for (const auto& r : tri_ctxs) tri_succ0 += r.succ.size();
  std::cerr << "input: " << g3(data.size() / 1e6) << "MB uni=" << sec.uni_count
            << " bi_ctx=" << bi_ctxs.size() << " bi_succ=" << bi_succ0
            << " tri_ctx=" << tri_ctxs.size() << " tri_succ=" << tri_succ0
            << "\n";

  if (opt.stats_only) {
    report_stats("bi", bi_ctxs);
    report_stats("tri", tri_ctxs);
    return 0;
  }

  // ---- 剪 bigram（回退 = unigram）----
  double bi_dropped = 0;
  std::vector<CtxRecord> bi_out;
  bi_out.reserve(bi_ctxs.size());
  const auto uni_backoff = [&uni](uint32_t c) { return uni_prob(uni, c); };
  for (const auto& r : bi_ctxs) {
    if (opt.bi_ctx_mass > 0) {
      double mass = 0;
      for (const Succ& s : r.succ) mass += static_cast<double>(s.prob);
      if (mass < opt.bi_ctx_mass) continue;
    }
    CtxRecord o;
    o.key = r.key;
    const double dropped_before = bi_dropped;
    o.succ = prune_record(r, opt.bi_tau, opt.bi_ratio, opt.bi_topk,
                          opt.bi_floor, static_cast<double>(r.lambda_),
                          uni_backoff, &bi_dropped);
    if (o.succ.empty()) continue;
    o.lambda_ = r.lambda_;
    if (opt.addback && bi_dropped > dropped_before) {
      o.lambda_ = static_cast<float>(std::max(
          static_cast<double>(r.lambda_) + (bi_dropped - dropped_before), 0.0));
    }
    bi_out.push_back(std::move(o));
  }

  // ---- 剪 trigram（回退 = 剪后的 bigram，与运行时一致）----
  std::vector<int32_t> bi_slot(kShift, -1);
  for (size_t i = 0; i < bi_out.size(); ++i) {
    const uint32_t b = static_cast<uint32_t>(bi_out[i].key % kShift);
    if (b < kShift) bi_slot[b] = i;
  }
  const auto bi_backoff = [&bi_out, &bi_slot, &uni](uint32_t c) -> double {
    const int32_t slot = c < kShift ? bi_slot[c] : -1;
    if (slot < 0) return uni_prob(uni, c);
    const CtxRecord& r = bi_out[slot];
    int64_t lo = 0, hi = static_cast<int64_t>(r.succ.size());
    while (lo < hi) {
      const int64_t mid = lo + (hi - lo) / 2;
      if (r.succ[mid].target < c) lo = mid + 1; else hi = mid;
    }
    const double direct =
        (lo < static_cast<int64_t>(r.succ.size()) && r.succ[lo].target == c)
            ? static_cast<double>(r.succ[lo].prob)
            : 0.0;
    return direct + static_cast<double>(r.lambda_) * uni_prob(uni, c);
  };
  double tri_dropped = 0;
  std::vector<CtxRecord> tri_out;
  tri_out.reserve(tri_ctxs.size());
  for (const auto& r : tri_ctxs) {
    if (opt.tri_ctx_mass > 0) {
      double mass = 0;
      for (const Succ& s : r.succ) mass += static_cast<double>(s.prob);
      if (mass < opt.tri_ctx_mass) continue;
    }
    CtxRecord o;
    o.key = r.key;
    const double dropped_before = tri_dropped;
    o.succ = prune_record(r, opt.tri_tau, opt.tri_ratio, opt.tri_topk,
                          opt.tri_floor, static_cast<double>(r.lambda_),
                          bi_backoff, &tri_dropped);
    if (o.succ.empty()) continue;
    o.lambda_ = r.lambda_;
    if (opt.addback && tri_dropped > dropped_before) {
      o.lambda_ = static_cast<float>(std::max(
          static_cast<double>(r.lambda_) + (tri_dropped - dropped_before), 0.0));
    }
    tri_out.push_back(std::move(o));
  }
  uint64_t bi_succ1 = 0, tri_succ1 = 0;
  for (const auto& r : bi_out) bi_succ1 += r.succ.size();
  for (const auto& r : tri_out) tri_succ1 += r.succ.size();
  if (opt.quantize_f16) {
    for (auto& r : bi_out) {
      r.lambda_ = roundtrip_f16(r.lambda_);
      for (Succ& s : r.succ) s.prob = roundtrip_f16(s.prob);
    }
    for (auto& r : tri_out) {
      r.lambda_ = roundtrip_f16(r.lambda_);
      for (Succ& s : r.succ) s.prob = roundtrip_f16(s.prob);
    }
    for (float& p : uni) p = roundtrip_f16(p);
  }
  // 低精度模拟（只动后继概率，λ/unigram 保持 f32——对应假想的 1 字节概率
  // 格式；--quantize-mant N 与 --quantize-log8 LO 互斥使用）
  if (opt.succ_quant_mant > 0) {
    for (auto& r : bi_out)
      for (Succ& s : r.succ) s.prob = quant_mant(s.prob, opt.succ_quant_mant);
    for (auto& r : tri_out)
      for (Succ& s : r.succ) s.prob = quant_mant(s.prob, opt.succ_quant_mant);
  }
  if (opt.succ_quant_log8_lo < 0) {
    for (auto& r : bi_out)
      for (Succ& s : r.succ) s.prob = quant_log8(s.prob, opt.succ_quant_log8_lo);
    for (auto& r : tri_out)
      for (Succ& s : r.succ) s.prob = quant_log8(s.prob, opt.succ_quant_log8_lo);
  }
  std::cerr << "pruned: bi_ctx " << bi_ctxs.size() << "->" << bi_out.size()
            << " bi_succ " << bi_succ0 << "->" << bi_succ1
            << " (dropped mass " << g3(bi_dropped) << ")\n"
            << "        tri_ctx " << tri_ctxs.size() << "->" << tri_out.size()
            << " tri_succ " << tri_succ0 << "->" << tri_succ1
            << " (dropped mass " << g3(tri_dropped) << ")\n";

  // ---- 序列化（与 train_tcsknm.cc 同构；--format f16 时后继 6B/条）----
  auto build_section = [&opt](const std::vector<CtxRecord>& ctxs,
                              std::vector<uint8_t>& blocks,
                              std::vector<uint8_t>& index) {
    const size_t pages = (ctxs.size() + kStride - 1) / kStride;
    for (size_t page = 0; page < pages; ++page) {
      const size_t begin = page * kStride;
      const size_t end = std::min(begin + kStride, ctxs.size());
      append_u64(index, ctxs[begin].key);
      append_u64(index, blocks.size());
      for (size_t i = begin; i < end; ++i) {
        const CtxRecord& r = ctxs[i];
        append_u64(blocks, r.key);
        append_f32(blocks, r.lambda_);
        append_u32(blocks, static_cast<uint32_t>(r.succ.size()));
        for (const Succ& s : r.succ) {
          append_u32(blocks, s.target);
          if (opt.format_f16) {
            append_u16(blocks, f16_bits(s.prob));
          } else if (opt.format_u8) {
            append_u8(blocks, log8_code(s.prob));
          } else {
            append_f32(blocks, s.prob);
          }
        }
      }
    }
  };
  std::vector<uint8_t> uni_buf, bi_blocks, bi_index, tri_blocks, tri_index;
  uni_buf.reserve(static_cast<size_t>(sec.uni_count) * 8);
  for (uint64_t i = 0; i < sec.uni_count; ++i) {
    const uint64_t off = sec.uni_off + i * 8;
    const uint32_t key = rd_u32(data, off);
    // 概率一律取自内存 uni 表（--quantize-f16 时已舍入；key=0 地板项同源）
    append_u32(uni_buf, key);
    append_f32(uni_buf, key < kShift ? uni[key] : rd_f32(data, off + 4));
  }
  build_section(bi_out, bi_blocks, bi_index);
  build_section(tri_out, tri_blocks, tri_index);

  const uint64_t header_size = 104;
  const uint64_t uni_off = header_size;
  const uint64_t bi_blocks_off = uni_off + uni_buf.size();
  const uint64_t bi_index_off = bi_blocks_off + bi_blocks.size();
  const uint64_t tri_blocks_off = bi_index_off + bi_index.size();
  const uint64_t tri_index_off = tri_blocks_off + tri_blocks.size();
  const uint64_t file_size = tri_index_off + tri_index.size();
  auto rebase = [](std::vector<uint8_t>& index, uint64_t base) {
    for (size_t p = 0; p + 16 <= index.size(); p += 16) {
      uint64_t v = rd_u64(index, p + 8);
      v += base;
      for (int i = 0; i < 8; ++i) index[p + 8 + i] = (v >> (8 * i)) & 0xFF;
    }
  };
  rebase(bi_index, bi_blocks_off);
  rebase(tri_index, tri_blocks_off);

  std::vector<uint8_t> out;
  out.reserve(file_size);
  const char magic02[8] = {'T', 'C', 'S', 'K', 'N', 'M', '0', '2'};
  const char magic03[8] = {'T', 'C', 'S', 'K', 'N', 'M', '0', '3'};
  const char magic04[8] = {'T', 'C', 'S', 'K', 'N', 'M', '0', '4'};
  const char* magic = opt.format_u8 ? magic04
                     : opt.format_f16 ? magic03 : magic02;
  out.insert(out.end(), magic, magic + 8);
  append_u32(out, 1);  // version
  append_u32(out, static_cast<uint32_t>(header_size));
  append_u64(out, file_size);
  append_u32(out, kStride);
  append_u32(out, 0);
  append_u32(out, sec.uni_count);
  append_u32(out, 0);
  append_u32(out, static_cast<uint32_t>(uni_off));
  append_u32(out, 0);
  append_u32(out, static_cast<uint32_t>(bi_out.size()));
  append_u32(out, static_cast<uint32_t>((bi_index.size() + 15) / 16));
  append_u64(out, bi_blocks_off);
  append_u64(out, bi_index_off);
  append_u32(out, static_cast<uint32_t>(tri_out.size()));
  append_u32(out, 0);
  append_u32(out, static_cast<uint32_t>((tri_index.size() + 15) / 16));
  append_u32(out, 0);
  append_u64(out, tri_blocks_off);
  append_u64(out, tri_index_off);
  out.insert(out.end(), uni_buf.begin(), uni_buf.end());
  out.insert(out.end(), bi_blocks.begin(), bi_blocks.end());
  out.insert(out.end(), bi_index.begin(), bi_index.end());
  out.insert(out.end(), tri_blocks.begin(), tri_blocks.end());
  out.insert(out.end(), tri_index.begin(), tri_index.end());
  if (out.size() != file_size) {
    std::cerr << "size mismatch " << out.size() << " != " << file_size << "\n";
    return 5;
  }
  std::ofstream f(out_path, std::ios::binary);
  f.write(reinterpret_cast<const char*>(out.data()),
          static_cast<std::streamsize>(out.size()));
  if (!f) {
    std::cerr << "write failed: " << out_path << "\n";
    return 5;
  }
  std::cerr << "wrote " << out_path << ": " << g3(out.size() / 1e6) << "MB ("
            << g3(100.0 * out.size() / data.size()) << "% of input)\n";
  return 0;
}
