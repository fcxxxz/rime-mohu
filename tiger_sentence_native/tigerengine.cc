// tigerengine — 魔虎整句 C 引擎（libtigerengine.dylib / .so / .dll）
// 从 TigerClaw 虎整句 Rime 版 Lua 引擎（tiger_sentence.lua / tiger_sentence_kn.lua）
// 直译为纯 C ABI 动态库，供 librime-lua 通过 package.loadlib 调用。
//
// 模型：TCSKNM01（整表）/ TCSKNM02（分页），mmap 直读，页缓存交给 OS。
// 码表（外挂 txt，UTF-8，每行）：
//   code <TAB> text <TAB> rank <TAB> freq_rank
//   code：小写字母与 /；text：单字；rank：选重档位（1 起）；freq_rank：字频名次（1 起）。
//
// C ABI：
//   tiger_engine_create(model, lexicon, beam, all_ranks_always, err, errcap) -> handle(>=0)|-1
//   tiger_engine_free(handle)
//   tiger_decode(handle, raw, include_early, out, outcap, ms) -> 候选数（<0 出错）
//   tiger_status(handle, out, outcap) -> 0
//   tiger_last_error() -> const char*
//
// tiger_decode 输出协议（out 缓冲，UTF-8）：
//   行1: truncated early_truncated uses_incomplete prefers_incomplete n_final n_early
//        consensus_complete consensus_text_bytes consensus_raw_length visible_consensus
//   之后 n_final + n_early 行: text \t segmented \t score \t confidence \t max_rank \t pathmap
//   pathmap：逗号分隔的 "文本字节数:原始码长"，供提前上屏定位边界。

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "tigerengine.h"

namespace {

// ---------------------------------------------------------------- 基础工具

thread_local std::string g_last_error;
std::mutex g_engine_mutex;

void set_error(const char* fmt, ...) {
  char buf[512];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  g_last_error = buf;
}

inline uint32_t rd_u32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
inline int32_t rd_i32(const uint8_t* p) { int32_t v; memcpy(&v, p, 4); return v; }
inline uint64_t rd_u64(const uint8_t* p) { uint64_t v; memcpy(&v, p, 8); return v; }
inline float rd_f32(const uint8_t* p) { float v; memcpy(&v, p, 4); return v; }

inline void utf8_next(const char* s, size_t len, size_t i, uint32_t* cp, size_t* n) {
  unsigned char c = (unsigned char)s[i];
  if (c < 0x80) { *cp = c; *n = 1; }
  else if ((c >> 5) == 6 && i + 1 < len) {
    *cp = ((c & 0x1F) << 6) | ((unsigned char)s[i + 1] & 0x3F); *n = 2;
  } else if ((c >> 4) == 14 && i + 2 < len) {
    *cp = ((c & 0x0F) << 12) | (((unsigned char)s[i + 1] & 0x3F) << 6) |
          ((unsigned char)s[i + 2] & 0x3F); *n = 3;
  } else if ((c >> 3) == 30 && i + 3 < len) {
    *cp = ((c & 0x07) << 18) | (((unsigned char)s[i + 1] & 0x3F) << 12) |
          (((unsigned char)s[i + 2] & 0x3F) << 6) | ((unsigned char)s[i + 3] & 0x3F); *n = 4;
  } else { *cp = 0xFFFD; *n = 1; }
}

inline std::vector<std::string> utf8_split(const std::string& s) {
  std::vector<std::string> out;
  size_t i = 0;
  while (i < s.size()) {
    uint32_t cp; size_t n;
    utf8_next(s.data(), s.size(), i, &cp, &n);
    out.emplace_back(s.substr(i, n));
    i += n;
  }
  return out;
}

inline double logsumexp(double a, double b) {
  double m = a > b ? a : b;
  return m + log(exp(a - m) + exp(b - m));
}

// ---------------------------------------------------------------- 模型读取

struct MappedFile {
  uint8_t* data = nullptr;
  size_t size = 0;
  bool open(const char* path) {
    int fd = ::open(path, O_RDONLY);
    if (fd < 0) { set_error("cannot open %s", path); return false; }
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size <= 0) {
      set_error("cannot stat %s", path); ::close(fd); return false;
    }
    size = (size_t)st.st_size;
    void* p = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
    ::close(fd);
    if (p == MAP_FAILED) { set_error("cannot mmap %s", path); return false; }
    data = (uint8_t*)p;
    return true;
  }
  ~MappedFile() { if (data) munmap(data, size); }
};

const uint64_t kShift = 2097152;  // 2^21，容一个 Unicode 码点
inline uint64_t pack2(uint32_t a, uint32_t b) {
  return (uint64_t)a * kShift + (b % kShift);
}

struct CtxCacheEntry {
  bool missing = false;
  int64_t page = -1;
  double lambda_ = 1.0;
  int64_t successor_count = 0;
  size_t successor_position = 0;
};

template <typename K>
struct FifoCache {
  std::unordered_map<K, CtxCacheEntry> values;
  std::vector<K> keys;
  size_t next = 0;
  size_t limit;
  explicit FifoCache(size_t n) : keys(n, K{}), limit(n) {}
  CtxCacheEntry* lookup(K k) {
    auto it = values.find(k);
    return it == values.end() ? nullptr : &it->second;
  }
  void remember(K k, const CtxCacheEntry& e) {
    auto old = values.find(keys[next]);
    if (old != values.end()) values.erase(old);
    keys[next] = k;
    values[k] = e;
    next = (next + 1) % limit;
  }
};

struct KnModel {
  MappedFile file;
  bool mobile = false;
  std::string path;

  double unknown_unigram = 0.0;
  const uint8_t* uni_base = nullptr;
  int64_t uni_count = 0;

  // TCSKNM01
  const uint8_t *bi_tab = nullptr, *bi_ctx = nullptr, *tri_tab = nullptr, *tri_ctx = nullptr;
  uint64_t bi_count = 0;
  int64_t bi_ctx_count = 0;
  uint64_t tri_count = 0;
  uint64_t tri_ctx_count = 0;

  // TCSKNM02
  int64_t index_stride = 64;
  const uint8_t *bi_index = nullptr, *tri_index = nullptr;
  int64_t bi_index_count = 0, tri_index_count = 0;
  uint64_t bi_section_end = 0, tri_section_end = 0;
  uint64_t bi_section_start = 0, tri_section_start = 0;
  int64_t bi_ctx_total = 0, tri_ctx_total = 0;
  FifoCache<uint64_t> cache_b{16384}, cache_t{16384};

  bool range_ok(uint64_t offset, uint64_t length) const {
    return offset <= file.size && length <= static_cast<uint64_t>(file.size) - offset;
  }

  bool advance_ok(size_t* position, uint64_t count, uint64_t element_size) const {
    if (count > UINT64_MAX / element_size) return false;
    const uint64_t length = count * element_size;
    if (!range_ok(*position, length) || length > SIZE_MAX - *position) return false;
    *position += static_cast<size_t>(length);
    return true;
  }

  bool load(const char* p) {
    if (!file.open(p)) return false;
    path = p;
    if (file.size < 32) { set_error("empty n-gram: %s", p); return false; }
    if (memcmp(file.data, "TCSKNM02", 8) == 0) return load_mobile();
    if (memcmp(file.data, "TCSKNM01", 8) == 0) return load_legacy();
    set_error("not a TCSKNM model: %s", p);
    return false;
  }

  bool load_legacy() {
    const uint8_t* d = file.data;
    mobile = false;
    if (rd_i32(d + 8) != 1) { set_error("unsupported n-gram version"); return false; }
    const int32_t raw_uni_count = rd_i32(d + 12);
    if (raw_uni_count <= 0) { set_error("invalid unigram count"); return false; }
    uni_count = raw_uni_count;
    size_t pos = 16;
    uni_base = d + pos;
    if (!advance_ok(&pos, static_cast<uint64_t>(uni_count), 8)) {
      set_error("truncated unigram table"); return false;
    }
    if (!range_ok(pos, 8)) { set_error("truncated bigram header"); return false; }
    bi_count = rd_u64(d + pos); pos += 8;
    bi_tab = d + pos;
    if (!advance_ok(&pos, bi_count, 12)) { set_error("truncated bigram table"); return false; }
    if (!range_ok(pos, 4)) { set_error("truncated bigram context header"); return false; }
    bi_ctx_count = rd_i32(d + pos); pos += 4;
    if (bi_ctx_count < 0) { set_error("invalid bigram context count"); return false; }
    bi_ctx = d + pos;
    if (!advance_ok(&pos, static_cast<uint64_t>(bi_ctx_count), 8)) {
      set_error("truncated bigram context table"); return false;
    }
    if (!range_ok(pos, 8)) { set_error("truncated trigram header"); return false; }
    tri_count = rd_u64(d + pos); pos += 8;
    tri_tab = d + pos;
    if (!advance_ok(&pos, tri_count, 12)) { set_error("truncated trigram table"); return false; }
    if (!range_ok(pos, 8)) { set_error("truncated trigram context header"); return false; }
    tri_ctx_count = rd_u64(d + pos); pos += 8;
    tri_ctx = d + pos;
    if (!advance_ok(&pos, tri_ctx_count, 12)) {
      set_error("truncated trigram context table"); return false;
    }
    unknown_unigram = rd_f32(uni_base + 4);
    return true;
  }

  bool load_mobile() {
    const uint8_t* d = file.data;
    mobile = true;
    if (file.size < 104) { set_error("truncated mobile n-gram"); return false; }
    size_t p = 8;
    auto u32 = [&]() { uint32_t v = rd_u32(d + p); p += 4; return v; };
    auto u64 = [&]() { uint64_t v = rd_u64(d + p); p += 8; return v; };
    uint32_t version = u32();
    uint32_t header_size = u32();
    uint64_t file_size = u64();
    index_stride = u32();
    (void)u32();               // reserved
    uni_count = u32();
    (void)u32();               // reserved
    uint64_t uni_off = u32();  // 注意：uni_off 是 I4
    (void)u32();               // reserved/alignment after the I4 offset
    uint64_t bi_ctx = u32();
    bi_index_count = u32();
    uint64_t bi_blocks_off = u64();
    uint64_t bi_index_off = u64();
    uint64_t tri_ctx = u32();
    (void)u32();               // reserved/alignment before the index count
    tri_index_count = u32();
    (void)u32();               // reserved
    uint64_t tri_blocks_off = u64();
    uint64_t tri_index_off = u64();
    if (version != 1 || header_size != 104) { set_error("unsupported mobile n-gram version"); return false; }
    if (bi_ctx > static_cast<uint64_t>(INT64_MAX) || tri_ctx > static_cast<uint64_t>(INT64_MAX) ||
        index_stride < 16 || (uint64_t)file.size != file_size ||
        !range_ok(uni_off, static_cast<uint64_t>(uni_count) * 8) ||
        !range_ok(bi_index_off, static_cast<uint64_t>(bi_index_count) * 16) ||
        !range_ok(tri_index_off, static_cast<uint64_t>(tri_index_count) * 16) ||
        !(uni_off >= header_size && bi_blocks_off >= uni_off) ||
        !(bi_blocks_off <= bi_index_off) || !(bi_index_off <= tri_blocks_off) ||
        !(tri_blocks_off <= tri_index_off) || tri_index_off > file.size ||
        bi_blocks_off > file.size || tri_blocks_off > file.size ||
        bi_index_off > file.size) {
      set_error("invalid mobile n-gram layout");
      return false;
    }
    if (uni_count == 0 || static_cast<uint64_t>(uni_count) > UINT64_MAX / 8 ||
        static_cast<uint64_t>(bi_index_count) > UINT64_MAX / 16 ||
        static_cast<uint64_t>(tri_index_count) > UINT64_MAX / 16) {
      set_error("mobile n-gram count overflow");
      return false;
    }
    const uint64_t uni_bytes = static_cast<uint64_t>(uni_count) * 8;
    const uint64_t bi_index_bytes = static_cast<uint64_t>(bi_index_count) * 16;
    const uint64_t tri_index_bytes = static_cast<uint64_t>(tri_index_count) * 16;
    if (uni_off > bi_blocks_off || uni_bytes > bi_blocks_off - uni_off ||
        bi_index_off > tri_blocks_off || bi_index_bytes > tri_blocks_off - bi_index_off ||
        tri_index_off > file.size || tri_index_bytes > file.size - tri_index_off) {
      set_error("mobile n-gram index exceeds section");
      return false;
    }
    auto validate_index = [&](uint64_t index_off, uint32_t count,
                              uint64_t section_start, uint64_t section_end) {
      for (uint32_t i = 0; i < count; ++i) {
        const uint64_t entry = index_off + static_cast<uint64_t>(i) * 16;
        const uint64_t page = rd_u64(d + entry + 8);
        if (page < section_start || page >= section_end || !range_ok(page, 16)) return false;
      }
      return true;
    };
    if (!validate_index(bi_index_off, bi_index_count, bi_blocks_off, tri_blocks_off) ||
        !validate_index(tri_index_off, tri_index_count, tri_blocks_off, file.size)) {
      set_error("mobile n-gram page offset is outside section");
      return false;
    }
    auto validate_pages = [&](uint64_t index_off, uint32_t index_count,
                              uint64_t context_count, uint64_t section_end) {
      for (uint32_t page = 0; page < index_count; ++page) {
        const uint64_t entry = index_off + static_cast<uint64_t>(page) * 16;
        const uint64_t page_offset = rd_u64(d + entry + 8);
        const uint64_t consumed = static_cast<uint64_t>(page) * index_stride;
        if (consumed >= context_count) continue;
        const uint64_t records = std::min<uint64_t>(index_stride, context_count - consumed);
        uint64_t position = page_offset;
        for (uint64_t record = 0; record < records; ++record) {
          if (position > section_end || 16 > section_end - position) return false;
          const int32_t successors = rd_i32(d + position + 12);
          if (successors < 0 || static_cast<uint64_t>(successors) > UINT64_MAX / 8) return false;
          position += 16;
          const uint64_t successor_bytes = static_cast<uint64_t>(successors) * 8;
          if (successor_bytes > section_end - position) return false;
          position += successor_bytes;
        }
      }
      return true;
    };
    if (!validate_pages(bi_index_off, bi_index_count, bi_ctx, tri_blocks_off) ||
        !validate_pages(tri_index_off, tri_index_count, tri_ctx, file.size)) {
      set_error("mobile n-gram successor table is outside section");
      return false;
    }
    uni_base = d + uni_off;
    bi_ctx_total = (int64_t)bi_ctx;
    tri_ctx_total = (int64_t)tri_ctx;
    bi_index = d + bi_index_off;
    tri_index = d + tri_index_off;
    bi_section_end = tri_blocks_off;   // 二元块区间结束
    tri_section_end = tri_index_off;   // 三元块区间结束
    bi_section_start = bi_blocks_off;
    tri_section_start = tri_blocks_off;
    unknown_unigram = rd_f32(uni_base + 4);
    return true;
  }

  double lookup_unigram(uint32_t key) const {
    int64_t lo = 0, hi = uni_count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      if ((uint32_t)rd_i32(uni_base + mid * 8) < key) lo = mid + 1; else hi = mid;
    }
    if (lo < uni_count) {
      const uint8_t* at = uni_base + lo * 8;
      if ((uint32_t)rd_i32(at) == key) return rd_f32(at + 4);
    }
    return unknown_unigram;
  }

  struct CtxResult { double lambda_; double prob; bool observed; };

  // legacy 表查询
  CtxResult legacy_lookup(bool trigram, uint64_t key_u64, uint32_t key_u32) const {
    const uint8_t* tab;
    uint64_t count;
    const uint8_t* ctx;
    uint64_t ctx_count;
    size_t ctx_entry;
    if (!trigram) {
      tab = bi_tab; count = bi_count; ctx = bi_ctx; ctx_count = (uint64_t)bi_ctx_count; ctx_entry = 8;
    } else {
      tab = tri_tab; count = tri_count; ctx = tri_ctx; ctx_count = tri_ctx_count; ctx_entry = 12;
    }
    int64_t lo = 0;
    int64_t hi = (int64_t)count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      if (rd_u64(tab + mid * 12) < key_u64) lo = mid + 1; else hi = mid;
    }
    double prob = 0.0;
    bool observed = false;
    if (lo < (int64_t)count) {
      const uint8_t* at = tab + lo * 12;
      if (rd_u64(at) == key_u64) { prob = rd_f32(at + 8); observed = true; }
    }
    double lambda_ = 1.0;
    lo = 0;
    hi = (int64_t)ctx_count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      uint64_t v = ctx_entry == 8 ? (uint64_t)(uint32_t)rd_i32(ctx + mid * 8) : rd_u64(ctx + mid * 12);
      if (v < (ctx_entry == 8 ? (uint64_t)key_u32 : key_u64)) lo = mid + 1; else hi = mid;
    }
    if (lo < (int64_t)ctx_count) {
      const uint8_t* at = ctx + lo * ctx_entry;
      uint64_t v = ctx_entry == 8 ? (uint64_t)(uint32_t)rd_i32(at) : rd_u64(at);
      if (v == (ctx_entry == 8 ? (uint64_t)key_u32 : key_u64))
        lambda_ = rd_f32(at + (ctx_entry == 8 ? 4 : 8));
    }
    return {lambda_, prob, observed};
  }

  const uint8_t* page_base(const uint8_t* index_data, int64_t index_count,
                           int64_t page, uint64_t section_start,
                           uint64_t section_end) const {
    if (!index_data || page < 0 || page >= index_count || section_start > section_end ||
        section_end > file.size || index_data < file.data ||
        index_data > file.data + file.size || static_cast<uint64_t>(page) > UINT64_MAX / 16)
      return nullptr;
    const uint64_t index_offset = static_cast<uint64_t>(index_data - file.data);
    const uint64_t at = static_cast<uint64_t>(page) * 16;
    if (!range_ok(index_offset + at, 16)) return nullptr;
    const uint64_t offset = rd_u64(index_data + at + 8);
    if (offset < section_start || offset >= section_end || !range_ok(offset, 16)) return nullptr;
    return file.data + offset;
  }

  CtxResult scan_successors(const uint8_t* data, size_t position, int64_t count,
                            double lambda_, uint32_t target, uint64_t section_end) const {
    if (!data || data < file.data || data > file.data + file.size || count < 0 ||
        section_end > file.size || static_cast<uint64_t>(count) > UINT64_MAX / 8) {
      return {lambda_, 0.0, false};
    }
    const uint64_t data_offset = static_cast<uint64_t>(data - file.data);
    if (data_offset > section_end || static_cast<uint64_t>(position) > section_end - data_offset ||
        static_cast<uint64_t>(count) * 8 >
            section_end - data_offset - static_cast<uint64_t>(position))
      return {lambda_, 0.0, false};
    int64_t lo = 0, hi = count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      if (rd_u32(data + position + mid * 8) < target) lo = mid + 1; else hi = mid;
    }
    if (lo < count) {
      const uint8_t* at = data + position + lo * 8;
      if (rd_u32(at) == target) return {lambda_, rd_f32(at + 4), true};
    }
    return {lambda_, 0.0, false};
  }

  int64_t find_page(const uint8_t* index_data, int64_t index_count, uint64_t key) const {
    int64_t lo = 0, hi = index_count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      if (rd_u64(index_data + mid * 16) <= key) lo = mid + 1; else hi = mid;
    }
    return lo - 1;
  }

  CtxResult mobile_lookup(bool trigram, uint64_t key, uint32_t target) {
    FifoCache<uint64_t>& cache = trigram ? cache_t : cache_b;
    const uint8_t* index_data = trigram ? tri_index : bi_index;
    int64_t index_count = trigram ? tri_index_count : bi_index_count;
    uint64_t section_start = trigram ? tri_section_start : bi_section_start;
    uint64_t section_end = trigram ? tri_section_end : bi_section_end;
    int64_t context_total = trigram ? tri_ctx_total : bi_ctx_total;

    if (CtxCacheEntry* c = cache.lookup(key)) {
      if (c->missing) return {1.0, 0.0, false};
      const uint8_t* data = page_base(index_data, index_count, c->page, section_start, section_end);
      return scan_successors(data, c->successor_position, c->successor_count, c->lambda_, target,
                             section_end);
    }
    int64_t page = find_page(index_data, index_count, key);
    if (page < 0) {
      cache.remember(key, {true, -1, 1.0, 0, 0});
      return {1.0, 0.0, false};
    }
    const uint8_t* data = page_base(index_data, index_count, page, section_start, section_end);
    if (!data) return {1.0, 0.0, false};
    if (page < 0 || context_total < 0) return {1.0, 0.0, false};
    const uint64_t page_start = static_cast<uint64_t>(page) *
                                static_cast<uint64_t>(index_stride);
    if (page_start >= static_cast<uint64_t>(context_total))
      return {1.0, 0.0, false};
    const uint64_t remaining_u = std::min<uint64_t>(
        static_cast<uint64_t>(index_stride),
        static_cast<uint64_t>(context_total) - page_start);
    if (remaining_u > static_cast<uint64_t>(INT64_MAX)) return {1.0, 0.0, false};
    const int64_t remaining = static_cast<int64_t>(remaining_u);
    size_t position = 0;
    for (int64_t i = 0; i < remaining; ++i) {
      const uint64_t data_offset = static_cast<uint64_t>(data - file.data);
      if (data_offset > section_end || static_cast<uint64_t>(position) > section_end - data_offset ||
          16 > section_end - data_offset - static_cast<uint64_t>(position)) return {1.0, 0.0, false};
      uint64_t context_key = rd_u64(data + position);
      double lambda_ = rd_f32(data + position + 8);
      int32_t succ = rd_i32(data + position + 12);
      if (succ < 0 || static_cast<uint64_t>(succ) > UINT64_MAX / 8 ||
          static_cast<uint64_t>(succ) * 8 > section_end - data_offset - static_cast<uint64_t>(position) - 16)
        return {1.0, 0.0, false};
      position += 16;
      if (context_key == key) {
        cache.remember(key, {false, page, lambda_, succ, position});
        return scan_successors(data, position, succ, lambda_, target, section_end);
      }
      if (context_key > key) break;
      position += (size_t)succ * 8;
    }
    cache.remember(key, {true, -1, 1.0, 0, 0});
    return {1.0, 0.0, false};
  }

  CtxResult lookup_context(bool trigram, uint64_t key_u64, uint32_t key_u32, uint32_t target) {
    if (!mobile) return legacy_lookup(trigram, key_u64, key_u32);
    return mobile_lookup(trigram, key_u64, target);
  }

  double logp(uint32_t a, uint32_t b, uint32_t c) {
    double unigram = lookup_unigram(c);
    KnModel::CtxResult bi = lookup_context(false, (uint64_t)b, b, c);
    if (!std::isfinite(unigram) || unigram < 0.0 ||
        !std::isfinite(bi.prob) || bi.prob < 0.0 ||
        !std::isfinite(bi.lambda_) || bi.lambda_ < 0.0) {
      return std::numeric_limits<double>::quiet_NaN();
    }
    double bigram = bi.prob + bi.lambda_ * unigram;
    KnModel::CtxResult tri = lookup_context(true, pack2(a, b), (uint32_t)pack2(a, b), c);
    if (!std::isfinite(bigram) || bigram < 0.0 ||
        !std::isfinite(tri.prob) || tri.prob < 0.0 ||
        !std::isfinite(tri.lambda_) || tri.lambda_ < 0.0) {
      return std::numeric_limits<double>::quiet_NaN();
    }
    double p = tri.prob + tri.lambda_ * bigram;
    if (!std::isfinite(p) || p < 0.0) return std::numeric_limits<double>::quiet_NaN();
    if (p < 1e-300) p = 1e-300;
    return log(p);
  }

  bool has_observed_bigram(uint32_t a, uint32_t b) {
    return lookup_context(false, (uint64_t)a, a, b).observed;
  }
};

// ---------------------------------------------------------------- 码表

struct LexEntry {
  std::string text;
  int rank;
  std::vector<uint32_t> chars;  // 预拆码点
};

struct Lexicon {
  std::unordered_map<std::string, std::vector<LexEntry>> codes;
  std::vector<int> lengths;                              // 去重升序
  std::unordered_set<std::string> proper_prefixes;
  std::unordered_map<std::string, int> freq_rank;        // text -> 名次
  int max_code_len = 1;
  bool has_multi_char_entries = false;

  static std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
  }

  static int parse_int(const std::string& token, int fallback) {
    int value = 0;
    const char* first = token.data();
    const char* last = first + token.size();
    const auto parsed = std::from_chars(first, last, value);
    if (parsed.ec != std::errc() || parsed.ptr != last) return fallback;
    return value;
  }

  bool load(const char* path) {
    FILE* f = fopen(path, "r");
    if (!f) { set_error("cannot open lexicon %s", path); return false; }
    std::vector<int> lens;
    char buf[4096];
    std::unordered_set<int> len_set;
    while (fgets(buf, sizeof(buf), f)) {
      std::string line = trim(buf);
      if (line.empty() || line[0] == '#') continue;
      std::vector<std::string> cols;
      size_t start = 0;
      for (size_t i = 0; i <= line.size(); ++i) {
        if (i == line.size() || line[i] == '\t') {
          cols.push_back(line.substr(start, i - start));
          start = i + 1;
        }
      }
      if (cols.size() < 2) continue;
      const std::string& code = cols[0];
      const std::string& text = cols[1];
      int rank = cols.size() > 2 ? parse_int(cols[2], 1) : 1;
      int fr = cols.size() > 3 ? parse_int(cols[3], 20001) : 20001;
      if (code.empty() || text.empty()) continue;
      LexEntry e;
      e.text = text;
      e.rank = rank < 1 ? 1 : rank;
      for (auto& ch : utf8_split(text)) {
        uint32_t cp;
        size_t n;
        utf8_next(ch.data(), ch.size(), 0, &cp, &n);
        e.chars.push_back(cp);
      }
      if (e.chars.size() != 1) has_multi_char_entries = true;
      codes[code].push_back(std::move(e));
      if (freq_rank.find(text) == freq_rank.end()) freq_rank[text] = fr;
      len_set.insert((int)code.size());
    }
    fclose(f);
    for (int l : len_set) {
      lens.push_back(l);
      if (l > max_code_len) max_code_len = l;
    }
    std::sort(lens.begin(), lens.end());
    lengths = lens;
    for (const auto& kv : codes) {
      const std::string& code = kv.first;
      for (size_t l = 1; l < code.size(); ++l)
        proper_prefixes.insert(code.substr(0, l));
    }
    return !codes.empty();
  }

  int rank_of(const std::string& text) const {
    auto it = freq_rank.find(text);
    return it == freq_rank.end() ? 20001 : it->second;
  }
};

// ---------------------------------------------------------------- 解码引擎

const uint32_t kBOS = 2, kEOS = 3;
const double kRankPenalty = 0.03;
const double kCharReward = 2.0;
const int kIsolationThreshold = 3000;
const double kIsolationLambda = 2.0;
const int kCandidateLimit = 20;
const int kEarlyCandidateLimit = 20;
const size_t kMaxRawLength = 128;

struct State {
  double score = 0;
  double mass_score = 0;
  std::string text;
  std::string segmented;
  uint32_t prev2 = kBOS, prev1 = kBOS;
  int max_rank = 1;
  int edges = 0;  // 消费的词典条目数；≤4 键排序时“整段单条命中”优先
  const State* previous = nullptr;
  size_t text_length = 0;   // 字节
  size_t raw_length = 0;
};

inline bool state_better(const State* l, const State* r) {
  if (l->max_rank != r->max_rank) return l->max_rank < r->max_rank;
  if (l->score == r->score) return l->text < r->text;
  return l->score > r->score;
}

// all_ranks（魔虎）模式：语言模型分数优先，rank 仅作平局裁决。
// 原版 max_rank 优先排序是为“选重键=用户意图”的万象虎设计；
// 全档位竞争时若 rank 优先，会排出“全 rank1 字的垃圾句”压过正确句。
inline bool state_better_free(const State* l, const State* r) {
  if (l->score == r->score) return l->text < r->text;
  return l->score > r->score;
}

struct Bucket {
  // 聚合式：text -> 索引
  std::unordered_map<std::string, size_t> index;
  std::vector<State*> best;
  std::vector<double> mass;
  bool truncated = false;
  bool free_order = false;  // all_ranks 模式：分数优先排序

  void add(State* s) {
    auto it = index.find(s->text);
    if (it == index.end()) {
      index[s->text] = best.size();
      best.push_back(s);
      mass.push_back(s->mass_score);
    } else {
      size_t i = it->second;
      mass[i] = logsumexp(mass[i], s->mass_score);
      const State* prev = best[i];
      bool better = free_order
          ? (s->score > prev->score || (s->score == prev->score && s->max_rank < prev->max_rank))
          : (s->max_rank < prev->max_rank ||
             (s->max_rank == prev->max_rank && s->score > prev->score));
      if (better) best[i] = s;
      best[i]->mass_score = mass[i];
    }
  }

  std::vector<State*> limit(int beam, bool* truncated_now) {
    std::vector<State*> all = best;
    bool now = (int)all.size() > beam;
    if (now) {
      if (free_order) {
        std::partial_sort(all.begin(), all.begin() + beam, all.end(), state_better_free);
      } else {
        std::partial_sort(all.begin(), all.begin() + beam, all.end(), state_better);
      }
      all.resize(beam);
    }
    *truncated_now = now;
    if (free_order) std::sort(all.begin(), all.end(), state_better_free);
    else std::sort(all.begin(), all.end(), state_better);
    return all;
  }
};

struct OutItem {
  std::string text, segmented, pathmap;
  double score = 0, confidence = 0;
  int max_rank = 1;
  int edges = 0;
};

struct DecodeResult {
  std::vector<OutItem> items;        // 终态候选（已排序）
  std::vector<OutItem> early;        // 提前上屏候选（按置信度降序）
  bool truncated = false;
  bool early_truncated = false;
  bool uses_incomplete = false;
  bool prefers_incomplete = false;
  // When the exposed candidate lists are capped, these fields carry a
  // compact consensus proof computed over the uncapped beam.  The prefix is
  // expressed in text bytes and has one shared raw-code boundary only when
  // consensus_complete is true.
  size_t consensus_text_bytes = 0;
  size_t consensus_raw_length = 0;
  bool consensus_complete = false;
  // This capability explicitly permits Lua to use a bounded-view heuristic
  // over every row it can see.  It is never a claim that hidden beam paths
  // were exhaustively enumerated; Lua validates visible rows and boundaries.
  bool visible_consensus = false;
};

struct Engine {
  KnModel model;
  Lexicon lex;
  int beam = 200;
  bool all_ranks_always = true;      // 魔虎模式：>4 键也允许全部档位竞争

  std::vector<std::unique_ptr<State>> pool;
  std::vector<std::unique_ptr<Bucket>> states;
  std::string cached_raw;
  bool cache_valid = false;
  DecodeResult cached_result;
  bool cached_with_early = false;
  // A multi-character lexicon entry is allowed only when it consumes the
  // complete composition.  Once such a terminal state has been generated,
  // retaining any cached frontier across an extension can turn that phrase
  // into an illegal sentence prefix (and a shrink can expose a phrase that
  // was intentionally suppressed under the longer input).  Rebuild in those
  // cases; ordinary single-character frontiers may still use incremental
  // expansion.
  bool has_terminal_phrase_states = false;

  std::unordered_map<uint64_t, double> logp_cache;

  double logp(uint32_t a, uint32_t b, uint32_t c) {
    uint64_t key = ((uint64_t)a << 42) | ((uint64_t)b << 21) | c;
    auto it = logp_cache.find(key);
    if (it != logp_cache.end()) return it->second;
    double v = model.logp(a, b, c);
    if (!std::isfinite(v)) throw std::runtime_error("invalid n-gram probability");
    if (logp_cache.size() > 65536) logp_cache.clear();
    logp_cache[key] = v;
    return v;
  }

  double isolation_penalty(const std::string& text) {
    if (text.empty()) return 0;
    double penalty = 0;
    auto chars = utf8_split(text);
    for (size_t i = 0; i < chars.size(); ++i) {
      int rank = lex.rank_of(chars[i]);
      if (rank > kIsolationThreshold) {
        bool left = false, right = false;
        uint32_t cp;
        size_t n;
        utf8_next(chars[i].data(), chars[i].size(), 0, &cp, &n);
        if (i > 0) {
          uint32_t p;
          utf8_next(chars[i - 1].data(), chars[i - 1].size(), 0, &p, &n);
          left = model.has_observed_bigram(p, cp);
        }
        if (i + 1 < chars.size()) {
          uint32_t q;
          utf8_next(chars[i + 1].data(), chars[i + 1].size(), 0, &q, &n);
          right = model.has_observed_bigram(cp, q);
        }
        if (!left && !right) penalty += kIsolationLambda;
      }
    }
    return penalty;
  }

  static size_t trailing_selector_span(const std::string& raw) {
    size_t i = raw.size();
    while (i > 0) {
      char c = raw[i - 1];
      if ((c >= '0' && c <= '9') || c == ';' || c == '\'') i--;
      else break;
    }
    return raw.size() - i;
  }

  // 返回 (rank, consumed_end)；无选重键时 rank=0
  static void parse_selector(const std::string& raw, size_t code_end,
                              int* rank, size_t* consumed) {
    *rank = 0;
    *consumed = code_end;
    size_t next = code_end + 1;
    if (next > raw.size()) return;
    char m = raw[code_end];
    if (m == ';') { *rank = 2; *consumed = next; return; }
    if (m == '\'') { *rank = 3; *consumed = next; return; }
    if (m >= '0' && m <= '9') {
      size_t end = next;
      while (end < raw.size() && raw[end] >= '0' && raw[end] <= '9') end++;
      int value = 0;
      for (size_t i = next; i < end; ++i) {
        const int digit = raw[i] - '0';
        if (value > (std::numeric_limits<int>::max() - digit) / 10) {
          value = std::numeric_limits<int>::max();
          break;
        }
        value = value * 10 + digit;
      }
      *rank = (value == 0 && end == next + 1) ? 10 : value;
      *consumed = end;
    }
  }

  State* new_state() {
    pool.push_back(std::make_unique<State>());
    return pool.back().get();
  }

  std::unique_ptr<Bucket> new_bucket() {
    auto b = std::make_unique<Bucket>();
    b->free_order = all_ranks_always;
    return b;
  }

  void expand_range(const std::string& raw, size_t from_pos, size_t length,
                    int64_t minimum_consumed_end) {
    if (minimum_consumed_end < 0) minimum_consumed_end = -1;
    bool allow_all = all_ranks_always || length <= 4;
    for (size_t pos = from_pos; pos < length; ++pos) {
      bool now_truncated = false;
      std::vector<State*> current = states[pos]->limit(beam, &now_truncated);
      auto nb = new_bucket();
      nb->truncated = states[pos]->truncated || now_truncated;
      for (State* s : current) nb->add(s);
      states[pos] = std::move(nb);
      const bool source_truncated = states[pos]->truncated;
      if (current.empty()) continue;
      for (int code_length : lex.lengths) {
        if (pos + (size_t)code_length > length) continue;
        std::string code = raw.substr(pos, (size_t)code_length);
        auto cit = lex.codes.find(code);
        if (cit == lex.codes.end()) continue;
        const std::vector<LexEntry>& candidates = cit->second;
        int selected_rank;
        size_t consumed_end;
        parse_selector(raw, pos + (size_t)code_length, &selected_rank, &consumed_end);
        if (!((int64_t)consumed_end > minimum_consumed_end)) continue;
        // 一码简词只允许独立输入；多键整句中的每条边至少消费双拼两键。
        if (length > 1 && consumed_end - pos < 2) continue;
        const std::vector<LexEntry>* chosen = &candidates;
        std::vector<LexEntry> filtered;
        if (!(selected_rank == 0 && allow_all)) {
          int want = selected_rank > 0 ? selected_rank : 1;
          for (const auto& e : candidates)
            if (e.rank == want) filtered.push_back(e);
          chosen = &filtered;
        }
        if (source_truncated && !chosen->empty())
          states[consumed_end]->truncated = true;
        for (State* item : current) {
          for (const LexEntry& cand : *chosen) {
            // 多字简词只作为整串完全命中的候选，不参与更长句子的内部切分。
            if (cand.chars.size() != 1 && !(pos == 0 && consumed_end == length)) continue;
            if (cand.chars.size() != 1) has_terminal_phrase_states = true;
            double score = item->score;
            uint32_t prev2 = item->prev2, prev1 = item->prev1;
            for (uint32_t cp : cand.chars) {
              score += logp(prev2, prev1, cp);
              score += kCharReward;
              prev2 = prev1;
              prev1 = cp;
            }
            if (selected_rank == 0 && cand.rank > 1)
              score -= kRankPenalty * log(1.0 + (double)(cand.rank - 1));
            std::string piece = raw.substr(pos, consumed_end - pos);
            std::string segmented = item->segmented.empty() ? piece
                : item->segmented + " " + piece;
            std::string text = item->text + cand.text;
            State* s2 = new_state();
            s2->score = score;
            s2->mass_score = item->mass_score + (score - item->score);
            s2->text = std::move(text);
            s2->segmented = std::move(segmented);
            s2->prev2 = prev2;
            s2->prev1 = prev1;
            s2->max_rank = std::max(item->max_rank, cand.rank);
            s2->edges = item->edges + 1;
            s2->previous = item;
            s2->text_length = s2->text.size();
            s2->raw_length = consumed_end;
            states[consumed_end]->add(s2);
          }
        }
      }
    }
  }

  void to_out(State* s, double ending_adjustment, OutItem* out) {
    out->text = s->text;
    out->segmented = s->segmented;
    out->score = s->score + ending_adjustment;
    out->confidence = s->mass_score + ending_adjustment;
    out->max_rank = std::max(1, s->max_rank);
    out->edges = s->edges;
  }

  void build_pathmap(State* s, OutItem* out) {
    std::unordered_map<size_t, size_t> lengths;
    const State* p = s;
    while (p) {
      auto it = lengths.find(p->text_length);
      if (it == lengths.end()) lengths[p->text_length] = p->raw_length;
      p = p->previous;
    }
    char buf[64];
    bool first = true;
    std::vector<std::pair<size_t, size_t>> v(lengths.begin(), lengths.end());
    std::sort(v.begin(), v.end());
    for (auto& kv : v) {
      snprintf(buf, sizeof(buf), "%s%zu:%zu", first ? "" : ",", kv.first, kv.second);
      out->pathmap += buf;
      first = false;
    }
  }

  static bool utf8_continuation(unsigned char value) {
    return value >= 0x80 && value <= 0xbf;
  }

  static size_t common_text_prefix(const std::vector<State*>& paths) {
    if (paths.empty() || !paths[0]) return 0;
    const std::string& first = paths[0]->text;
    size_t limit = first.size();
    for (size_t index = 1; index < paths.size(); ++index) {
      if (!paths[index]) return 0;
      limit = std::min(limit, paths[index]->text.size());
    }
    if (paths.size() == 1) return limit;
    size_t matched = 0;
    while (matched < limit) {
      bool same = true;
      for (size_t index = 1; index < paths.size(); ++index) {
        if (first[matched] != paths[index]->text[matched]) {
          same = false;
          break;
        }
      }
      if (!same) break;
      ++matched;
    }
    while (matched > 0 && matched < first.size() &&
           utf8_continuation(static_cast<unsigned char>(first[matched]))) {
      --matched;
    }
    return matched;
  }

  static bool raw_boundary_for_prefix(const State* state, size_t text_bytes,
                                      size_t* raw_length) {
    if (!state || !raw_length) return false;
    for (const State* cursor = state; cursor; cursor = cursor->previous) {
      if (cursor->text_length == text_bytes) {
        *raw_length = cursor->raw_length;
        return true;
      }
    }
    return false;
  }

  static void compute_consensus(const std::vector<State*>& paths, bool complete,
                                DecodeResult* result) {
    if (!result) return;
    result->consensus_text_bytes = 0;
    result->consensus_raw_length = 0;
    result->consensus_complete = false;
    if (!complete || paths.empty()) return;

    size_t prefix = common_text_prefix(paths);
    const std::string& first = paths[0]->text;
    size_t shortest = first.size();
    for (const State* path : paths) shortest = std::min(shortest, path->text.size());
    // Never return an entire shortest candidate as an early-commit proposal;
    // it may still be extended by a later code edge.
    if (prefix >= shortest && prefix > 0) {
      --prefix;
      while (prefix > 0 && prefix < first.size() &&
             utf8_continuation(static_cast<unsigned char>(first[prefix]))) {
        --prefix;
      }
    }
    while (true) {
      bool valid = true;
      size_t shared_raw = 0;
      bool have_raw = false;
      for (const State* path : paths) {
        size_t raw = 0;
        if (!raw_boundary_for_prefix(path, prefix, &raw)) {
          valid = false;
          break;
        }
        if (!have_raw) {
          shared_raw = raw;
          have_raw = true;
        } else if (shared_raw != raw) {
          valid = false;
          break;
        }
      }
      if (valid) {
        result->consensus_text_bytes = prefix;
        result->consensus_raw_length = shared_raw;
        result->consensus_complete = true;
        return;
      }
      if (prefix == 0) break;
      size_t next = prefix - 1;
      while (next > 0 && next < first.size() &&
             utf8_continuation(static_cast<unsigned char>(first[next]))) {
        --next;
      }
      prefix = next;
    }
    // An empty prefix is a complete (but non-actionable) proof.
    result->consensus_complete = true;
  }

  bool incomplete_code_tail(const std::string& tail) {
    if (tail.empty()) return false;
    for (char c : tail)
      if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'))) return false;
    if (!lex.proper_prefixes.count(tail)) return false;
    if (tail.size() >= 2 && lex.codes.count(tail)) return false;
    return true;
  }

  DecodeResult emit(const std::string& raw, bool include_early) {
    DecodeResult result;
    bool now_truncated = false;
    std::vector<State*> completed = states[raw.size()]->limit(beam, &now_truncated);
    result.truncated = states[raw.size()]->truncated || now_truncated;
    const bool beam_consensus_complete = !result.truncated;
    bool consensus_paths_complete = beam_consensus_complete;
    std::vector<State*> consensus_paths;
    if (beam_consensus_complete) {
      for (State* s : states[raw.size()]->best)
        if (s && !s->text.empty()) consensus_paths.push_back(s);
    }
    // Keep the full beam available to the early-commit mass calculation below,
    // but expose only the documented native top-20 to Lua and downstream
    // reranking.  This bounds the ABI payload without changing confidence math.
    std::vector<OutItem> final_items;
    final_items.reserve(completed.size());
    for (State* s : completed) {
      double ending = logp(s->prev2, s->prev1, kEOS) - isolation_penalty(s->text);
      OutItem item;
      to_out(s, ending, &item);
      // Early-commit and downstream prefix consumers need the raw-code
      // boundaries for terminal candidates as well as incomplete paths.
      build_pathmap(s, &item);
      final_items.push_back(std::move(item));
    }
    // ≤4 键：魔虎“四码简快码”规则——整段单条命中按码表序排最前，
    // 其余多段解析按码表序；>4 键（整句）：语言模型分数优先。
    bool short_input = raw.size() <= 4;
    // Apply EOS/isolation adjustments before exposing the bounded top-20.
    // Truncating the pre-adjustment beam first could hide a candidate whose
    // final score should move it above the twentieth row.
    std::sort(final_items.begin(), final_items.end(),
              [this, short_input](const OutItem& l, const OutItem& r) {
                if (short_input) {
                  if (l.edges != r.edges) return l.edges < r.edges;
                  if (l.max_rank != r.max_rank) return l.max_rank < r.max_rank;
                } else if (!all_ranks_always) {
                  if (l.max_rank != r.max_rank) return l.max_rank < r.max_rank;
                }
                if (l.score == r.score) return l.text < r.text;
                return l.score > r.score;
              });
    if (final_items.size() > static_cast<size_t>(kCandidateLimit)) {
      // The exposed ABI is itself a bounded view.  Treat rows hidden by that
      // cap as truncated so consumers such as early-commit do not mistake the
      // visible twenty-way intersection for the complete beam.
      result.truncated = true;
      final_items.resize(kCandidateLimit);
    }
    result.items = std::move(final_items);
    compute_consensus(consensus_paths, beam_consensus_complete, &result);
    if (!include_early) return result;

    std::unordered_map<std::string, double> mass_by_text;
    std::unordered_map<std::string, OutItem> best_by_text;
    auto add_states = [&](const std::vector<State*>& vs) {
      for (State* s : vs) {
        if (s->text.empty()) continue;
        double ending = logp(s->prev2, s->prev1, kEOS) - isolation_penalty(s->text);
        double confidence = s->mass_score + ending;
        auto it = mass_by_text.find(s->text);
        if (it == mass_by_text.end()) mass_by_text[s->text] = confidence;
        else it->second = logsumexp(it->second, confidence);
        auto bit = best_by_text.find(s->text);
        if (bit == best_by_text.end() || confidence > bit->second.confidence) {
          OutItem item;
          to_out(s, ending, &item);
          item.confidence = confidence;
          build_pathmap(s, &item);
          best_by_text[s->text] = std::move(item);
        }
      }
    };
    add_states(completed);
    size_t max_tail = std::min<size_t>(lex.max_code_len - 1, raw.size() - 1);
    for (size_t tail_len = 1; tail_len <= max_tail; ++tail_len) {
      size_t consumed = raw.size() - tail_len;
      std::string tail = raw.substr(consumed);
      if (!incomplete_code_tail(tail)) continue;
      bool partial_truncated = false;
      std::vector<State*> partial = states[consumed]->limit(beam, &partial_truncated);
      if (partial.empty()) continue;
      if (states[consumed]->truncated || partial_truncated)
        result.early_truncated = true;
      if (beam_consensus_complete && !states[consumed]->truncated && !partial_truncated) {
        for (State* s : states[consumed]->best)
          if (s && !s->text.empty()) consensus_paths.push_back(s);
      } else {
        // We do not have a complete proof for a pruned partial frontier.
        // Keep the visible candidates for confidence ordering, but prevent
        // them from being used as a global consensus below.
        result.consensus_complete = false;
        consensus_paths_complete = false;
      }
      result.uses_incomplete = true;
      add_states(partial);
    }
    if (result.uses_incomplete) {
      for (auto& kv : best_by_text) {
        OutItem item = kv.second;
        item.confidence = mass_by_text[item.text];
        result.early.push_back(std::move(item));
      }
      std::sort(result.early.begin(), result.early.end(),
                [](const OutItem& l, const OutItem& r) {
                  if (l.confidence == r.confidence) return l.text < r.text;
                  return l.confidence > r.confidence;
                });
      if (result.early.size() > static_cast<size_t>(kEarlyCandidateLimit)) {
        // Lua only consumes a bounded consensus view.  Mark the omission so
        // it will never be treated as complete evidence for early commit.
        result.early_truncated = true;
        result.early.resize(kEarlyCandidateLimit);
      }
      if (!result.early.empty()) {
        if (result.items.empty()) {
          result.prefers_incomplete = true;
        } else {
          const OutItem* full_top = &result.items[0];
          for (size_t i = 1; i < result.items.size(); ++i)
            if (result.items[i].confidence > full_top->confidence)
              full_top = &result.items[i];
          result.prefers_incomplete = result.early[0].text != full_top->text;
        }
      }
    }
    compute_consensus(consensus_paths, consensus_paths_complete, &result);
    // When the exposed lists are capped, advertise that their bounded rows may
    // still be used for the neural early-commit heuristic.  The Lua side only
    // accepts a high-confidence prefix with consistent visible boundaries.
    result.visible_consensus = include_early &&
        (result.truncated || result.early_truncated) &&
        result.items.size() + result.early.size() >= 2;
    return result;
  }

  void invalidate_decode_cache(const std::string& raw, bool include_early) {
    // A non-letter composition is not a valid native frontier.  Retaining
    // buckets from the previous composition would allow a later letter suffix
    // (for example, "33cd") to reuse states that belong to a different raw
    // prefix and can even index the resized vector out of bounds.
    pool.clear();
    states.clear();
    has_terminal_phrase_states = false;
    cached_raw = raw;
    cache_valid = true;
    cached_result = DecodeResult();
    cached_with_early = include_early;
  }

  DecodeResult decode_full(const std::string& raw_code, bool include_early) {
    std::string raw = normalize(raw_code);
    if (raw.empty() || raw.find_first_of("abcdefghijklmnopqrstuvwxyz") == std::string::npos) {
      invalidate_decode_cache(raw, include_early);
      return cached_result;
    }
    rebuild(raw, include_early);
    return cached_result;
  }

  DecodeResult& decode(const std::string& raw_code, bool include_early) {
    std::string raw = normalize(raw_code);
    if (raw.empty() || raw.find_first_of("abcdefghijklmnopqrstuvwxyz") == std::string::npos) {
      invalidate_decode_cache(raw, include_early);
      return cached_result;
    }
    if (cache_valid && cached_raw == raw &&
        cached_with_early == (include_early != 0))
      return cached_result;

    size_t length = raw.size();
    bool reuse = false;
    if (cache_valid && !cached_raw.empty() && states.size() > 1) {
      size_t old_n = cached_raw.size();
      if (old_n == 1 || length == 1 || ((old_n <= 4) != (length <= 4))) {
        reuse = false;
      } else if (length > old_n && raw.compare(0, old_n, cached_raw) == 0 &&
                 !has_terminal_phrase_states) {
        size_t max_consume = lex.max_code_len + trailing_selector_span(raw);
        size_t from_pos = old_n + 1 > max_consume ? old_n + 1 - max_consume : 0;
        states.resize(length + 1);
        for (size_t i = old_n + 1; i <= length; ++i)
          states[i] = new_bucket();
        expand_range(raw, from_pos, length, (int64_t)old_n);
        reuse = true;
      } else if (length < old_n && cached_raw.compare(0, length, raw) == 0 &&
                 !lex.has_multi_char_entries) {
        states.resize(length + 1);
        reuse = true;
      }
    }
    if (!reuse) rebuild(raw, include_early);
    else {
      cached_result = emit(raw, include_early);
      cached_raw = raw;
      cache_valid = true;
      cached_with_early = include_early;
    }
    return cached_result;
  }

  void rebuild(const std::string& raw, bool include_early) {
    pool.clear();
    states.clear();
    has_terminal_phrase_states = false;
    size_t length = raw.size();
    states.resize(length + 1);
    for (auto& s : states) s = new_bucket();
    State* root = new_state();
    states[0]->add(root);
    expand_range(raw, 0, length, -1);
    cached_result = emit(raw, include_early);
    cached_raw = raw;
    cache_valid = true;
    cached_with_early = include_early;
  }

  static std::string normalize(const std::string& s) {
    std::string out;
    for (char c : s) {
      if (c == ' ' || c == '\t' || c == '\n' || c == '\r') continue;
      out += (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
    }
    return out;
  }
};

// Handles are intentionally never reused after free.  The table is tiny
// compared with the mapped model, and tombstones prevent a late caller from
// turning a stale integer handle into a different engine (ABA).
std::vector<std::unique_ptr<Engine>> g_engines;

bool serialize(const DecodeResult& r, char* out, int outcap) {
  if (!out || outcap <= 0) return false;
  std::string s;
  char buf[256];
  snprintf(buf, sizeof(buf), "%d %d %d %d %zu %zu %d %zu %zu %d\n",
           r.truncated ? 1 : 0, r.early_truncated ? 1 : 0,
           r.uses_incomplete ? 1 : 0, r.prefers_incomplete ? 1 : 0,
           r.items.size(), r.early.size(), r.consensus_complete ? 1 : 0,
           r.consensus_text_bytes, r.consensus_raw_length,
           r.visible_consensus ? 1 : 0);
  s += buf;
  auto write_item = [&](const OutItem& it) {
    if (!std::isfinite(it.score) || !std::isfinite(it.confidence)) return false;
    s += it.text; s += '\t'; s += it.segmented; s += '\t';
    snprintf(buf, sizeof(buf), "%.9g\t%.9g\t%d\t", it.score, it.confidence, it.max_rank);
    s += buf; s += it.pathmap; s += '\n';
    return true;
  };
  for (const auto& it : r.items) if (!write_item(it)) return false;
  for (const auto& it : r.early) if (!write_item(it)) return false;
  if ((int)s.size() + 1 > outcap) return false;
  memcpy(out, s.data(), s.size() + 1);
  return true;
}

bool raw_length_within_limit(const char* raw) {
  return raw && ::strnlen(raw, kMaxRawLength + 1) <= kMaxRawLength;
}

void copy_last_error(char* out, int capacity) noexcept {
  if (out && capacity > 0) snprintf(out, capacity, "%s", g_last_error.c_str());
}

}  // namespace

extern "C" {

int tiger_engine_create(const char* model_path, const char* lexicon_path,
                        int beam_width, int all_ranks_always,
                        char* err, int errcap) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (!model_path || !lexicon_path) {
      set_error("model and lexicon paths are required");
      copy_last_error(err, errcap);
      return -1;
    }
    auto e = std::make_unique<Engine>();
    if (beam_width > 0) e->beam = beam_width;
    if (all_ranks_always >= 0) e->all_ranks_always = all_ranks_always != 0;
    if (!e->model.load(model_path) || !e->lex.load(lexicon_path)) {
      copy_last_error(err, errcap);
      return -1;
    }
    if (g_engines.size() >= static_cast<size_t>(std::numeric_limits<int>::max())) {
      set_error("too many engine handles");
      copy_last_error(err, errcap);
      return -1;
    }
    g_engines.push_back(std::move(e));
    return (int)g_engines.size() - 1;
  } catch (const std::exception&) {
    set_error("engine allocation failed");
    copy_last_error(err, errcap);
    return -1;
  } catch (...) {
    set_error("engine creation failed");
    copy_last_error(err, errcap);
    return -1;
  }
}

void tiger_engine_free(int handle) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle >= 0 && handle < (int)g_engines.size())
      g_engines[handle].reset();
  } catch (...) {
    set_error("engine release failed");
  }
}

int tiger_decode(int handle, const char* raw, int include_early,
                 char* out, int outcap, double* ms) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!raw || !out || outcap <= 0) {
      set_error("raw input and output buffer are required");
      return -1;
    }
    if (!raw_length_within_limit(raw)) {
      set_error("raw input exceeds maximum length");
      return -1;
    }
    Engine* e = g_engines[handle].get();
    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    DecodeResult& r = e->decode(raw, include_early != 0);
    struct timespec t1;
    clock_gettime(CLOCK_MONOTONIC, &t1);
    if (ms) *ms = (t1.tv_sec - t0.tv_sec) * 1000.0 + (t1.tv_nsec - t0.tv_nsec) / 1e6;
    if (!serialize(r, out, outcap)) {
      set_error("output buffer too small");
      return -1;
    }
    return (int)r.items.size();
  } catch (const std::exception&) {
    set_error("engine decode failed");
    return -1;
  } catch (...) {
    set_error("engine decode failed");
    return -1;
  }
}

int tiger_decode_full(int handle, const char* raw, int include_early,
                      char* out, int outcap) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!raw || !out || outcap <= 0) {
      set_error("raw input and output buffer are required");
      return -1;
    }
    if (!raw_length_within_limit(raw)) {
      set_error("raw input exceeds maximum length");
      return -1;
    }
    Engine* e = g_engines[handle].get();
    DecodeResult r = e->decode_full(raw, include_early != 0);
    if (!serialize(r, out, outcap)) {
      set_error("output buffer too small");
      return -1;
    }
    return (int)r.items.size();
  } catch (const std::exception&) {
    set_error("engine decode failed");
    return -1;
  } catch (...) {
    set_error("engine decode failed");
    return -1;
  }
}

int tiger_status(int handle, char* out, int outcap) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) return -1;
    Engine* e = g_engines[handle].get();
    char buf[1024];
    snprintf(buf, sizeof(buf), "path=%s\tformat=%s\tbytes=%llu\tcodes=%zu\tbeam=%d",
             e->model.path.c_str(), e->model.mobile ? "TCSKNM02" : "TCSKNM01",
             (unsigned long long)e->model.file.size, e->lex.codes.size(), e->beam);
    if (out && outcap > 0) snprintf(out, outcap, "%s", buf);
    return 0;
  } catch (...) {
    set_error("engine status failed");
    return -1;
  }
}

const char* tiger_last_error() { return g_last_error.c_str(); }

}  // extern "C"
