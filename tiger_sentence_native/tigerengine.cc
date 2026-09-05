// tigerengine — 魔虎整句 C 引擎（libtigerengine.dylib / .so / .dll）
// 从 TigerClaw 虎整句 Rime 版 Lua 引擎（tiger_sentence.lua / tiger_sentence_kn.lua）
// 直译为纯 C ABI 动态库，供 librime-lua 通过 package.loadlib 调用。
//
// 模型：TCSKNM01（整表）/ TCSKNM02（分页），mmap 直读，页缓存交给 OS。
// 码表（外挂 txt，UTF-8，每行）：
//   code <TAB> text <TAB> rank <TAB> freq_rank [<TAB> reading_freq]
//   code：小写字母与 /；text：单字；rank：选重档位（1 起）；freq_rank：字频名次（1 起）；
//   reading_freq：可选读音条件简频（同字罕用读音≈0），装载期归一为
//   log P(读音|字) 先验并入路径分（tiger_engine_set_reading_prior_weight）。
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
//   之后 n_final + n_early 行: text \t segmented \t score \t confidence \t max_rank \t pathmap [\t personal]
//   pathmap：逗号分隔的 "文本字节数:原始码长"，供提前上屏定位边界。

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <charconv>
#include <chrono>
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
#include <utility>
#include <vector>

#ifdef _WIN32
// windows.h 定义 min/max 宏会破坏 std::min/std::max，必须先声明 NOMINMAX。
#define NOMINMAX
#include <windows.h>
#include <io.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#include "tigerengine.h"

namespace {

// ---------------------------------------------------------------- 基础工具

thread_local std::string g_last_error;
std::mutex g_engine_mutex;
std::atomic<unsigned long long> g_snapshot_temp_sequence{0};

#ifdef TIGERENGINE_MAPPING_TEST
int g_mapping_unmap_count = 0;
int g_mapping_close_count = 0;
#endif

void set_error(const char* fmt, ...) {
  char buf[512];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  g_last_error = buf;
}

#ifdef _WIN32
bool utf8_path_to_wide(const char* path, std::wstring* output) {
  const size_t length = path ? std::strlen(path) : 0;
  if (length == 0 || length > static_cast<size_t>(std::numeric_limits<int>::max())) {
    return false;
  }
  const int wide_length = MultiByteToWideChar(
      CP_UTF8, MB_ERR_INVALID_CHARS, path, static_cast<int>(length), nullptr, 0);
  if (wide_length <= 0) return false;
  output->resize(static_cast<size_t>(wide_length));
  return MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path,
                             static_cast<int>(length), output->data(), wide_length) ==
         wide_length;
}
#endif

std::FILE* open_snapshot_file(const char* path, bool write) {
#ifdef _WIN32
  std::wstring wide_path;
  if (!utf8_path_to_wide(path, &wide_path)) {
    set_error("snapshot path must be valid UTF-8");
    return nullptr;
  }
  std::FILE* file = _wfopen(wide_path.c_str(), write ? L"wb" : L"rb");
#else
  std::FILE* file = std::fopen(path, write ? "wb" : "rb");
#endif
  if (!file) set_error("cannot open snapshot: %s", std::strerror(errno));
  return file;
}

void remove_snapshot_file(const char* path) noexcept {
#ifdef _WIN32
  try {
    std::wstring wide_path;
    if (utf8_path_to_wide(path, &wide_path)) _wremove(wide_path.c_str());
  } catch (...) {
  }
#else
  std::remove(path);
#endif
}

bool atomic_replace_snapshot_file(const char* temporary_path,
                                  const char* destination_path) {
#ifdef _WIN32
  std::wstring temporary_wide;
  std::wstring destination_wide;
  if (!utf8_path_to_wide(temporary_path, &temporary_wide) ||
      !utf8_path_to_wide(destination_path, &destination_wide)) {
    set_error("snapshot paths must be valid UTF-8");
    return false;
  }
  if (!MoveFileExW(temporary_wide.c_str(), destination_wide.c_str(),
                   MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
    set_error("cannot replace snapshot (Windows error %lu)",
              static_cast<unsigned long>(GetLastError()));
    return false;
  }
#else
  if (std::rename(temporary_path, destination_path) != 0) {
    set_error("cannot replace snapshot: %s", std::strerror(errno));
    return false;
  }
#endif
  return true;
}

std::string snapshot_temporary_path(const char* destination_path) {
#ifdef _WIN32
  const unsigned long process_id = GetCurrentProcessId();
#else
  const unsigned long process_id = static_cast<unsigned long>(getpid());
#endif
  const unsigned long long sequence = g_snapshot_temp_sequence.fetch_add(1) + 1;
  return std::string(destination_path) + ".tmp-" + std::to_string(process_id) +
         "-" + std::to_string(sequence);
}

// A malformed mobile-model page is different from an ordinary missing
// context.  Keep a dedicated exception type so every native entry point can
// preserve the fail-open signal instead of silently assigning a fallback
// probability.
class InvalidPageError : public std::runtime_error {
 public:
  InvalidPageError() : std::runtime_error("invalid n-gram page") {}
};

bool strict_mobile_validation_enabled() {
  const char* value = std::getenv("MOHU_TIGER_STRICT_VALIDATE");
  return value != nullptr && std::strcmp(value, "1") == 0;
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
  bool owned = true;  // false = 容器内视图，指向宿主映射，析构不 unmap
#ifdef _WIN32
  HANDLE mapping = nullptr;
#endif
  MappedFile() = default;
  MappedFile(MappedFile&& o) noexcept : data(o.data), size(o.size), owned(o.owned) {
#ifdef _WIN32
    mapping = o.mapping;
    o.mapping = nullptr;
#endif
    o.data = nullptr;
    o.size = 0;
    o.owned = true;
  }
  MappedFile& operator=(MappedFile&& o) noexcept {
    if (this != &o) {
      release();
      data = o.data;
      size = o.size;
      owned = o.owned;
#ifdef _WIN32
      mapping = o.mapping;
      o.mapping = nullptr;
#endif
      o.data = nullptr;
      o.size = 0;
      o.owned = true;
    }
    return *this;
  }
  // 在既有映射上开非持有视图（单文件容器内嵌多模型时复用同一次 mmap）。
  void set_borrowed_view(const uint8_t* base, size_t len) {
    release();
    data = const_cast<uint8_t*>(base);
    size = len;
    owned = false;
  }
  void release() {
    if (owned) {
#ifdef _WIN32
      if (data) {
        UnmapViewOfFile(data);
#ifdef TIGERENGINE_MAPPING_TEST
        ++g_mapping_unmap_count;
#endif
      }
      if (mapping) {
        CloseHandle(mapping);
#ifdef TIGERENGINE_MAPPING_TEST
        ++g_mapping_close_count;
#endif
      }
#else
      if (data) {
        munmap(data, size);
#ifdef TIGERENGINE_MAPPING_TEST
        ++g_mapping_unmap_count;
#endif
      }
#endif
    }
    data = nullptr;
    size = 0;
    owned = true;
#ifdef _WIN32
    mapping = nullptr;
#endif
  }
  bool open(const char* path) {
    release();
#ifdef _WIN32
    HANDLE file = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
      set_error("cannot open %s", path);
      return false;
    }
    LARGE_INTEGER file_size;
    if (!GetFileSizeEx(file, &file_size) || file_size.QuadPart <= 0) {
      set_error("cannot stat %s", path);
      CloseHandle(file);
      release();
      return false;
    }
    if (static_cast<unsigned long long>(file_size.QuadPart) >
        static_cast<unsigned long long>(SIZE_MAX)) {
      set_error("model is too large: %s", path);
      CloseHandle(file);
      release();
      return false;
    }
    size = (size_t)file_size.QuadPart;
    mapping = CreateFileMappingA(file, nullptr, PAGE_READONLY, 0, 0, nullptr);
    CloseHandle(file);
    if (mapping == nullptr) {
      set_error("cannot mmap %s", path);
      release();
      return false;
    }
    void* view = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
    if (view == nullptr) {
      CloseHandle(mapping);
      mapping = nullptr;
      set_error("cannot mmap %s", path);
      release();
      return false;
    }
    data = (uint8_t*)view;
    return true;
#else
    int fd = ::open(path, O_RDONLY);
    if (fd < 0) { set_error("cannot open %s", path); return false; }
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size <= 0) {
      set_error("cannot stat %s", path); ::close(fd); release(); return false;
    }
    size = (size_t)st.st_size;
    void* p = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
    ::close(fd);
    if (p == MAP_FAILED) { set_error("cannot mmap %s", path); release(); return false; }
    data = (uint8_t*)p;
    return true;
#endif
  }
  ~MappedFile() { release(); }
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
  bool invalid = false;
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
  std::unordered_set<int64_t> invalid_b_pages, invalid_t_pages;

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
    MappedFile mapped;
    if (!mapped.open(p)) {
      *this = KnModel{};
      return false;
    }
    return load_mapped(std::move(mapped), p);
  }

  // Publish a parsed model only after all metadata has loaded successfully.
  // The temporary owns the mapping while parsing, so every failure path releases
  // it without leaving stale pointers or cache entries in the target object.
  bool load_mapped(MappedFile&& mapped, const char* label) {
    KnModel fresh;
    fresh.file = std::move(mapped);
    fresh.path = label;
    if (!fresh.load_common(label)) {
      *this = KnModel{};
      return false;
    }
    *this = std::move(fresh);
    return true;
  }

  // 容器内视图加载：[base, base+len) 视作独立模型文件（内部偏移自洽）。
  bool load_view(const uint8_t* base, uint64_t len, const char* label) {
    if (len > (uint64_t)SIZE_MAX) {
      set_error("model view too large: %s", label);
      *this = KnModel{};
      return false;
    }
    MappedFile view;
    view.set_borrowed_view(base, (size_t)len);
    return load_mapped(std::move(view), label);
  }

  bool load_common(const char* p) {
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
    if (file.size < 104) {
      set_error("truncated mobile n-gram");
      return false;
    }
    size_t p = 8;
    auto u32 = [&]() { uint32_t v = rd_u32(d + p); p += 4; return v; };
    auto u64 = [&]() { uint64_t v = rd_u64(d + p); p += 8; return v; };
    const uint32_t version = u32();
    const uint32_t header_size = u32();
    const uint64_t declared_file_size = u64();
    const uint32_t raw_stride = u32();
    (void)u32();
    const uint32_t raw_uni_count = u32();
    (void)u32();
    const uint64_t uni_off = u32();
    (void)u32();
    const uint64_t bi_ctx = u32();
    const uint32_t raw_bi_index_count = u32();
    const uint64_t bi_blocks_off = u64();
    const uint64_t bi_index_off = u64();
    const uint64_t tri_ctx = u32();
    (void)u32();
    const uint32_t raw_tri_index_count = u32();
    (void)u32();
    const uint64_t tri_blocks_off = u64();
    const uint64_t tri_index_off = u64();

    if (version != 1 || header_size != 104 || declared_file_size != file.size ||
        raw_stride < 16 || raw_uni_count == 0) {
      set_error("invalid mobile n-gram header");
      return false;
    }
    const uint64_t stride = raw_stride;
    const uint64_t uni_count_u = raw_uni_count;
    const uint64_t bi_index_count_u = raw_bi_index_count;
    const uint64_t tri_index_count_u = raw_tri_index_count;
    const uint64_t expected_bi = bi_ctx == 0 ? 0 : (bi_ctx - 1) / stride + 1;
    const uint64_t expected_tri = tri_ctx == 0 ? 0 : (tri_ctx - 1) / stride + 1;
    if (expected_bi != bi_index_count_u || expected_tri != tri_index_count_u ||
        bi_ctx > static_cast<uint64_t>(INT64_MAX) ||
        tri_ctx > static_cast<uint64_t>(INT64_MAX) ||
        bi_index_count_u > static_cast<uint64_t>(INT64_MAX) ||
        tri_index_count_u > static_cast<uint64_t>(INT64_MAX)) {
      set_error("invalid mobile n-gram index count");
      return false;
    }
    auto checked_bytes = [](uint64_t count, uint64_t element, uint64_t* out) {
      if (element == 0 || count > UINT64_MAX / element) return false;
      *out = count * element;
      return true;
    };
    uint64_t uni_bytes = 0, bi_index_bytes = 0, tri_index_bytes = 0;
    if (!checked_bytes(uni_count_u, 8, &uni_bytes) ||
        !checked_bytes(bi_index_count_u, 16, &bi_index_bytes) ||
        !checked_bytes(tri_index_count_u, 16, &tri_index_bytes) ||
        uni_off < header_size || bi_blocks_off < uni_off ||
        bi_index_off < bi_blocks_off || tri_blocks_off < bi_index_off ||
        tri_index_off < tri_blocks_off || tri_index_off > file.size ||
        !range_ok(uni_off, uni_bytes) ||
        uni_bytes > bi_blocks_off - uni_off ||
        !range_ok(bi_index_off, bi_index_bytes) ||
        bi_index_bytes > tri_blocks_off - bi_index_off ||
        !range_ok(tri_index_off, tri_index_bytes) ||
        tri_index_bytes > file.size - tri_index_off) {
      set_error("invalid mobile n-gram section layout");
      return false;
    }

    auto validate_index = [&](uint64_t index_off, uint64_t index_count,
                              uint64_t section_start, uint64_t section_end) {
      uint64_t previous_key = 0;
      bool have_previous = false;
      for (uint64_t i = 0; i < index_count; ++i) {
        const uint64_t entry_delta = i * 16;  // count was checked above
        const uint64_t entry = index_off + entry_delta;
        const uint64_t key = rd_u64(d + entry);
        const uint64_t page = rd_u64(d + entry + 8);
        if (page < section_start || page > section_end ||
            16 > section_end - page ||
            (have_previous && key < previous_key)) {
          return false;
        }
        previous_key = key;
        have_previous = true;
      }
      return true;
    };
    if (!validate_index(bi_index_off, bi_index_count_u, bi_blocks_off, bi_index_off) ||
        !validate_index(tri_index_off, tri_index_count_u, tri_blocks_off, tri_index_off)) {
      set_error("invalid mobile n-gram page index");
      return false;
    }

    auto validate_pages = [&](uint64_t index_off, uint64_t index_count,
                              uint64_t context_count, uint64_t section_end) {
      for (uint64_t page = 0; page < index_count; ++page) {
        const uint64_t entry = index_off + page * 16;
        const uint64_t page_offset = rd_u64(d + entry + 8);
        const uint64_t consumed = page * stride;
        if (consumed >= context_count) return false;
        const uint64_t records = std::min<uint64_t>(stride, context_count - consumed);
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
    if (strict_mobile_validation_enabled() &&
        (!validate_pages(bi_index_off, bi_index_count_u, bi_ctx, bi_index_off) ||
         !validate_pages(tri_index_off, tri_index_count_u, tri_ctx, tri_index_off))) {
      set_error("mobile n-gram successor table is outside section");
      return false;
    }

    uni_count = static_cast<int64_t>(uni_count_u);
    index_stride = static_cast<int64_t>(stride);
    bi_index_count = static_cast<int64_t>(bi_index_count_u);
    tri_index_count = static_cast<int64_t>(tri_index_count_u);
    uni_base = d + uni_off;
    bi_ctx_total = static_cast<int64_t>(bi_ctx);
    tri_ctx_total = static_cast<int64_t>(tri_ctx);
    bi_index = d + bi_index_off;
    tri_index = d + tri_index_off;
    bi_section_end = bi_index_off;
    tri_section_end = tri_index_off;
    bi_section_start = bi_blocks_off;
    tri_section_start = tri_blocks_off;
    invalid_b_pages.clear();
    invalid_t_pages.clear();
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

  struct CtxResult {
    double lambda_ = 1.0;
    double prob = 0.0;
    bool observed = false;
    bool invalid = false;
  };

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
    return {lambda_, prob, observed, false};
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
    if (at > UINT64_MAX - index_offset || !range_ok(index_offset + at, 16)) return nullptr;
    const uint64_t offset = rd_u64(index_data + at + 8);
    if (offset < section_start || offset > section_end ||
        16 > section_end - offset) return nullptr;
    return file.data + offset;
  }

  CtxResult scan_successors(const uint8_t* data, size_t position, int64_t count,
                            double lambda_, uint32_t target, uint64_t section_end) const {
    if (!data || data < file.data || data > file.data + file.size || count < 0 ||
        section_end > file.size || static_cast<uint64_t>(count) > UINT64_MAX / 8) {
      return {lambda_, 0.0, false, true};
    }
    const uint64_t data_offset = static_cast<uint64_t>(data - file.data);
    if (data_offset > section_end || static_cast<uint64_t>(position) > section_end - data_offset ||
        static_cast<uint64_t>(count) * 8 >
            section_end - data_offset - static_cast<uint64_t>(position))
      return {lambda_, 0.0, false, true};
    int64_t lo = 0, hi = count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      if (rd_u32(data + position + mid * 8) < target) lo = mid + 1; else hi = mid;
    }
    if (lo < count) {
      const uint8_t* at = data + position + lo * 8;
      if (rd_u32(at) == target) {
        const double probability = rd_f32(at + 4);
        if (!std::isfinite(probability) || probability < 0.0)
          return {lambda_, 0.0, false, true};
        return {lambda_, probability, true, false};
      }
    }
    return {lambda_, 0.0, false, false};
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
    std::unordered_set<int64_t>& invalid_pages = trigram ? invalid_t_pages : invalid_b_pages;
    const uint8_t* index_data = trigram ? tri_index : bi_index;
    int64_t index_count = trigram ? tri_index_count : bi_index_count;
    uint64_t section_start = trigram ? tri_section_start : bi_section_start;
    uint64_t section_end = trigram ? tri_section_end : bi_section_end;
    int64_t context_total = trigram ? tri_ctx_total : bi_ctx_total;

    auto invalid = []() { return CtxResult{1.0, 0.0, false, true}; };
    auto missing = []() { return CtxResult{1.0, 0.0, false, false}; };
    if (CtxCacheEntry* c = cache.lookup(key)) {
      if (c->invalid) return invalid();
      if (c->missing) return missing();
      const uint8_t* data = page_base(index_data, index_count, c->page, section_start, section_end);
      if (!data) {
        invalid_pages.insert(c->page);
        c->invalid = true;
        return invalid();
      }
      CtxResult result = scan_successors(data, c->successor_position,
                                         c->successor_count, c->lambda_, target,
                                         section_end);
      if (result.invalid) {
        invalid_pages.insert(c->page);
        c->invalid = true;
      }
      return result;
    }
    int64_t page = find_page(index_data, index_count, key);
    if (page < 0) {
      cache.remember(key, {true, -1, 1.0, 0, 0});
      return missing();
    }
    if (invalid_pages.find(page) != invalid_pages.end()) return invalid();
    const uint8_t* data = page_base(index_data, index_count, page, section_start, section_end);
    if (!data) {
      invalid_pages.insert(page);
      cache.remember(key, {false, page, 1.0, 0, 0, true});
      return invalid();
    }
    if (context_total < 0 || index_stride < 1 ||
        static_cast<uint64_t>(page) > UINT64_MAX /
            static_cast<uint64_t>(index_stride)) {
      invalid_pages.insert(page);
      cache.remember(key, {false, page, 1.0, 0, 0, true});
      return invalid();
    }
    const uint64_t page_start = static_cast<uint64_t>(page) *
                                static_cast<uint64_t>(index_stride);
    if (page_start >= static_cast<uint64_t>(context_total))
      return missing();
    const uint64_t remaining_u = std::min<uint64_t>(
        static_cast<uint64_t>(index_stride),
        static_cast<uint64_t>(context_total) - page_start);
    if (remaining_u > static_cast<uint64_t>(INT64_MAX)) {
      invalid_pages.insert(page);
      cache.remember(key, {false, page, 1.0, 0, 0, true});
      return invalid();
    }
    const int64_t remaining = static_cast<int64_t>(remaining_u);
    size_t position = 0;
    uint64_t previous_context_key = 0;
    bool have_previous_context = false;
    for (int64_t i = 0; i < remaining; ++i) {
      const uint64_t data_offset = static_cast<uint64_t>(data - file.data);
      const uint64_t position_u = static_cast<uint64_t>(position);
      if (data_offset > section_end || position_u > section_end - data_offset ||
          16 > section_end - data_offset - position_u) {
        invalid_pages.insert(page);
        cache.remember(key, {false, page, 1.0, 0, 0, true});
        return invalid();
      }
      uint64_t context_key = rd_u64(data + position);
      double lambda_ = rd_f32(data + position + 8);
      int32_t succ = rd_i32(data + position + 12);
      if ((have_previous_context && context_key < previous_context_key) ||
          !std::isfinite(lambda_) || lambda_ < 0.0 || succ < 0 ||
          static_cast<uint64_t>(succ) > UINT64_MAX / 8 ||
          static_cast<uint64_t>(succ) * 8 >
              section_end - data_offset - position_u - 16) {
        invalid_pages.insert(page);
        cache.remember(key, {false, page, 1.0, 0, 0, true});
        return invalid();
      }
      previous_context_key = context_key;
      have_previous_context = true;
      position += 16;
      if (context_key == key) {
        cache.remember(key, {false, page, lambda_, succ, position});
        CtxResult result = scan_successors(data, position, succ, lambda_, target,
                                           section_end);
        if (result.invalid) {
          invalid_pages.insert(page);
          if (CtxCacheEntry* cached = cache.lookup(key)) cached->invalid = true;
        }
        return result;
      }
      if (context_key > key) break;
      position += (size_t)succ * 8;
    }
    cache.remember(key, {true, -1, 1.0, 0, 0});
    return missing();
  }

  CtxResult lookup_context(bool trigram, uint64_t key_u64, uint32_t key_u32, uint32_t target) {
    if (!mobile) return legacy_lookup(trigram, key_u64, key_u32);
    return mobile_lookup(trigram, key_u64, target);
  }

  double logp(uint32_t a, uint32_t b, uint32_t c) {
    double unigram = lookup_unigram(c);
    KnModel::CtxResult bi = lookup_context(false, (uint64_t)b, b, c);
    if (bi.invalid) throw InvalidPageError();
    if (!std::isfinite(unigram) || unigram < 0.0 ||
        !std::isfinite(bi.prob) || bi.prob < 0.0 ||
        !std::isfinite(bi.lambda_) || bi.lambda_ < 0.0) {
      return std::numeric_limits<double>::quiet_NaN();
    }
    double bigram = bi.prob + bi.lambda_ * unigram;
    KnModel::CtxResult tri = lookup_context(true, pack2(a, b), (uint32_t)pack2(a, b), c);
    if (tri.invalid) throw InvalidPageError();
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
    CtxResult result = lookup_context(false, (uint64_t)a, a, b);
    if (result.invalid) throw InvalidPageError();
    return result.observed;
  }
};

// ---------------------------------------------------------------- 词级模型

// MHKNM01：词级 Kneser-Ney 三元 + 词义向量（魔虎音形词场景深度定制）。
// 与字符级 TCSKNM 的区别：解码按「码表词条」整词转移打分，与魔虎
// script/beam 的组词过程同构；未观察搭配由词义向量余弦回退，解决
// 「申请很迷茫 vs 神情很迷茫」这类频率无法裁决的同音竞争。
struct WordModel {
  MappedFile file;
  std::string path;
  std::vector<std::string> vocab;            // id -> 词（0=<s> 1=</s>）
  std::unordered_map<std::string, uint32_t> ids;
  std::vector<float> uni;                    // id -> P(w)
  uint32_t emb_dim = 0;
  std::vector<int8_t> emb_q;                 // id*dim 量化向量
  std::vector<float> emb_scale;              // id -> scale
  std::vector<float> emb_norm;               // id -> 单位化范数缓存
  double beta_sem = 0.8;

  int64_t index_stride = 64;
  const uint8_t *bi_index = nullptr, *tri_index = nullptr;
  int64_t bi_index_count = 0, tri_index_count = 0;
  uint64_t bi_start = 0, bi_end = 0, tri_start = 0, tri_end = 0;
  int64_t bi_ctx_total = 0, tri_ctx_total = 0;
  FifoCache<uint64_t> cache_b{16384}, cache_t{16384};

  static uint32_t rd_u32_at(const uint8_t* p) { return rd_u32(p); }
  static uint64_t rd_u64_at(const uint8_t* p) { return rd_u64(p); }
  static float rd_f32_at(const uint8_t* p) { return rd_f32(p); }

  bool range_ok(uint64_t offset, uint64_t length) const {
    return offset <= file.size && length <= static_cast<uint64_t>(file.size) - offset;
  }

  const uint8_t* page_base(const uint8_t* index_data, int64_t index_count,
                           int64_t page, uint64_t sstart, uint64_t send) const {
    if (!index_data || page < 0 || page >= index_count || sstart > send ||
        send > file.size || index_data < file.data ||
        index_data > file.data + file.size ||
        static_cast<uint64_t>(page) > UINT64_MAX / 16)
      return nullptr;
    const uint64_t index_offset = static_cast<uint64_t>(index_data - file.data);
    const uint64_t at = static_cast<uint64_t>(page) * 16;
    if (index_offset + at + 16 > file.size) return nullptr;
    const uint64_t offset = rd_u64(index_data + at + 8);
    if (offset < sstart || offset >= send || offset + 16 > send) return nullptr;
    return file.data + offset;
  }

  int64_t find_page(const uint8_t* index_data, int64_t index_count,
                    uint64_t key) const {
    int64_t lo = 0, hi = index_count;
    while (lo < hi) {
      int64_t mid = lo + (hi - lo) / 2;
      if (rd_u64(index_data + mid * 16) <= key) lo = mid + 1; else hi = mid;
    }
    return lo - 1;
  }

  struct Ctx { double lambda_; double prob; bool observed; };

  Ctx scan(const uint8_t* data, size_t position, int64_t count, double lambda_,
           uint32_t target, uint64_t send) const {
    if (!data || count <= 0) return {lambda_, 0.0, false};
    const uint64_t base = (uint64_t)(data - file.data) + position;
    if (base + (uint64_t)count * 8 > send) return {lambda_, 0.0, false};
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

  Ctx lookup(bool trigram, uint64_t key, uint32_t target) {
    FifoCache<uint64_t>& cache = trigram ? cache_t : cache_b;
    const uint8_t* index_data = trigram ? tri_index : bi_index;
    int64_t index_count = trigram ? tri_index_count : bi_index_count;
    uint64_t sstart = trigram ? tri_start : bi_start;
    uint64_t send = trigram ? tri_end : bi_end;
    int64_t ctx_total = trigram ? tri_ctx_total : bi_ctx_total;
    if (CtxCacheEntry* c = cache.lookup(key)) {
      if (c->missing) return {1.0, 0.0, false};
      const uint8_t* data =
          page_base(index_data, index_count, c->page, sstart, send);
      if (!data) return {1.0, 0.0, false};
      return scan(data, c->successor_position, c->successor_count, c->lambda_,
                  target, send);
    }
    int64_t page = find_page(index_data, index_count, key);
    if (page < 0) {
      cache.remember(key, {true, -1, 1.0, 0, 0});
      return {1.0, 0.0, false};
    }
    const uint8_t* data = page_base(index_data, index_count, page, sstart, send);
    if (!data) return {1.0, 0.0, false};
    const uint64_t page_start = (uint64_t)page * (uint64_t)index_stride;
    if (page_start >= (uint64_t)ctx_total) return {1.0, 0.0, false};
    const int64_t remaining = (int64_t)std::min<uint64_t>(
        (uint64_t)index_stride, (uint64_t)ctx_total - page_start);
    size_t position = 0;
    for (int64_t i = 0; i < remaining; ++i) {
      uint64_t context_key = rd_u64(data + position);
      double lambda_ = rd_f32(data + position + 8);
      int32_t succ = rd_i32(data + position + 12);
      if (succ < 0) return {1.0, 0.0, false};
      position += 16;
      if (context_key == key) {
        cache.remember(key, {false, page, lambda_, succ, position});
        return scan(data, position, succ, lambda_, target, send);
      }
      if (context_key > key) break;
      position += (size_t)succ * 8;
    }
    cache.remember(key, {true, -1, 1.0, 0, 0});
    return {1.0, 0.0, false};
  }

  double semantic(uint32_t w, uint32_t ctx_word) const {
    if (emb_dim == 0 || w >= emb_norm.size() || ctx_word >= emb_norm.size())
      return 0.0;
    const int8_t* a = emb_q.data() + (size_t)w * emb_dim;
    const int8_t* b = emb_q.data() + (size_t)ctx_word * emb_dim;
    double dot = 0;
    for (uint32_t k = 0; k < emb_dim; ++k) dot += (double)a[k] * (double)b[k];
    // 量化还原：真实点积 = Σ(qa·qb)·scale_a·scale_b
    dot *= (double)emb_scale[w] * (double)emb_scale[ctx_word];
    double denom = emb_norm[w] * emb_norm[ctx_word];
    if (denom <= 0) return 0.0;
    double cos = dot / denom;
    if (cos > 1.0) cos = 1.0;
    if (cos < -1.0) cos = -1.0;
    return cos;
  }

  double logp(uint32_t a, uint32_t b, uint32_t c) {
    double u = c < uni.size() ? uni[c] : 1e-9;
    double sem = semantic(c, b);
    if (sem > 0) u *= std::exp(beta_sem * sem);
    Ctx bi = lookup(false, (uint64_t)b, c);
    if (!(u >= 0.0) || !(bi.prob >= 0.0) || !(bi.lambda_ >= 0.0))
      return std::numeric_limits<double>::quiet_NaN();
    double bigram = bi.prob + bi.lambda_ * u;
    Ctx tri = lookup(true, ((uint64_t)a << 32) | b, c);
    if (!(bigram >= 0.0) || !(tri.prob >= 0.0) || !(tri.lambda_ >= 0.0))
      return std::numeric_limits<double>::quiet_NaN();
    double p = tri.prob + tri.lambda_ * bigram;
    if (!(p >= 0.0)) return std::numeric_limits<double>::quiet_NaN();
    if (p < 1e-300) p = 1e-300;
    return std::log(p);
  }

  bool load(const char* p) {
    MappedFile mapped;
    if (!mapped.open(p)) {
      *this = WordModel{};
      return false;
    }
    return load_mapped(std::move(mapped), p);
  }

  // Publish a parsed model only after all metadata has loaded successfully.
  // The temporary owns the mapping while parsing, so every failure path releases
  // it without leaving stale pointers or cache entries in the target object.
  bool load_mapped(MappedFile&& mapped, const char* label) {
    WordModel fresh;
    fresh.file = std::move(mapped);
    fresh.path = label;
    if (!fresh.load_common(label)) {
      *this = WordModel{};
      return false;
    }
    *this = std::move(fresh);
    return true;
  }

  // 容器内视图加载：与非持有 MappedFile 视图配合，从单文件容器取词级层。
  bool load_view(const uint8_t* base, uint64_t len, const char* label) {
    if (len > (uint64_t)SIZE_MAX) {
      set_error("model view too large: %s", label);
      *this = WordModel{};
      return false;
    }
    MappedFile view;
    view.set_borrowed_view(base, (size_t)len);
    return load_mapped(std::move(view), label);
  }

  bool load_common(const char* p) {
    const uint8_t* d = file.data;
    if (file.size < 120 || std::memcmp(d, "MHKNM01", 7) != 0) {
      set_error("not an MHKNM01 model: %s", p);
      return false;
    }
    size_t q = 8;
    auto u32 = [&]() { uint32_t v = rd_u32(d + q); q += 4; return v; };
    auto u64 = [&]() { uint64_t v = rd_u64(d + q); q += 8; return v; };
    auto f32 = [&]() { float v = rd_f32(d + q); q += 4; return v; };
    const uint32_t version = u32();
    const uint32_t header_size = u32();
    const uint64_t file_size = u64();
    const uint32_t vocab_count = u32();
    emb_dim = u32();
    index_stride = u32();
    (void)u32();  // flags
    const uint64_t vocab_off = u64();
    const uint64_t uni_off = u64();
    const uint64_t bi_blocks_off = u64();
    const uint64_t bi_index_off = u64();
    const uint64_t tri_blocks_off = u64();
    const uint64_t emb_off = u64();
    const uint32_t bi_ctx_n = u32();
    const uint32_t tri_ctx_n = u32();
    const uint32_t bi_idx_n = u32();
    const uint32_t tri_idx_n = u32();
    beta_sem = f32();
    if (version != 1 || header_size != 120 || file_size != file.size ||
        emb_dim == 0 || emb_dim > 512 || vocab_count < 2 || index_stride == 0 ||
        vocab_off < header_size || vocab_off > file.size || uni_off > file.size ||
        bi_blocks_off > file.size || bi_index_off > file.size ||
        tri_blocks_off > file.size || emb_off > file.size) {
      set_error("invalid MHKNM01 header: %s", p);
      return false;
    }
    if (static_cast<uint64_t>(vocab_count) > UINT64_MAX / 4) {
      set_error("MHKNM01 unigram size overflow: %s", p);
      return false;
    }
    const uint64_t uni_bytes = static_cast<uint64_t>(vocab_count) * 4;
    if (!range_ok(uni_off, uni_bytes)) {
      set_error("invalid MHKNM01 unigram range: %s", p);
      return false;
    }
    // vocab
    size_t pos = static_cast<size_t>(vocab_off);
    vocab.clear();
    vocab.reserve(vocab_count);
    for (uint32_t i = 0; i < vocab_count; ++i) {
      if (!range_ok(pos, 4)) { set_error("vocab truncated"); return false; }
      uint32_t len = rd_u32(d + pos);
      pos += 4;
      if (!range_ok(pos, len)) { set_error("vocab entry truncated"); return false; }
      vocab.emplace_back((const char*)(d + pos), len);
      pos += len;
    }
    if (uni_off > bi_blocks_off || uni_bytes > bi_blocks_off - uni_off ||
        static_cast<uint64_t>(pos) > uni_off ||
        static_cast<uint64_t>(bi_idx_n) > UINT64_MAX / 16 ||
        static_cast<uint64_t>(tri_idx_n) > UINT64_MAX / 16) {
      set_error("invalid MHKNM01 vocabulary/unigram layout");
      return false;
    }
    ids.clear();
    ids.reserve(vocab.size() * 2);
    for (uint32_t i = 0; i < vocab.size(); ++i) ids[vocab[i]] = i;
    // unigram
    uni.resize(vocab_count);
    for (uint32_t i = 0; i < vocab_count; ++i) uni[i] = rd_f32(d + uni_off + i * 4);

    if (static_cast<uint64_t>(bi_idx_n) > UINT64_MAX / 16 ||
        static_cast<uint64_t>(tri_idx_n) > UINT64_MAX / 16) {
      set_error("MHKNM01 index count overflow");
      return false;
    }
    const uint64_t bi_index_bytes = static_cast<uint64_t>(bi_idx_n) * 16;
    const uint64_t tri_index_bytes = static_cast<uint64_t>(tri_idx_n) * 16;
    if (uni_off > bi_blocks_off ||
        uni_bytes > bi_blocks_off - uni_off ||
        bi_index_off < bi_blocks_off ||
        bi_index_bytes > file.size - bi_index_off ||
        bi_index_off > tri_blocks_off ||
        bi_index_bytes > tri_blocks_off - bi_index_off ||
        tri_blocks_off > emb_off ||
        (tri_idx_n > 0 && tri_index_bytes > emb_off) ||
        (tri_idx_n > 0 && emb_off - tri_index_bytes < tri_blocks_off)) {
      set_error("invalid MHKNM01 section layout");
      return false;
    }
    const uint64_t tri_index_off = emb_off - tri_index_bytes;
    if (tri_index_off < tri_blocks_off ||
        !range_ok(tri_index_off, tri_index_bytes) ||
        emb_off < tri_index_off ||
        tri_index_bytes != emb_off - tri_index_off) {
      set_error("invalid MHKNM01 trigram index range");
      return false;
    }
    const uint64_t emb_item_bytes = static_cast<uint64_t>(emb_dim) + 4;
    if (static_cast<uint64_t>(vocab_count) > UINT64_MAX / emb_item_bytes) {
      set_error("MHKNM01 embedding size overflow");
      return false;
    }
    const uint64_t emb_bytes = static_cast<uint64_t>(vocab_count) * emb_item_bytes;
    if (!range_ok(emb_off, emb_bytes) || emb_bytes != file.size - emb_off) {
      set_error("emb truncated");
      return false;
    }
    if (bi_ctx_n > static_cast<uint32_t>(INT64_MAX) ||
        tri_ctx_n > static_cast<uint32_t>(INT64_MAX) ||
        bi_idx_n > static_cast<uint32_t>(INT64_MAX) ||
        tri_idx_n > static_cast<uint32_t>(INT64_MAX)) {
      set_error("MHKNM01 count exceeds runtime limits");
      return false;
    }
    auto validate_index = [&](uint64_t index_off, uint32_t count,
                              uint64_t section_start, uint64_t section_end) {
      uint64_t previous_key = 0;
      bool have_previous = false;
      for (uint32_t i = 0; i < count; ++i) {
        const uint64_t entry = index_off + static_cast<uint64_t>(i) * 16;
        const uint64_t key = rd_u64(d + entry);
        const uint64_t page = rd_u64(d + entry + 8);
        if ((have_previous && key < previous_key) ||
            page < section_start || page >= section_end || !range_ok(page, 16)) return false;
        previous_key = key;
        have_previous = true;
      }
      return true;
    };
    if (!validate_index(bi_index_off, bi_idx_n, bi_blocks_off, tri_blocks_off) ||
        !validate_index(tri_index_off, tri_idx_n, tri_blocks_off, emb_off)) {
      set_error("MHKNM01 page index is outside section");
      return false;
    }
    auto validate_pages = [&](uint64_t index_off, uint32_t index_count,
                              uint32_t context_count, uint64_t section_end) {
      for (uint32_t page = 0; page < index_count; ++page) {
        const uint64_t entry = index_off + static_cast<uint64_t>(page) * 16;
        const uint64_t page_offset = rd_u64(d + entry + 8);
        const uint64_t consumed = static_cast<uint64_t>(page) * index_stride;
        if (consumed >= context_count) continue;
        const uint64_t records = std::min<uint64_t>(index_stride, context_count - consumed);
        uint64_t position = page_offset;
        for (uint64_t record = 0; record < records; ++record) {
          if (position > section_end || section_end - position < 16) return false;
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
    if (!validate_pages(bi_index_off, bi_idx_n, bi_ctx_n, tri_blocks_off) ||
        !validate_pages(tri_index_off, tri_idx_n, tri_ctx_n, emb_off)) {
      set_error("MHKNM01 successor table is outside section");
      return false;
    }
    bi_start = bi_blocks_off;
    bi_end = bi_index_off;
    bi_index = d + bi_index_off;
    bi_index_count = bi_idx_n;
    bi_ctx_total = bi_ctx_n;
    tri_start = tri_blocks_off;
    tri_end = tri_index_off;
    tri_index = d + tri_index_off;
    tri_index_count = tri_idx_n;
    tri_ctx_total = tri_ctx_n;
    if (getenv("MHDBG")) {
      fprintf(stderr,
              "[MHDBG] vocab=%zu dim=%u stride=%lld beta=%.2f uni[0]=%.3g "
              "biB@%llu biI@%llu(%lld) triB@%llu triI@%llu(%lld) emb@%llu\n",
              vocab.size(), emb_dim, (long long)index_stride, beta_sem,
              uni.size() > 2 ? uni[2] : -1.0,
              (unsigned long long)bi_blocks_off, (unsigned long long)bi_index_off,
              (long long)bi_index_count, (unsigned long long)tri_blocks_off,
              (unsigned long long)tri_index_off,
              (long long)tri_index_count, (unsigned long long)emb_off);
    }
    // 词向量
    if (static_cast<uint64_t>(vocab_count) > SIZE_MAX / emb_dim) {
      set_error("MHKNM01 embedding allocation overflow");
      return false;
    }
    emb_q.resize(static_cast<size_t>(vocab_count) * emb_dim);
    emb_scale.resize(vocab_count);
    emb_norm.resize(vocab_count);
    for (uint32_t i = 0; i < vocab_count; ++i) {
      const uint8_t* base = d + emb_off + static_cast<uint64_t>(i) * emb_item_bytes;
      std::memcpy(emb_q.data() + static_cast<size_t>(i) * emb_dim, base, emb_dim);
      emb_scale[i] = rd_f32(base + emb_dim);
      double n2 = 0;
      for (uint32_t k = 0; k < emb_dim; ++k) {
        double v = (double)((const int8_t*)base)[k] * emb_scale[i];
        n2 += v * v;
      }
      emb_norm[i] = std::sqrt(n2);
    }
    return true;
  }
};

// ---------------------------------------------------------------- 码表

struct LexEntry {
  std::string text;
  int rank;
  std::vector<uint32_t> chars;  // 预拆码点
  bool personal = false;
  double personal_boost = 0.0;
  // 读音先验 log P(该读音|该字)：由码表可选第 5 列（读音条件简频）在
  // 装载期按 (字, 双拼) 去重归一得出，如「万」mò ≈ log(1/1.2M) 而
  // wàn ≈ 0。缺列（旧码表、多字词、个人词）保持 0 = 中性。
  double reading_prior = 0.0;
  int reading_freq_raw = -1;
};

struct Lexicon {
  std::unordered_map<std::string, std::vector<LexEntry>> codes;
  std::vector<int> lengths;                              // 去重升序
  std::unordered_set<std::string> proper_prefixes;
  std::unordered_map<std::string, int> freq_rank;        // text -> 名次
  int max_code_len = 1;
  bool has_multi_char_entries = false;
  std::unordered_map<std::string, std::vector<LexEntry>> base_codes;
  std::unordered_map<std::string, int> base_freq_rank;

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
      // 可选第 5 列：读音条件简频（同一字罕用读音接近 0，主读音大）。
      int reading_freq = cols.size() > 4 ? parse_int(cols[4], -1) : -1;
      if (code.empty() || text.empty()) continue;
      LexEntry e;
      e.text = text;
      e.rank = rank < 1 ? 1 : rank;
      e.reading_freq_raw = reading_freq < 0 ? -1 : reading_freq;
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
    finalize_reading_priors();
    base_codes = codes;
    base_freq_rank = freq_rank;
    return !codes.empty();
  }

  // 先验 = log((f+0.5)/(total+0.5))，f 为该 (字, 双拼音节) 的读音简频，
  // total 为该字全部读音简频之和（同一音节多码形去重取最大，避免重复
  // 计数）。+0.5 平滑使 f=0 的读音只受有限惩罚、单一读音的字为 0。
  void finalize_reading_priors() {
    std::unordered_map<std::string,
        std::unordered_map<std::string, long long>> per_text;
    for (const auto& kv : codes) {
      const std::string syllable = kv.first.substr(0, 2);
      for (const LexEntry& e : kv.second) {
        if (e.chars.size() != 1 || e.reading_freq_raw < 0) continue;
        long long& slot = per_text[e.text][syllable];
        if (e.reading_freq_raw > slot) slot = e.reading_freq_raw;
      }
    }
    std::unordered_map<std::string, long long> totals;
    for (const auto& t : per_text) {
      long long total = 0;
      for (const auto& s : t.second) total += s.second;
      totals[t.first] = total;
    }
    for (auto& kv : codes) {
      for (LexEntry& e : kv.second) {
        if (e.chars.size() != 1 || e.reading_freq_raw < 0) continue;
        auto it = totals.find(e.text);
        if (it == totals.end()) continue;
        e.reading_prior = std::log(((double)e.reading_freq_raw + 0.5) /
                                   ((double)it->second + 0.5));
      }
    }
  }

  static bool valid_personal_row(const std::string& code, const std::string& text) {
    if (code.size() < 4 || code.size() > 128 || text.empty() || text.size() > 192)
      return false;
    for (char c : code)
      if (c < 'a' || c > 'z') return false;
    for (size_t i = 0; i < text.size();) {
      uint32_t cp = 0;
      size_t n = 0;
      utf8_next(text.data(), text.size(), i, &cp, &n);
      if (n == 0 || cp == 0xFFFD || cp < 0x20 || cp == 0x7f) return false;
      i += n;
    }
    return true;
  }

  void rebuild_metadata() {
    lengths.clear();
    proper_prefixes.clear();
    max_code_len = 1;
    has_multi_char_entries = false;
    std::unordered_set<int> length_set;
    for (const auto& kv : codes) {
      const std::string& code = kv.first;
      max_code_len = std::max(max_code_len, static_cast<int>(code.size()));
      length_set.insert(static_cast<int>(code.size()));
      for (size_t l = 1; l < code.size(); ++l)
        proper_prefixes.insert(code.substr(0, l));
      for (const auto& entry : kv.second)
        if (entry.chars.size() != 1) has_multi_char_entries = true;
    }
    lengths.assign(length_set.begin(), length_set.end());
    std::sort(lengths.begin(), lengths.end());
  }

  // 增量刷新状态：上次成功应用的负载原文与生效个人词键集（code\ttext -> boost）。
  std::string personal_payload;
  std::unordered_map<std::string, double> personal_boosts;
  std::unordered_map<std::string, int> personal_counts;

  struct PersonalRow {
    std::string code;
    std::string text;
    std::string key;
    int commits;
    double boost;
  };

  static double personal_boost_for_commits(int commits) {
    if (commits <= 0) return 0.0;
    const int bounded = std::min(commits, 1000000);
    // Calibrated against native path gaps: one selection is visible but small;
    // repeated selections can eventually beat an unrelated segmentation.
    return std::min(12.0, std::log1p(static_cast<double>(bounded)) * 5.0);
  }

  // 个人词新增后增量登记编码元数据，等价于 rebuild_metadata 对该码的部分效果。
  void note_personal_code(const std::string& code) {
    const int length = static_cast<int>(code.size());
    if (std::find(lengths.begin(), lengths.end(), length) == lengths.end()) {
      lengths.insert(std::upper_bound(lengths.begin(), lengths.end(), length), length);
    }
    if (length > max_code_len) max_code_len = length;
    for (size_t l = 1; l < code.size(); ++l)
      proper_prefixes.insert(code.substr(0, l));
  }

  // 应用一行个人词：命中静态同码同词则只叠加 boost，否则新增个人条目并登记元数据。
  void apply_personal_row(const PersonalRow& row) {
    auto& entries = codes[row.code];
    for (auto& existing : entries) {
      if (existing.text == row.text) {
        existing.personal_boost = row.boost;
        existing.personal = true;
        return;
      }
    }
    LexEntry entry;
    entry.text = row.text;
    entry.rank = 1;
    entry.personal = true;
    entry.personal_boost = row.boost;
    for (auto& ch : utf8_split(row.text)) {
      uint32_t cp;
      size_t n;
      utf8_next(ch.data(), ch.size(), 0, &cp, &n);
      entry.chars.push_back(cp);
    }
    if (entry.chars.size() != 1) has_multi_char_entries = true;
    entries.push_back(std::move(entry));
    note_personal_code(row.code);
  }

  // 解析负载行到 parsed/index；require_terminated 为 true 时要求块以
  // 换行结尾（分片 append 协议用它防止半行被拆到两个块里）。
  // rows 为空指针返回 false，其余一律产出已解析行（非法行照旧跳过）。
  static bool parse_personal_rows(const char* rows, bool require_terminated,
                                  std::unordered_map<std::string, size_t>& index,
                                  std::vector<PersonalRow>& parsed) {
    if (!rows) return false;
    const size_t payload_size = std::strlen(rows);
    if (require_terminated && payload_size > 0 && rows[payload_size - 1] != '\n')
      return false;
    size_t start = 0;
    while (start < payload_size) {
      size_t end = start;
      while (end < payload_size && rows[end] != '\n') ++end;
      std::string line(rows + start, end - start);
      start = end + (end < payload_size ? 1 : 0);
      if (line.empty()) continue;
      size_t a = line.find('\t');
      size_t b = a == std::string::npos ? std::string::npos : line.find('\t', a + 1);
      if (a == std::string::npos || b == std::string::npos || line.find('\t', b + 1) != std::string::npos)
        continue;
      PersonalRow row;
      row.code = line.substr(0, a);
      row.text = line.substr(a + 1, b - a - 1);
      int commits = Lexicon::parse_int(line.substr(b + 1), 0);
      if (commits <= 0 || !valid_personal_row(row.code, row.text)) continue;
      row.key = row.code + "\t" + row.text;
      row.commits = commits;
      row.boost = personal_boost_for_commits(commits);
      if (index.emplace(row.key, parsed.size()).second)
        parsed.push_back(std::move(row));
    }
    return true;
  }

  // 应用已解析的行集（整体替换语义）：旧键全部保留时走增量路径，
  // 只加新条目并更新 boost；键集收缩则恢复基线并整表重建。
  // *changed 指示是否发生了任何实际变化。
  void apply_personal_parsed(const std::unordered_map<std::string, size_t>& index,
                             const std::vector<PersonalRow>& parsed,
                             bool* changed) {
    bool incremental = true;
    for (const auto& applied : personal_boosts) {
      if (index.find(applied.first) == index.end()) {
        incremental = false;
        break;
      }
    }

    if (incremental) {
      for (const auto& row : parsed) {
        auto existing = personal_boosts.find(row.key);
        if (existing != personal_boosts.end()) {
          personal_counts[row.key] = row.commits;
          if (existing->second != row.boost) {
            auto bucket = codes.find(row.code);
            if (bucket != codes.end()) {
              for (auto& entry : bucket->second) {
                if (entry.text == row.text) {
                  entry.personal_boost = row.boost;
                  break;
                }
              }
            }
            existing->second = row.boost;
            *changed = true;
          }
          continue;
        }
        apply_personal_row(row);
        personal_boosts[row.key] = row.boost;
        personal_counts[row.key] = row.commits;
        *changed = true;
      }
    } else {
      codes = base_codes;
      freq_rank = base_freq_rank;
      personal_boosts.clear();
      personal_counts.clear();
      for (const auto& row : parsed) {
        apply_personal_row(row);
        personal_boosts[row.key] = row.boost;
        personal_counts[row.key] = row.commits;
      }
      rebuild_metadata();
      *changed = true;
    }
  }

  // 返回 0 = 负载与上次相同（未变更，调用方可保留解码缓存），
  // 1 = 已应用，-1 = 参数错误。整体路径；分片路径见下方事务接口。
  int set_personal(const char* rows) {
    if (!rows) return -1;
    const size_t payload_size = std::strlen(rows);
    if (personal_payload == std::string(rows, payload_size)) return 0;
    personal_txn.reset();  // 整体调用与挂起事务互斥：以整体结果为准
    std::vector<PersonalRow> parsed;
    std::unordered_map<std::string, size_t> index;
    if (!parse_personal_rows(rows, false, index, parsed)) return -1;
    bool changed = false;
    apply_personal_parsed(index, parsed, &changed);
    personal_payload.assign(rows, payload_size);
    return 1;
  }

  // Apply one positive commit immediately without scanning the userdb.  The
  // next full snapshot still reconciles deletions and sync changes.
  int adjust_personal(const std::string& code, const std::string& text, int delta) {
    if (delta <= 0 || !valid_personal_row(code, text)) return -1;
    const std::string key = code + "\t" + text;
    const int previous = personal_counts.count(key) ? personal_counts[key] : 0;
    if (previous > 1000000 - delta) return -1;
    const int commits = previous + delta;
    PersonalRow row{code, text, key, commits, personal_boost_for_commits(commits)};

    auto applied = personal_boosts.find(key);
    if (applied == personal_boosts.end()) {
      apply_personal_row(row);
    } else {
      auto bucket = codes.find(code);
      if (bucket == codes.end()) return -1;
      bool found = false;
      for (auto& entry : bucket->second) {
        if (entry.text == text) {
          entry.personal_boost = row.boost;
          found = true;
          break;
        }
      }
      if (!found) apply_personal_row(row);
    }
    personal_boosts[key] = row.boost;
    personal_counts[key] = commits;
    personal_payload.clear();
    return 1;
  }

  // ---- 分片事务：begin / append*(整行块) / commit|abort ----
  // 事务期间解码始终使用已应用的旧快照；commit 原子切换。
  // 增长路径解析成本随 append 摊销，commit 只剩哈希层比对与应用。
  struct PersonalTxn {
    std::unordered_map<std::string, size_t> index;
    std::vector<PersonalRow> parsed;
  };
  std::unique_ptr<PersonalTxn> personal_txn;

  int personal_begin() {
    personal_txn = std::make_unique<PersonalTxn>();  // 隐式丢弃旧事务
    // 按当前已应用规模预留容量：扩容 rehash 若落在单次 append 上会造成
    // 远超分片预算的尖刺（实测 50 万条时约 20ms）。
    const size_t expected = personal_boosts.size() + 4096;
    personal_txn->index.reserve(expected);
    personal_txn->parsed.reserve(expected);
    return 0;
  }

  int personal_append(const char* rows) {
    if (!personal_txn || !rows) return -1;
    if (!parse_personal_rows(rows, true, personal_txn->index, personal_txn->parsed))
      return -1;
    return 0;
  }

  // 返回 0 = 无变化（可保留解码缓存），1 = 已应用，-1 = 无事务或出错。
  int personal_commit() {
    if (!personal_txn) return -1;
    bool changed = false;
    apply_personal_parsed(personal_txn->index, personal_txn->parsed, &changed);
    personal_txn.reset();
    // 分片路径不持有整串负载；置空保证后续整体调用不会误判 no-op。
    personal_payload.clear();
    return changed ? 1 : 0;
  }

  int personal_abort() {
    personal_txn.reset();
    return 0;
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
// 词级模式的每字奖励（实验可调；默认与字符模式一致）
inline double word_char_reward() {
  static const double cached = []() {
    const char* env = getenv("MH_WORD_REWARD");
    double v = env ? atof(env) : kCharReward;
    return v > 0 ? v : kCharReward;
  }();
  return cached;
}
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
  uint32_t pw2 = 0, pw1 = 0;   // 词级上下文（0 = <s>）
  int max_rank = 1;
  int edges = 0;  // 消费的词典条目数；≤4 键排序时“整段单条命中”优先
  const State* previous = nullptr;
  size_t text_length = 0;   // 字节
  size_t raw_length = 0;
  bool personal = false;
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
  bool personal = false;
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

// ---------------------------------------------------------------- 用户调频层
// 小型用户三元模型：统计用户实际提交文本的字符 trigram 计数，在
// Engine::logp 的融合槽位与静态主模型做概率域插值。静态模型（V5）文件
// 永不改写；该表全内存、容量有上限，跨会话由 Lua 协调 native 快照
// 读写接口持久化（见 tiger_engine_user_model_export/import）。
struct UserNgram {
  // 与 logp_cache 同构的键位打包（码点 < 2^21）。
  static uint64_t tri_key(uint32_t a, uint32_t b, uint32_t c) {
    return ((uint64_t)a << 42) | ((uint64_t)b << 21) | c;
  }
  static uint64_t bi_key(uint32_t a, uint32_t b) {
    return ((uint64_t)a << 21) | b;
  }

  std::unordered_map<uint64_t, uint32_t> tri;
  std::unordered_map<uint64_t, uint32_t> bi;
  std::unordered_map<uint32_t, uint32_t> uni;
  uint64_t total = 0;  // unigram 总计数（含哨兵）
  size_t max_tri_entries = 100000;

  bool empty() const { return total == 0; }

  // Jelinek-Mercer 级联回退：上下文未见时权重自动落到低阶，kFloor 保证非零。
  double logp(uint32_t a, uint32_t b, uint32_t c) const {
    static const double kTriW = 0.6, kBiW = 0.3, kUniW = 0.09;
    static const double kFloor = 1e-12;
    double p = kFloor;
    auto ctx = bi.find(bi_key(a, b));
    if (ctx != bi.end() && ctx->second > 0) {
      auto it = tri.find(tri_key(a, b, c));
      if (it != tri.end())
        p += kTriW * (double)it->second / (double)ctx->second;
    }
    auto ub = uni.find(b);
    if (ub != uni.end() && ub->second > 0) {
      auto it = bi.find(bi_key(b, c));
      if (it != bi.end())
        p += kBiW * (double)it->second / (double)ub->second;
    }
    if (total > 0) {
      auto uc = uni.find(c);
      if (uc != uni.end())
        p += kUniW * (double)uc->second / (double)total;
    }
    return std::log(p);
  }

  void note(uint32_t a, uint32_t b, uint32_t c) {
    ++tri[tri_key(a, b, c)];
    ++bi[bi_key(a, b)];
    ++uni[b];
    ++uni[c];
    ++total;
  }

  // 喂入一段提交文本：BOS/EOS 框架内逐码点计数。
  bool observe(const std::string& text) {
    if (text.empty()) return false;
    uint32_t p2 = kBOS, p1 = kBOS;
    size_t i = 0;
    while (i < text.size()) {
      uint32_t cp; size_t n;
      utf8_next(text.data(), text.size(), i, &cp, &n);
      if (n == 0) break;  // 非法 UTF-8 截断，保守放弃尾部
      note(p2, p1, cp);
      p2 = p1;
      p1 = cp;
      i += n;
    }
    note(p2, p1, kEOS);
    decay_if_large();
    return true;
  }

  // 超过容量后全表衰减 ×0.9 并剔除零计数，保持近期输入的相对优势。
  void decay_if_large() {
    if (tri.size() <= max_tri_entries) return;
    decay_map(tri);
    decay_map(bi);
    for (auto it = uni.begin(); it != uni.end();) {
      it->second = (uint32_t)(it->second * 0.9);
      it = it->second == 0 ? uni.erase(it) : std::next(it);
    }
    total = (uint64_t)(total * 0.9);
  }

  template <typename K>
  static void decay_map(std::unordered_map<K, uint32_t>& m) {
    for (auto it = m.begin(); it != m.end();) {
      it->second = (uint32_t)(it->second * 0.9);
      it = it->second == 0 ? m.erase(it) : std::next(it);
    }
  }

  // 快照格式（小端二进制）："MHUG01\n" + 8 字节 total +
  // {u32 条数, (u64 key, u32 count)*} × (tri, bi) + {u32 条数, (u32 key, u32 count)*}。
  std::string export_blob() const {
    std::string out;
    out.reserve(16 + tri.size() * 12 + bi.size() * 12 + uni.size() * 8);
    auto append_u32 = [&out](uint32_t v) {
      out.append((const char*)&v, 4);
    };
    auto append_u64 = [&out](uint64_t v) {
      out.append((const char*)&v, 8);
    };
    out += "MHUG01\n";
    append_u64(total);
    append_u32((uint32_t)tri.size());
    for (const auto& kv : tri) { append_u64(kv.first); append_u32(kv.second); }
    append_u32((uint32_t)bi.size());
    for (const auto& kv : bi) { append_u64(kv.first); append_u32(kv.second); }
    append_u32((uint32_t)uni.size());
    for (const auto& kv : uni) { append_u32(kv.first); append_u32(kv.second); }
    return out;
  }

  // 解析快照；任何尺寸越界或魔数不符都整体拒绝，不影响现有计数。
  static bool import_blob(const std::string& blob, UserNgram* into) {
    struct Reader {
      const char* p; const char* end; bool ok = true;
      void take(char* dst, size_t n) {
        if (!ok || (size_t)(end - p) < n) { ok = false; return; }
        memcpy(dst, p, n);
        p += n;
      }
      uint32_t u32() { uint32_t v = 0; take((char*)&v, 4); return v; }
      uint64_t u64() { uint64_t v = 0; take((char*)&v, 8); return v; }
    } r{blob.data(), blob.data() + blob.size()};
    char magic[7] = {};
    r.take(magic, 7);
    if (!r.ok || memcmp(magic, "MHUG01", 6) != 0) return false;
    uint64_t total = r.u64();
    if (!r.ok) return false;
    UserNgram fresh;
    fresh.total = total > (1ull << 40) ? (1ull << 40) : total;
    uint32_t n_tri = r.u32();
    if (!r.ok || n_tri > 4000000u) return false;
    for (uint32_t i = 0; i < n_tri && r.ok; i++) {
      uint64_t k = r.u64();
      uint32_t c = r.u32();
      if (c > 0) fresh.tri[k] = c > 1000000u ? 1000000u : c;
    }
    uint32_t n_bi = r.u32();
    if (!r.ok || n_bi > 4000000u) return false;
    for (uint32_t i = 0; i < n_bi && r.ok; i++) {
      uint64_t k = r.u64();
      uint32_t c = r.u32();
      if (c > 0) fresh.bi[k] = c > 1000000u ? 1000000u : c;
    }
    uint32_t n_uni = r.u32();
    if (!r.ok || n_uni > 4000000u) return false;
    for (uint32_t i = 0; i < n_uni && r.ok; i++) {
      uint32_t k = r.u32();
      uint32_t c = r.u32();
      if (c > 0) fresh.uni[k] = c > 1000000u ? 1000000u : c;
    }
    if (!r.ok) return false;
    *into = std::move(fresh);
    return true;
  }
};

struct Engine {
  // 单文件容器（MHCTN01）的宿主映射；model/wm 以非持有视图指向其中区段。
  // 声明在最前，保证视图（非持有）先于宿主析构。
  MappedFile container;
  KnModel model;
  WordModel wm;              // 词级+词义模型（MHKNM01）
  bool word_mode = false;
  bool packed_word_scorer = false;  // 容器词层：仅供候选评分，不改变解码路径
  WordModel scorer;          // 显式加载的独立词级评分模型（覆盖用）
  bool explicit_scorer = false;
  KnModel blend;             // 可选第二字符模型（概率域插值）
  bool blend_mode = false;
  double blend_alpha = 0.6;  // 主模型权重
  UserNgram user;            // 用户调频层（提交文本的 trigram 计数）
  double user_weight = 0.85; // 静态模型（含 blend 后）权重；>=1 等价关闭用户层
  // 读音先验权重：把码表第 5 列推导的 log P(读音|字) 加进路径分，
  // 补上字符级模型「只认字频、不认读音」的盲区（万 mò 类罕用读音）。
  double reading_prior_weight = 1.0;
  // 跨候选调频：上屏历史尾部的 CJK 字作为解码左上文（字符级 trigram
  // 条件窗口恰为 2 字）。词级上下文（pw2/pw1）不参与播种，维持 <s>。
  bool has_decode_context = false;
  uint32_t ctx_prev2 = kBOS, ctx_prev1 = kBOS;

  // 整段最近上屏文本 -> 尾部至多 window 个 CJK 码点作左上文；无汉字则
  // 清除（与 librime 整段传递、模型侧定窗口的口径一致）。上下文变化
  // 时整帧 beam 缓存作废（旧状态内嵌的是旧条件下的分数）。
  bool set_decode_context(const std::string& text, int window) {
    if (window < 1 || window > 2) window = 2;  // 字符级三元结构窗口 = 2
    uint32_t last1 = 0, last2 = 0;
    int found = 0;
    size_t i = 0;
    while (i < text.size()) {
      uint32_t cp; size_t n;
      utf8_next(text.data(), text.size(), i, &cp, &n);
      if (n == 0) break;  // 非法 UTF-8 截断，保守放弃尾部
      const bool cjk = (cp >= 0x3400 && cp <= 0x9FFF) ||
                       (cp >= 0xF900 && cp <= 0xFAFF) || cp >= 0x20000;
      if (cjk) {
        last2 = last1;  // 不足窗口时保持 0，由下方 kBOS 兜底
        last1 = cp;
        if (found < window) found++;
      }
      i += n;
    }
    const bool new_has = found > 0;
    const uint32_t new_p2 = found >= 2 ? last2 : kBOS;
    const uint32_t new_p1 = found >= 1 ? last1 : kBOS;
    if (new_has == has_decode_context && new_p2 == ctx_prev2 &&
        new_p1 == ctx_prev1) {
      return false;  // 逐键重复设置同一历史时保持零开销
    }
    has_decode_context = new_has;
    ctx_prev2 = new_p2;
    ctx_prev1 = new_p1;
    cache_valid = false;
    has_terminal_phrase_states = false;
    cached_raw.clear();
    return true;
  }
  Lexicon lex;
  int beam = 200;
  bool all_ranks_always = true;      // 魔虎模式：>4 键也允许全部档位竞争

  // 码表词条 -> 词 id（词表含码表全部词条，正常都有；未命中用 OOV 地板）
  uint32_t word_id(const std::string& text) const {
    return word_id_in(&wm, text);
  }

  static uint32_t word_id_in(const WordModel* m, const std::string& text) {
    auto it = m->ids.find(text);
    return it == m->ids.end() ? 0xFFFFFFFFu : it->second;
  }

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

  double word_logp(uint32_t a, uint32_t b, uint32_t c) {
    if (c == 0xFFFFFFFFu) {
      // 词条不在词表：字级兜底（拆字按字符模型路径不可用时给重罚）
      return -20.0;
    }
    uint64_t key = ((uint64_t)a << 42) | ((uint64_t)b << 21) | (c & 0x1FFFFF);
    auto it = logp_cache.find(key);
    if (it != logp_cache.end()) return it->second;
    double v = wm.logp(a, b, c);
    if (!std::isfinite(v)) v = -20.0;
    if (logp_cache.size() > 65536) logp_cache.clear();
    logp_cache[key] = v;
    return v;
  }

  // ---- 词级候选评分（跨候选调频：4 键词码的上下文重排信号） ----

  // 词级评分模型：显式加载的独立词模型优先；其次主模型自带的词层
  // （MHKNM01 主模型或 MHCTN01 容器词层）。都没有则评分不可用。
  // 非 const：WordModel::logp 会写页内查找缓存。
  WordModel* word_scorer() {
    if (explicit_scorer) return &scorer;
    if (word_mode || packed_word_scorer) return &wm;
    return nullptr;
  }

  std::unordered_map<uint64_t, double> scorer_logp_cache;  // 独立词模型 id 空间
  std::string word_ctx_text;      // 上文切词缓存（单条目：同一历史逐键重复零开销）
  uint32_t word_ctx_pw2 = 0, word_ctx_pw1 = 0;
  int word_ctx_window = 2;
  bool word_ctx_valid = false;

  // 上文尾部逆向最大匹配切出末 1–2 词（词表含全部单字）。只看末 16 个
  // CJK 字：边界误差至多影响窗口首词，不影响末两词。无词可切时 pw2/pw1
  // 保持 0（<s>）。
  bool resolve_word_context(const std::string& text, int window_words) {
    if (window_words < 1 || window_words > 2) window_words = 2;
    WordModel* m = word_scorer();
    if (!m) {
      set_error("word scorer not loaded");
      return false;
    }
    if (word_ctx_valid && word_ctx_window == window_words && word_ctx_text == text)
      return true;
    std::vector<std::string> tail;  // 末 16 个 CJK 字
    size_t i = 0;
    while (i < text.size()) {
      uint32_t cp; size_t n;
      utf8_next(text.data(), text.size(), i, &cp, &n);
      if (n == 0) break;  // 非法 UTF-8 截断，保守放弃剩余
      const bool cjk = (cp >= 0x3400 && cp <= 0x9FFF) ||
                       (cp >= 0xF900 && cp <= 0xFAFF) || cp >= 0x20000;
      if (cjk) {
        tail.push_back(text.substr(i, n));
        if (tail.size() > 16) tail.erase(tail.begin());
      }
      i += n;
    }
    std::vector<uint32_t> rev;  // 从尾往头方向的词 id
    size_t end = tail.size();
    while (end > 0 && rev.size() < (size_t)window_words) {
      size_t taken = 0;
      uint32_t id = 0xFFFFFFFFu;
      const size_t max_len = std::min<size_t>(8, end);
      std::string w;
      for (size_t len = max_len; len >= 1; --len) {
        w.clear();
        for (size_t k = end - len; k < end; ++k) w += tail[k];
        auto it = m->ids.find(w);
        if (it != m->ids.end()) {
          id = it->second;
          taken = len;
          break;
        }
      }
      if (taken == 0) {
        --end;  // 该字不在词表（极罕见，词表含全部单字）→ 跳过
        continue;
      }
      rev.push_back(id);
      end -= taken;
    }
    word_ctx_pw1 = rev.size() >= 1 ? rev[0] : 0;  // 0 = <s>
    word_ctx_pw2 = rev.size() >= 2 ? rev[1] : 0;
    word_ctx_text = text;
    word_ctx_window = window_words;
    word_ctx_valid = true;
    return true;
  }

  double scorer_word_logp(WordModel* m, uint32_t a, uint32_t b, uint32_t c) {
    if (c == 0xFFFFFFFFu) return -20.0;  // OOV：无信号，Lua 侧不提升
    if (m->vocab.size() >= kShift) {
      // 词表超出 21 位键打包范围：直查不缓存（常规模型远小于此）。
      double v = m->logp(a, b, c);
      return std::isfinite(v) ? v : -20.0;
    }
    // 与主词模型共享 logp_cache（容器/词级主模型场景下 id 空间一致）；
    // 独立词模型用单独缓存，避免 id 撞键。
    std::unordered_map<uint64_t, double>& cache =
        (m == &wm) ? logp_cache : scorer_logp_cache;
    uint64_t key = ((uint64_t)a << 42) | ((uint64_t)b << 21) | (c & 0x1FFFFF);
    auto it = cache.find(key);
    if (it != cache.end()) return it->second;
    double v = m->logp(a, b, c);
    if (!std::isfinite(v)) v = -20.0;
    if (cache.size() > 65536) cache.clear();
    cache[key] = v;
    return v;
  }

  // 批量词级评分：out[i] = logP(候选 i | 上文尾部词)。返回写入个数。
  int context_word_scores(const std::string& context_text,
                          const std::vector<std::string>& candidates,
                          int window_words, double* out) {
    WordModel* m = word_scorer();
    if (!m) {
      set_error("word scorer not loaded");
      return -1;
    }
    if (!resolve_word_context(context_text, window_words)) return -1;
    for (size_t i = 0; i < candidates.size(); ++i)
      out[i] = scorer_word_logp(m, word_ctx_pw2, word_ctx_pw1,
                                word_id_in(m, candidates[i]));
    return (int)candidates.size();
  }

  // 批量字符级续写评分（octagram 同型机制）：out[i] = Σ_codepoints
  // logP(cp | 上文末 2 个 CJK 字与候选已出字)。空上文时从 BOS 起步，
  // 返回值即基线分（供上层做 lift：score(上文) − score(空上文) 剥离
  // 词频成分，只留上下文增益）。字符模型路径专用；word_mode 主模型
  // 无字符层时返回 -1。不动解码状态。
  int context_char_scores(const std::string& context_text,
                          const std::vector<std::string>& candidates,
                          double* out) {
    if (word_mode) {
      set_error("char scoring requires a char-level model");
      return -1;
    }
    uint32_t p2 = kBOS, p1 = kBOS;
    {
      uint32_t last1 = 0, last2 = 0;
      int found = 0;
      size_t i = 0;
      while (i < context_text.size()) {
        uint32_t cp; size_t n;
        utf8_next(context_text.data(), context_text.size(), i, &cp, &n);
        if (n == 0) break;
        const bool cjk = (cp >= 0x3400 && cp <= 0x9FFF) ||
                         (cp >= 0xF900 && cp <= 0xFAFF) || cp >= 0x20000;
        if (cjk) {
          last2 = last1;
          last1 = cp;
          if (found < 2) found++;
        }
        i += n;
      }
      if (found >= 1) p1 = last1;
      if (found >= 2) p2 = last2;
    }
    for (size_t i = 0; i < candidates.size(); ++i) {
      const std::string& cand = candidates[i];
      double s = 0;
      uint32_t a = p2, b = p1;
      size_t j = 0;
      while (j < cand.size()) {
        uint32_t cp; size_t n;
        utf8_next(cand.data(), cand.size(), j, &cp, &n);
        if (n == 0) break;
        s += logp(a, b, cp);
        a = b;
        b = cp;
        j += n;
      }
      out[i] = s;
    }
    return (int)candidates.size();
  }

  double logp(uint32_t a, uint32_t b, uint32_t c) {
    uint64_t key = ((uint64_t)a << 42) | ((uint64_t)b << 21) | c;
    auto it = logp_cache.find(key);
    if (it != logp_cache.end()) return it->second;
    double v = model.logp(a, b, c);
    if (!std::isfinite(v)) throw std::runtime_error("invalid n-gram probability");
    if (blend_mode) {
      double w = blend.logp(a, b, c);
      if (!std::isfinite(w)) throw std::runtime_error("invalid n-gram probability");
      const double p1 = std::exp(std::max(-700.0, v));
      const double p2 = std::exp(std::max(-700.0, w));
      const double mixed = blend_alpha * p1 + (1.0 - blend_alpha) * p2;
      if (!(mixed > 0.0) || !std::isfinite(mixed))
        throw std::runtime_error("invalid n-gram probability");
      v = std::log(mixed);
    }
    if (user_weight < 1.0 && !user.empty()) {
      double pu = user.logp(a, b, c);
      if (!std::isfinite(v) || !std::isfinite(pu))
        throw std::runtime_error("invalid n-gram probability");
      const double p1 = std::exp(std::max(-700.0, v));
      const double p2 = std::exp(std::max(-700.0, pu));
      const double mixed = user_weight * p1 + (1.0 - user_weight) * p2;
      if (!(mixed > 0.0) || !std::isfinite(mixed))
        throw std::runtime_error("invalid n-gram probability");
      v = std::log(mixed);
    }
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
            // 静态多字词保持整段命中语义；个人词允许成为长句内部边。
            if (cand.chars.size() != 1 && !cand.personal &&
                !(pos == 0 && consumed_end == length)) continue;
            if (cand.chars.size() != 1 && !cand.personal) has_terminal_phrase_states = true;
            double score = item->score;
            uint32_t prev2 = item->prev2, prev1 = item->prev1;
            uint32_t pw2 = item->pw2, pw1 = item->pw1;
            if (word_mode) {
              // 词级打分：整词转移 + 每字奖励（鼓励长词、对冲 logp 项数差）
              uint32_t wid = word_id(cand.text);
              double wlp = word_logp(pw2, pw1, wid);
              if (cand.personal && wid == 0xFFFFFFFFu) wlp = -8.0;
              score += wlp;
              score += word_char_reward() * cand.chars.size();
              pw2 = pw1;
              pw1 = wid;
            } else {
              for (uint32_t cp : cand.chars) {
                score += logp(prev2, prev1, cp);
                score += kCharReward;
                prev2 = prev1;
                prev1 = cp;
              }
            }
            if (selected_rank == 0 && cand.rank > 1)
              score -= kRankPenalty * log(1.0 + (double)(cand.rank - 1));
            // 读音先验：字符级 LM 无读音概念，罕用读音的高频字（万 mò）
            // 会凭全局字频挤到候选前列；先验按贝叶斯项 P(码|字) 惩罚。
            if (reading_prior_weight != 0.0 && cand.reading_prior != 0.0)
              score += reading_prior_weight * cand.reading_prior;
            score += cand.personal_boost;
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
            s2->pw2 = pw2;
            s2->pw1 = pw1;
            s2->max_rank = std::max(item->max_rank, cand.rank);
            s2->edges = item->edges + 1;
            s2->previous = item;
            s2->text_length = s2->text.size();
            s2->raw_length = consumed_end;
            s2->personal = item->personal || cand.personal;
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
    out->personal = s->personal;
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
      double ending = (word_mode ? word_logp(s->pw2, s->pw1, 1 /*</s>*/)
                                 : logp(s->prev2, s->prev1, kEOS)) -
                      (word_mode ? 0.0 : isolation_penalty(s->text));
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
        double ending = (word_mode ? word_logp(s->pw2, s->pw1, 1 /*</s>*/)
                                 : logp(s->prev2, s->prev1, kEOS)) -
                      (word_mode ? 0.0 : isolation_penalty(s->text));
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

  void invalidate_overlay_cache() {
    pool.clear();
    states.clear();
    cached_raw.clear();
    cached_result = DecodeResult();
    cache_valid = false;
    has_terminal_phrase_states = false;
    logp_cache.clear();
  }

  // A lazy page failure can occur halfway through beam expansion.  Never keep
  // that partial frontier or a score cache alive for the next composition.
  void abort_decode() {
    pool.clear();
    states.clear();
    cached_result = DecodeResult();
    cached_raw.clear();
    cache_valid = false;
    cached_with_early = false;
    has_terminal_phrase_states = false;
    logp_cache.clear();
    scorer_logp_cache.clear();
    word_ctx_valid = false;
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
    if (has_decode_context) {
      // 跨候选左上文：beam 起步条件改为 P(首字|上文尾部两字)，
      // 与后续字的 trigram 打分天然同构，候选间比较保持一致。
      root->prev2 = ctx_prev2;
      root->prev1 = ctx_prev1;
    }
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
    s += buf; s += it.pathmap; s += '\t'; s += (it.personal ? '1' : '0'); s += '\n';
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

#ifdef TIGERENGINE_MAPPING_TEST
int mapping_ownership_probe_impl() {
  g_mapping_unmap_count = 0;
  g_mapping_close_count = 0;

  char path[L_tmpnam] = {};
  if (!std::tmpnam(path)) return 10;
  {
    FILE* stream = std::fopen(path, "wb");
    if (!stream) return 11;
    const uint8_t bytes[] = {
        0x4d, 0x48, 0x43, 0x54, 0x4e, 0x30, 0x31, 0x00,
        0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
    };
    const bool written = std::fwrite(bytes, 1, sizeof(bytes), stream) == sizeof(bytes);
    std::fclose(stream);
    if (!written) {
      std::remove(path);
      return 12;
    }
  }

  int result = 0;
  {
    MappedFile owner;
    if (!owner.open(path)) {
      result = 13;
    } else {
      const uint8_t expected = owner.data[3];
      {
        MappedFile first_view;
        first_view.set_borrowed_view(owner.data + 2, owner.size - 2);
        if (first_view.owned || first_view.data[1] != expected) {
          result = 14;
        }
        first_view.release();
        if (!first_view.owned || first_view.data || first_view.size) result = 15;
      }
      if (!result && owner.data[3] != expected) result = 16;

      if (!result) {
        MappedFile second_view;
        second_view.set_borrowed_view(owner.data + 4, owner.size - 4);
        MappedFile moved_view(std::move(second_view));
        if (second_view.data || second_view.size || !second_view.owned ||
            moved_view.owned || moved_view.data[0] != owner.data[4]) {
          result = 17;
        }
        // Reusing a borrowed object must release only its view and restore the
        // default owner state before it is used again.
        moved_view.set_borrowed_view(owner.data + 1, owner.size - 1);
        moved_view.release();
        if (!moved_view.owned || moved_view.data || moved_view.size) result = 18;
      }

      MappedFile moved_owner(std::move(owner));
      if (owner.data || owner.size || !owner.owned) {
        result = 19;
      } else if (!moved_owner.data || moved_owner.data[3] != expected) {
        result = 20;
      }
      moved_owner.release();
      if (moved_owner.data || moved_owner.size || !moved_owner.owned) result = 21;
    }
    owner.release();
  }
  std::remove(path);
  if (result) return result;
  if (g_mapping_unmap_count != 1) return 22;
#ifdef _WIN32
  if (g_mapping_close_count != 1) return 23;
#endif
  return 0;
}
#endif

}  // namespace

#ifdef TIGERENGINE_MAPPING_TEST
extern "C" int tigerengine_mapping_ownership_probe() {
  return mapping_ownership_probe_impl();
}
#endif

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
    // Map the primary file once, then dispatch by its magic.  A model loader
    // takes ownership by move, while container sub-models remain borrowed views
    // of this host mapping.  In particular, do not probe the same path through
    // several loaders: each failed probe would otherwise fault/map the whole V5
    // file before the successful loader gets a chance to use it.
    if (!e->container.open(model_path)) {
      copy_last_error(err, errcap);
      return -1;
    }
    if (e->container.size < 8) {
      set_error("model is too small to identify: %s", model_path);
      copy_last_error(err, errcap);
      return -1;
    }
    const uint8_t* primary = e->container.data;
    if (memcmp(primary, "MHCTN01", 7) == 0) {
      // 单文件容器（MHCTN01）：一次 mmap 同携字符与词级模型。词层仅供
      // 候选评分（packed_word_scorer），解码仍走字符级，与非容器行为一致；
      // 词层缺失/损坏时降级为纯字符引擎（评分接口报错，解码不受影响）。
      if (e->container.size < 64) {
        set_error("invalid MHCTN01 container header");
        copy_last_error(err, errcap);
        return -1;
      }
      const uint8_t* cd = e->container.data;
      size_t q = 8;
      auto u32c = [&]() { uint32_t v = rd_u32(cd + q); q += 4; return v; };
      auto u64c = [&]() { uint64_t v = rd_u64(cd + q); q += 8; return v; };
      const uint32_t version = u32c();
      const uint32_t header_size = u32c();
      const uint64_t file_size = u64c();
      const uint32_t flags = u32c();
      (void)u32c();  // reserved
      const uint64_t char_off = u64c(), char_len = u64c();
      const uint64_t word_off = u64c(), word_len = u64c();
      const bool has_char = (flags & 1u) != 0, has_word = (flags & 2u) != 0;
      auto section_ok = [&](uint64_t off, uint64_t len) {
        return off >= (uint64_t)header_size && off <= e->container.size &&
               len <= e->container.size - off;
      };
      if (version != 1 || header_size != 64 || file_size != e->container.size ||
          q > header_size) {
        set_error("invalid MHCTN01 container header");
        copy_last_error(err, errcap);
        return -1;
      }
      if (!has_char || !section_ok(char_off, char_len)) {
        set_error("container requires a valid char section");
        copy_last_error(err, errcap);
        return -1;
      }
      if (!e->model.load_view(e->container.data + char_off, char_len, model_path)) {
        copy_last_error(err, errcap);
        return -1;
      }
      if (has_word && section_ok(word_off, word_len) &&
          e->wm.load_view(e->container.data + word_off, word_len, model_path)) {
        e->packed_word_scorer = true;
      }
    } else if (memcmp(primary, "TCSKNM01", 8) == 0 ||
               memcmp(primary, "TCSKNM02", 8) == 0) {
      if (!e->model.load_mapped(std::move(e->container), model_path)) {
        copy_last_error(err, errcap);
        return -1;
      }
    } else if (memcmp(primary, "MHKNM01", 7) == 0) {
      if (!e->wm.load_mapped(std::move(e->container), model_path)) {
        copy_last_error(err, errcap);
        return -1;
      }
      e->word_mode = true;
    } else {
      set_error("unknown model format: %s", model_path);
      copy_last_error(err, errcap);
      return -1;
    }
    if (!e->lex.load(lexicon_path)) {
      copy_last_error(err, errcap);
      return -1;
    }
    // 可选第二字符模型（研究用：MH_BLEND 指向 TCSKNM 路径做概率域融合）
    if (const char* bp = getenv("MH_BLEND")) {
      if (!e->word_mode && e->blend.load(bp)) {
        e->blend_mode = true;
        if (const char* al = getenv("MH_BLEND_ALPHA"))
          e->blend_alpha = atof(al);
      }
    }
    if (g_engines.size() >= static_cast<size_t>(std::numeric_limits<int>::max())) {
      set_error("too many engine handles");
      copy_last_error(err, errcap);
      return -1;
    }
    g_engines.push_back(std::move(e));
    // 成功路径清掉两级模型探测留下的诊断信息，避免它被误当成后续调用的错误。
    g_last_error.clear();
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

int tiger_engine_set_personal_lexicon(int handle, const char* rows) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    const int applied = g_engines[handle]->lex.set_personal(rows);
    if (applied < 0) return -1;
    // 负载未变化（no-op）时保留解码缓存；只有实际应用了变更才失效。
    if (applied > 0) g_engines[handle]->invalidate_overlay_cache();
    return 0;
  } catch (const std::exception&) {
    set_error("personal lexicon update failed");
    return -1;
  } catch (...) {
    set_error("personal lexicon update failed");
    return -1;
  }
}

int tiger_engine_adjust_personal(int handle, const char* code, const char* text,
                                 int commits_delta) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!code || !text) {
      set_error("personal edge code and text are required");
      return -1;
    }
    const int rc = g_engines[handle]->lex.adjust_personal(code, text, commits_delta);
    if (rc < 0) {
      set_error("invalid personal edge delta");
      return -1;
    }
    g_engines[handle]->invalidate_overlay_cache();
    return rc;
  } catch (const std::exception&) {
    set_error("personal edge update failed");
    return -1;
  } catch (...) {
    set_error("personal edge update failed");
    return -1;
  }
}

int tiger_engine_personal_begin(int handle) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    return g_engines[handle]->lex.personal_begin();
  } catch (...) {
    set_error("personal transaction failed");
    return -1;
  }
}

int tiger_engine_personal_append(int handle, const char* rows) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    return g_engines[handle]->lex.personal_append(rows);
  } catch (...) {
    set_error("personal transaction append failed");
    return -1;
  }
}

int tiger_engine_personal_commit(int handle) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    const int rc = g_engines[handle]->lex.personal_commit();
    if (rc < 0) return -1;
    // 0 = 无变化，保留解码缓存；1 = 已应用，失效缓存。
    if (rc == 1) g_engines[handle]->invalidate_overlay_cache();
    return 0;
  } catch (...) {
    set_error("personal transaction commit failed");
    return -1;
  }
}

int tiger_engine_personal_abort(int handle) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    return g_engines[handle]->lex.personal_abort();
  } catch (...) {
    set_error("personal transaction abort failed");
    return -1;
  }
}

int tiger_engine_update_user_model(int handle, const char* text) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!text) {
      set_error("user model update requires text");
      return -1;
    }
    Engine* e = g_engines[handle].get();
    if (!e->user.observe(text)) return 0;  // 空文本，无变化
    e->invalidate_overlay_cache();
    return 1;
  } catch (const std::exception&) {
    set_error("user model update failed");
    return -1;
  } catch (...) {
    set_error("user model update failed");
    return -1;
  }
}

int tiger_engine_set_user_model_weight(int handle, double static_weight) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    Engine* e = g_engines[handle].get();
    if (!(static_weight > 0.0 && static_weight <= 1.0)) {
      set_error("user model weight must be in (0, 1]");
      return -1;
    }
    if (e->user_weight == static_weight) return 0;
    e->user_weight = static_weight;
    e->invalidate_overlay_cache();
    return 1;
  } catch (...) {
    set_error("user model weight update failed");
    return -1;
  }
}

int tiger_engine_set_reading_prior_weight(int handle, double weight) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!(weight >= 0.0 && weight <= 4.0)) {
      set_error("reading prior weight must be in [0, 4]");
      return -1;
    }
    Engine* e = g_engines[handle].get();
    if (e->reading_prior_weight == weight) return 0;
    e->reading_prior_weight = weight;
    e->invalidate_overlay_cache();
    return 1;
  } catch (...) {
    set_error("reading prior weight update failed");
    return -1;
  }
}

/* 返回 malloc 分配的快照 blob 与其字节数（*size_out），调用方负责 free()；
 * 空模型返回 ""，错误返回 NULL。blob 是二进制，可能含 NUL，禁止当 C 字符串用。 */
char* tiger_engine_user_model_export(int handle, size_t* size_out) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (size_out) *size_out = 0;
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return nullptr;
    }
    const std::string blob = g_engines[handle]->user.export_blob();
    char* out = (char*)malloc(blob.size() + 1);
    if (!out) {
      set_error("user model export allocation failed");
      return nullptr;
    }
    blob.copy(out, blob.size());
    out[blob.size()] = 0;
    if (size_out) *size_out = blob.size();
    return out;
  } catch (...) {
    set_error("user model export failed");
    return nullptr;
  }
}

int tiger_engine_set_decode_context(int handle, const char* text, int window_chars) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!text) {
      set_error("decode context requires text");
      return -1;
    }
    return g_engines[handle]->set_decode_context(text, window_chars) ? 1 : 0;
  } catch (...) {
    set_error("decode context update failed");
    return -1;
  }
}

/* 显式加载独立词级评分模型（MHKNM01）。容器（MHCTN01）或词级主模型已
 * 自带词层时可省略；显式加载优先。0 成功，-1 失败（引擎本体不受影响）。 */
int tiger_engine_load_word_scorer(int handle, const char* model_path) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!model_path || !model_path[0]) {
      set_error("word scorer requires a model path");
      return -1;
    }
    WordModel fresh;
    if (!fresh.load(model_path)) return -1;  // set_error 已携带路径信息
    Engine* e = g_engines[handle].get();
    e->scorer = std::move(fresh);
    e->explicit_scorer = true;
    e->word_ctx_valid = false;
    e->scorer_logp_cache.clear();
    return 0;
  } catch (...) {
    set_error("word scorer load failed");
    return -1;
  }
}

/* 批量词级上下文评分：candidates 为 '\n' 分隔的候选文本，out_scores 按
 * 顺序写 logP(候选 | 上文尾部词)，OOV 写 -20（无信号）。window_words
 * <= 0 取默认 2，>2 截到 2。返回写入个数，<0 出错。 */
int tiger_engine_context_word_scores(int handle, const char* context_text,
                                     const char* candidates, int candidate_count,
                                     int window_words, double* out_scores) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!context_text || !candidates || !out_scores) {
      set_error("word scores require context, candidates and output buffer");
      return -1;
    }
    if (candidate_count <= 0 || candidate_count > 4096) {
      set_error("candidate count out of range");
      return -1;
    }
    std::vector<std::string> cands;
    cands.reserve((size_t)candidate_count);
    const char* p = candidates;
    for (int i = 0; i < candidate_count; ++i) {
      const char* nl = strchr(p, '\n');
      if (!nl) {
        if (i + 1 < candidate_count) {
          set_error("candidates fewer than count");
          return -1;
        }
        cands.emplace_back(p);
      } else {
        cands.emplace_back(p, (size_t)(nl - p));
        p = nl + 1;
      }
    }
    return g_engines[handle]->context_word_scores(context_text, cands,
                                                  window_words, out_scores);
  } catch (...) {
    set_error("word scores failed");
    return -1;
  }
}

/* 批量字符级续写评分（octagram 同型）：out_scores[i] = Σ logP(候选 i 的
 * 码点 | 上文末 2 个 CJK 字及候选已出字)。空上文返回 BOS 基线分（供
 * lift 计算）。字符级主模型专用；word_mode 主模型返回 -1。 */
int tiger_engine_context_char_scores(int handle, const char* context_text,
                                     const char* candidates, int candidate_count,
                                     double* out_scores) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!context_text || !candidates || !out_scores) {
      set_error("char scores require context, candidates and output buffer");
      return -1;
    }
    if (candidate_count <= 0 || candidate_count > 4096) {
      set_error("candidate count out of range");
      return -1;
    }
    std::vector<std::string> cands;
    cands.reserve((size_t)candidate_count);
    const char* p = candidates;
    for (int i = 0; i < candidate_count; ++i) {
      const char* nl = strchr(p, '\n');
      if (!nl) {
        if (i + 1 < candidate_count) {
          set_error("candidates fewer than count");
          return -1;
        }
        cands.emplace_back(p);
      } else {
        cands.emplace_back(p, (size_t)(nl - p));
        p = nl + 1;
      }
    }
    return g_engines[handle]->context_char_scores(context_text, cands, out_scores);
  } catch (const InvalidPageError& error) {
    set_error("%s", error.what());
    return -1;
  } catch (const std::exception& error) {
    set_error("%s", error.what());
    return -1;
  } catch (...) {
    set_error("char scores failed");
    return -1;
  }
}

int tiger_engine_user_model_import(int handle, const char* blob, size_t blob_size) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) {
      set_error("invalid engine handle");
      return -1;
    }
    if (!blob || blob_size == 0) {
      set_error("user model import requires a snapshot blob");
      return -1;
    }
    Engine* e = g_engines[handle].get();
    UserNgram fresh;
    // blob 是二进制（可含 NUL），必须按显式长度构造。
    if (!UserNgram::import_blob(std::string(blob, blob_size), &fresh)) {
      set_error("user model snapshot is corrupt");
      return -1;
    }
    const bool had = !e->user.empty();
    e->user = std::move(fresh);
    if (had || !e->user.empty()) e->invalidate_overlay_cache();
    return 1;
  } catch (...) {
    set_error("user model import failed");
    return -1;
  }
}

char* tiger_read_snapshot_file(const char* path, size_t* size_out) {
  if (size_out) *size_out = 0;
  if (!path || !path[0] || !size_out) {
    set_error("snapshot read requires a path and size output");
    return nullptr;
  }
  try {
    std::unique_ptr<std::FILE, int (*)(std::FILE*)> file(
        open_snapshot_file(path, false), &std::fclose);
    if (!file) return nullptr;
    if (std::fseek(file.get(), 0, SEEK_END) != 0) {
      set_error("cannot seek snapshot");
      return nullptr;
    }
    const long length = std::ftell(file.get());
    if (length < 0 || std::fseek(file.get(), 0, SEEK_SET) != 0) {
      set_error("cannot measure snapshot");
      return nullptr;
    }
    const size_t size = static_cast<size_t>(length);
    char* blob = static_cast<char*>(std::malloc(size > 0 ? size : 1));
    if (!blob) {
      set_error("cannot allocate snapshot buffer");
      return nullptr;
    }
    if (size > 0 && std::fread(blob, 1, size, file.get()) != size) {
      std::free(blob);
      set_error("cannot read snapshot");
      return nullptr;
    }
    *size_out = size;
    return blob;
  } catch (...) {
    set_error("snapshot read failed");
    return nullptr;
  }
}

int tiger_atomic_write_snapshot_file(const char* path, const char* blob, size_t size) {
  if (!path || !path[0] || (!blob && size > 0)) {
    set_error("snapshot write requires a path and blob");
    return -1;
  }
  std::string temporary_path;
  try {
    temporary_path = snapshot_temporary_path(path);
    std::unique_ptr<std::FILE, int (*)(std::FILE*)> file(
        open_snapshot_file(temporary_path.c_str(), true), &std::fclose);
    if (!file) return -1;
    bool written = size == 0 || std::fwrite(blob, 1, size, file.get()) == size;
    written = written && std::fflush(file.get()) == 0;
#ifdef _WIN32
    written = written && _commit(_fileno(file.get())) == 0;
#else
    written = written && fsync(fileno(file.get())) == 0;
#endif
    const int close_result = std::fclose(file.release());
    if (!written || close_result != 0) {
      remove_snapshot_file(temporary_path.c_str());
      set_error("cannot write snapshot");
      return -1;
    }
    if (!atomic_replace_snapshot_file(temporary_path.c_str(), path)) {
      remove_snapshot_file(temporary_path.c_str());
      return -1;
    }
    return 0;
  } catch (...) {
    if (!temporary_path.empty()) remove_snapshot_file(temporary_path.c_str());
    set_error("snapshot write failed");
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
  Engine* e = nullptr;
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
    e = g_engines[handle].get();
    const auto t0 = std::chrono::steady_clock::now();
    DecodeResult& r = e->decode(raw, include_early != 0);
    const auto t1 = std::chrono::steady_clock::now();
    if (ms) *ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    if (!serialize(r, out, outcap)) {
      set_error("output buffer too small");
      return -1;
    }
    return (int)r.items.size();
  } catch (const InvalidPageError& error) {
    if (e) e->abort_decode();
    set_error("%s", error.what());
    return -1;
  } catch (const std::exception& error) {
    if (e) e->abort_decode();
    set_error("%s", error.what());
    return -1;
  } catch (...) {
    if (e) e->abort_decode();
    set_error("engine decode failed");
    return -1;
  }
}

int tiger_decode_full(int handle, const char* raw, int include_early,
                      char* out, int outcap) {
  Engine* e = nullptr;
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
    e = g_engines[handle].get();
    DecodeResult r = e->decode_full(raw, include_early != 0);
    if (!serialize(r, out, outcap)) {
      set_error("output buffer too small");
      return -1;
    }
    return (int)r.items.size();
  } catch (const InvalidPageError& error) {
    if (e) e->abort_decode();
    set_error("%s", error.what());
    return -1;
  } catch (const std::exception& error) {
    if (e) e->abort_decode();
    set_error("%s", error.what());
    return -1;
  } catch (...) {
    if (e) e->abort_decode();
    set_error("engine decode failed");
    return -1;
  }
}

int tiger_status(int handle, char* out, int outcap) {
  try {
    std::lock_guard<std::mutex> lock(g_engine_mutex);
    if (handle < 0 || handle >= (int)g_engines.size() || !g_engines[handle]) return -1;
    Engine* e = g_engines[handle].get();
    const WordModel* ws = e->word_scorer();
    const char* ws_state = e->explicit_scorer ? "explicit"
                           : e->word_mode ? "primary"
                           : e->packed_word_scorer ? "packed" : "off";
    const std::string& primary_path = e->word_mode ? e->wm.path : e->model.path;
    const char* primary_format = e->word_mode ? "MHKNM01"
                              : e->model.mobile ? "TCSKNM02" : "TCSKNM01";
    const size_t primary_size = e->word_mode ? e->wm.file.size : e->model.file.size;
    char buf[1024];
    const int written = snprintf(buf, sizeof(buf), "path=%s\tformat=%s\tbytes=%llu\tcodes=%zu\tbeam=%d\tuser_tri=%zu\tuser_weight=%.3f\tword_scorer=%s\tword_vocab=%zu", primary_path.c_str(), primary_format, (unsigned long long)primary_size, e->lex.codes.size(), e->beam, e->user.tri.size(), e->user_weight, ws_state, ws ? ws->vocab.size() : (size_t)0);
    (void)written;
    if (out && outcap > 0) snprintf(out, outcap, "%s", buf);
    return 0;
  } catch (...) {
    set_error("engine status failed");
    return -1;
  }
}

const char* tiger_last_error() { return g_last_error.c_str(); }

}  // extern "C"
