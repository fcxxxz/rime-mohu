// neural_infer.h
//
// Lightweight header-only C++17 transformer inference engine for TinyCharLM
// (40M-param char-level language model) on macOS / Apple Silicon.
//
// 轻量 char 级语言模型打分器：给每个候选计算 sum logP(候选 | 上文)，
// 供跨候选重排（contextual_order / word_order）使用。
//
// Design constraints:
//   - Header-only; every definition is `inline`.
//   - No external dependencies: Accelerate (cblas_sgemm) + C++17 stdlib only.
//   - Weights are loaded via mmap and never copied.
//   - All hot-path buffers are members, grown once and reused (no malloc in
//     steady-state scoring).
//   - Not thread-safe: one NeuralScorer per thread (BLAS itself may use
//     internal worker threads, which is fine).
//
// Weight file layout (little-endian, produced by export_neural_weights.py):
//   [64-byte header][contiguous float32 weight blocks]
//   header: magic[8]="MOHU_NLM", version=1(u32), d_model, n_layers, n_heads,
//           vocab_size, max_len, ffn_dim (u32 each), reserved[28]
//   weights: embed [V,D], pos [L,D],
//            per layer: ln1_g [D], ln1_b [D], qkv_w [3D,D] (PyTorch
//            in_proj_weight: Q,K,V rows stacked), qkv_b [3D], ao_w [D,D],
//            ao_b [D], ln2_g [D], ln2_b [D], ff1_w [F,D], ff1_b [F],
//            ff2_w [D,F], ff2_b [D],
//            then lnf_g [D], lnf_b [D], head_w [V,D].
//
// Architecture: Pre-LayerNorm transformer, multi-head causal attention,
// GELU FFN, untied embedding / output head, no dropout (inference only).
//
// Usage:
//   mohu::nlm::NeuralScorer ns;
//   ns.load("tinycharlm.bin");                 // mmap the weights
//   ns.load_vocab("vocab.json");               // {"字": id, ...}
//   std::vector<float> s = ns.score("今天天气", {"很好", "不错"});
//   // or, loading vocab on the fly:
//   std::vector<float> s2 = ns.score("今天天气", {"很好"}, "vocab.json");

#pragma once

#include <Accelerate/Accelerate.h>  // cblas_sgemm

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

namespace mohu {
namespace nlm {
namespace detail {

// ---------------------------------------------------------------------------
// Small utilities: little-endian reads, UTF-8, minimal vocab.json parsing.
// ---------------------------------------------------------------------------

inline uint32_t read_u32_le(const unsigned char* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

// Decode UTF-8 text into Unicode code points. Invalid bytes / truncated
// sequences are skipped one byte at a time (never crash on bad input).
inline void utf8_to_codepoints(const std::string& s, std::vector<uint32_t>& out) {
    out.clear();
    const size_t n = s.size();
    size_t i = 0;
    while (i < n) {
        const unsigned char c = (unsigned char)s[i];
        size_t len;
        uint32_t cp;
        if (c < 0x80) {
            len = 1;
            cp = c;
        } else if ((c & 0xE0) == 0xC0) {
            len = 2;
            cp = c & 0x1Fu;
        } else if ((c & 0xF0) == 0xE0) {
            len = 3;
            cp = c & 0x0Fu;
        } else if ((c & 0xF8) == 0xF0) {
            len = 4;
            cp = c & 0x07u;
        } else {
            ++i;  // stray continuation byte
            continue;
        }
        if (i + len > n) break;
        bool ok = true;
        for (size_t j = 1; j < len; ++j) {
            const unsigned char cc = (unsigned char)s[i + j];
            if ((cc & 0xC0) != 0x80) {
                ok = false;
                break;
            }
            cp = (cp << 6) | (cc & 0x3Fu);
        }
        if (!ok) {
            ++i;
            continue;
        }
        out.push_back(cp);
        i += len;
    }
}

inline void append_utf8(std::string& out, uint32_t cp) {
    if (cp < 0x80) {
        out += (char)cp;
    } else if (cp < 0x800) {
        out += (char)(0xC0 | (cp >> 6));
        out += (char)(0x80 | (cp & 0x3Fu));
    } else if (cp < 0x10000) {
        out += (char)(0xE0 | (cp >> 12));
        out += (char)(0x80 | ((cp >> 6) & 0x3Fu));
        out += (char)(0x80 | (cp & 0x3Fu));
    } else {
        out += (char)(0xF0 | (cp >> 18));
        out += (char)(0x80 | ((cp >> 12) & 0x3Fu));
        out += (char)(0x80 | ((cp >> 6) & 0x3Fu));
        out += (char)(0x80 | (cp & 0x3Fu));
    }
}

inline void json_skip_ws(const char*& p, const char* end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) ++p;
}

// Parse exactly 4 hex digits ("\uXXXX" payload) into `out`.
inline bool json_hex4(const char*& p, const char* end, uint32_t& out) {
    if (end - p < 4) return false;
    uint32_t v = 0;
    for (int i = 0; i < 4; ++i) {
        const char h = *p++;
        v <<= 4;
        if (h >= '0' && h <= '9') {
            v |= (uint32_t)(h - '0');
        } else if (h >= 'a' && h <= 'f') {
            v |= (uint32_t)(h - 'a' + 10);
        } else if (h >= 'A' && h <= 'F') {
            v |= (uint32_t)(h - 'A' + 10);
        } else {
            return false;
        }
    }
    out = v;
    return true;
}

// Parse a JSON string literal (leading quote consumed, trailing quote
// consumed). Handles \uXXXX escapes including surrogate pairs.
inline bool json_parse_string(const char*& p, const char* end, std::string& out) {
    if (p >= end || *p != '"') return false;
    ++p;
    out.clear();
    while (p < end) {
        const char c = *p;
        if (c == '"') {
            ++p;
            return true;
        }
        if (c == '\\') {
            ++p;
            if (p >= end) return false;
            const char e = *p++;
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    uint32_t cp;
                    if (!json_hex4(p, end, cp)) return false;
                    if (cp >= 0xD800 && cp <= 0xDBFF) {
                        // high surrogate: must be followed by \uDC00-\uDFFF
                        if (end - p < 6 || p[0] != '\\' || p[1] != 'u') return false;
                        p += 2;
                        uint32_t lo;
                        if (!json_hex4(p, end, lo)) return false;
                        if (lo < 0xDC00 || lo > 0xDFFF) return false;
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                        return false;  // unpaired low surrogate
                    }
                    append_utf8(out, cp);
                    break;
                }
                default:
                    return false;
            }
        } else {
            if ((unsigned char)c < 0x20) return false;  // raw control char
            out += c;
            ++p;
        }
    }
    return false;
}

inline bool json_parse_int(const char*& p, const char* end, long long& out) {
    json_skip_ws(p, end);
    bool neg = false;
    if (p < end && *p == '-') {
        neg = true;
        ++p;
    }
    if (p >= end || *p < '0' || *p > '9') return false;
    long long v = 0;
    while (p < end && *p >= '0' && *p <= '9') {
        v = v * 10 + (*p - '0');
        if (v > 0x7FFFFFFF) return false;  // token ids must fit in int
        ++p;
    }
    out = neg ? -v : v;
    return true;
}

// Parse {"<char>": <id>, ...} into codepoint -> id. Multi-codepoint keys
// cannot match a char-level encoder and are ignored.
inline bool json_parse_vocab(const char* p, const char* end,
                             std::unordered_map<uint32_t, int>& out) {
    out.clear();
    json_skip_ws(p, end);
    if (p >= end || *p != '{') return false;
    ++p;
    json_skip_ws(p, end);
    if (p < end && *p == '}') return true;  // empty object
    while (true) {
        json_skip_ws(p, end);
        std::string key;
        if (!json_parse_string(p, end, key)) return false;
        json_skip_ws(p, end);
        if (p >= end || *p != ':') return false;
        ++p;
        long long id = 0;
        if (!json_parse_int(p, end, id)) return false;
        std::vector<uint32_t> cps;
        utf8_to_codepoints(key, cps);
        if (cps.size() == 1 && id >= 0) out[cps[0]] = (int)id;
        json_skip_ws(p, end);
        if (p < end && *p == ',') {
            ++p;
            continue;
        }
        if (p < end && *p == '}') return true;
        return false;
    }
}

}  // namespace detail

// ---------------------------------------------------------------------------
// NeuralScorer
// ---------------------------------------------------------------------------
class NeuralScorer {
public:
    static constexpr int kPadId = 0;  // padding id (kept for encoder parity)
    static constexpr int kBosId = 2;  // beginning-of-sequence token id
    // Encoder caps: context keeps its LAST kMaxContextChars code points,
    // candidates keep their FIRST kMaxCandidateChars.
    static constexpr size_t kMaxContextChars = 160;
    static constexpr size_t kMaxCandidateChars = 31;
    static constexpr float kLnEps = 1e-5f;

    NeuralScorer() = default;
    ~NeuralScorer() { release(); }
    NeuralScorer(const NeuralScorer&) = delete;
    NeuralScorer& operator=(const NeuralScorer&) = delete;
    NeuralScorer(NeuralScorer&&) = delete;
    NeuralScorer& operator=(NeuralScorer&&) = delete;

    // mmap the weight file (format documented at the top of this file).
    // Returns false on any I/O, format, or size mismatch. On failure the
    // scorer stays in the unloaded state.
    bool load(const char* path);

    // Load vocab.json: {"字": id, ...} (UTF-8 char -> token id).
    // May be called before or after load(); the map survives a reload of the
    // weights.
    bool load_vocab(const char* vocab_json_path);

    bool is_loaded() const { return data_ != nullptr; }
    bool vocab_loaded() const { return !vocab_cp_.empty(); }
    size_t param_count() const { return param_count_; }

    // Batch scoring: for candidate i, return
    //   sum_{k} logP(candidate[k] | BOS, context, candidate[0..k-1])
    // (characters missing from the vocab are skipped, so the score covers the
    // in-vocab characters actually encoded). Larger is better. Candidates that
    // encode to nothing score 0. Returns zeros when the model or vocab is not
    // loaded.
    std::vector<float> score(const std::string& context,
                             const std::vector<std::string>& candidates);

    // Convenience overload: (re)load vocab.json, then score. Returns zeros if
    // the vocab cannot be loaded.
    std::vector<float> score(const std::string& context,
                             const std::vector<std::string>& candidates,
                             const char* vocab_json_path) {
        if (vocab_json_path == nullptr || !load_vocab(vocab_json_path))
            return std::vector<float>(candidates.size(), 0.0f);
        return score(context, candidates);
    }

private:
    struct LayerWeights {
        const float* ln1_g = nullptr;
        const float* ln1_b = nullptr;
        const float* qkv_w = nullptr;  // [3D, D] Q/K/V rows stacked
        const float* qkv_b = nullptr;  // [3D]
        const float* ao_w = nullptr;   // [D, D]
        const float* ao_b = nullptr;   // [D]
        const float* ln2_g = nullptr;
        const float* ln2_b = nullptr;
        const float* ff1_w = nullptr;  // [F, D]
        const float* ff1_b = nullptr;  // [F]
        const float* ff2_w = nullptr;  // [D, F]
        const float* ff2_b = nullptr;  // [D]
    };

    // One packed sequence: [BOS, context..., candidate...].
    struct SeqInfo {
        size_t base = 0;      // first row of this sequence in the batch
        uint32_t len = 0;     // 1 + ctx_n + cand_n
        uint32_t ctx_n = 0;   // context tokens actually used
        uint32_t cand_n = 0;  // candidate tokens
        const int* cand_ids = nullptr;
        uint32_t cand_idx = 0;  // index into the score output
    };

    // ---- model config (from the binary header) ----
    uint32_t d_ = 0;
    uint32_t n_layers_ = 0;
    uint32_t n_heads_ = 0;
    uint32_t vocab_size_ = 0;
    uint32_t max_len_ = 0;
    uint32_t ffn_dim_ = 0;
    uint32_t d_head_ = 0;
    size_t param_count_ = 0;

    // ---- mmap-backed weights ----
    void* map_ = nullptr;
    size_t map_size_ = 0;
    const float* data_ = nullptr;  // first float after the 64-byte header
    const float* w_embed_ = nullptr;
    const float* w_pos_ = nullptr;
    const float* w_lnf_g_ = nullptr;
    const float* w_lnf_b_ = nullptr;
    const float* w_head_ = nullptr;
    std::vector<LayerWeights> layers_;

    // ---- vocab: code point -> token id ----
    std::unordered_map<uint32_t, int> vocab_cp_;

    // ---- preallocated scratch buffers ----
    // Row-keyed buffers grow once to the high-water mark and are reused;
    // model-dim-only buffers are sized in load().
    size_t cap_rows_ = 0;
    std::vector<int> ids_;                        // [rows]
    std::vector<float> x_;                        // [rows, D]  activations
    std::vector<float> normed_;                   // [rows, D]  LN output
    std::vector<float> qkv_;                      // [rows, 3D]
    std::vector<float> attn_out_;                 // [rows, D]  concat heads
    std::vector<float> proj_;                     // [rows, D]  linear output
    std::vector<float> ff_;                       // [rows, F]
    std::vector<float> attn_scores_;              // [t_cap, t_cap]
    std::vector<float> gather_;                   // [logit_chunk_, D]
    std::vector<float> logits_;                   // [logit_chunk_, V]
    size_t logit_chunk_ = 0;

    void release();
    void ensure_row_capacity(size_t rows);
    void encode_text(const std::string& text, bool keep_last, size_t max_chars,
                     std::vector<int>& out) const;
    void layer_norm_rows(const float* src, const float* gamma, const float* beta,
                         float* dst, size_t rows) const;
    void gemm_nt(const float* a, const float* w, size_t rows, size_t in_dim,
                 size_t out_dim, float* c) const;
    void add_bias_rows(float* m, const float* bias, size_t rows, size_t dim) const;
    static void gelu_inplace(float* v, size_t n);
    void causal_softmax(float* s, uint32_t t) const;
    void attention(const SeqInfo& seq);
    void forward(size_t total_rows, const std::vector<SeqInfo>& seqs);
};

// ---------------------------------------------------------------------------
// Inline implementation
// ---------------------------------------------------------------------------

inline void NeuralScorer::release() {
    if (map_ != nullptr) {
        munmap(map_, map_size_);
        map_ = nullptr;
    }
    map_size_ = 0;
    data_ = nullptr;
    w_embed_ = w_pos_ = w_lnf_g_ = w_lnf_b_ = w_head_ = nullptr;
    layers_.clear();
    d_ = n_layers_ = n_heads_ = vocab_size_ = max_len_ = ffn_dim_ = d_head_ = 0;
    param_count_ = 0;
    cap_rows_ = 0;
    logit_chunk_ = 0;
    std::vector<int>().swap(ids_);
    std::vector<float>().swap(x_);
    std::vector<float>().swap(normed_);
    std::vector<float>().swap(qkv_);
    std::vector<float>().swap(attn_out_);
    std::vector<float>().swap(proj_);
    std::vector<float>().swap(ff_);
    std::vector<float>().swap(attn_scores_);
    std::vector<float>().swap(gather_);
    std::vector<float>().swap(logits_);
    // vocab_cp_ is intentionally kept: it is independent of the weights.
}

inline bool NeuralScorer::load(const char* path) {
    release();
    if (path == nullptr) return false;

    const int fd = ::open(path, O_RDONLY);
    if (fd < 0) return false;

    struct stat st;
    if (::fstat(fd, &st) != 0 || st.st_size < (off_t)64) {
        ::close(fd);
        return false;
    }
    const size_t file_size = (size_t)st.st_size;

    void* m = mmap(nullptr, file_size, PROT_READ, MAP_PRIVATE, fd, 0);
    ::close(fd);
    if (m == MAP_FAILED) return false;

    const unsigned char* bytes = (const unsigned char*)m;
    if (std::memcmp(bytes, "MOHU_NLM", 8) != 0) {
        munmap(m, file_size);
        return false;
    }
    const uint32_t version = detail::read_u32_le(bytes + 8);
    const uint32_t d = detail::read_u32_le(bytes + 12);
    const uint32_t nl = detail::read_u32_le(bytes + 16);
    const uint32_t nh = detail::read_u32_le(bytes + 20);
    const uint32_t vs = detail::read_u32_le(bytes + 24);
    const uint32_t ml = detail::read_u32_le(bytes + 28);
    const uint32_t ff = detail::read_u32_le(bytes + 32);

    if (version != 1 || d == 0 || nl == 0 || nh == 0 || vs == 0 || ml < 2 ||
        ff == 0 || d % nh != 0) {
        munmap(m, file_size);
        return false;
    }

    const size_t per_layer =
        2 * (size_t)d +                          // ln1_g + ln1_b
        3 * (size_t)d * d + 3 * (size_t)d +      // qkv_w + qkv_b
        (size_t)d * d + (size_t)d +              // ao_w + ao_b
        2 * (size_t)d +                          // ln2_g + ln2_b
        (size_t)ff * d + (size_t)ff +            // ff1_w + ff1_b
        (size_t)d * ff + (size_t)d;              // ff2_w + ff2_b
    const size_t floats =
        (size_t)vs * d +   // embed
        (size_t)ml * d +   // pos
        (size_t)nl * per_layer + 2 * (size_t)d +  // blocks + ln_f
        (size_t)vs * d;    // head

    if (file_size < 64 + floats * sizeof(float)) {
        munmap(m, file_size);
        return false;
    }

    map_ = m;
    map_size_ = file_size;
    data_ = (const float*)(bytes + 64);
    d_ = d;
    n_layers_ = nl;
    n_heads_ = nh;
    vocab_size_ = vs;
    max_len_ = ml;
    ffn_dim_ = ff;
    d_head_ = d / nh;
    param_count_ = floats;

    const float* q = data_;
    auto take = [&q](size_t n) {
        const float* r = q;
        q += n;
        return r;
    };
    w_embed_ = take((size_t)vs * d);
    w_pos_ = take((size_t)ml * d);
    layers_.resize(nl);
    for (LayerWeights& lw : layers_) {
        lw.ln1_g = take(d);
        lw.ln1_b = take(d);
        lw.qkv_w = take(3 * (size_t)d * d);
        lw.qkv_b = take(3 * (size_t)d);
        lw.ao_w = take((size_t)d * d);
        lw.ao_b = take(d);
        lw.ln2_g = take(d);
        lw.ln2_b = take(d);
        lw.ff1_w = take((size_t)ff * d);
        lw.ff1_b = take(ff);
        lw.ff2_w = take((size_t)d * ff);
        lw.ff2_b = take(d);
    }
    w_lnf_g_ = take(d);
    w_lnf_b_ = take(d);
    w_head_ = take((size_t)vs * d);

    // Scratch that depends only on model dims. The attention score matrix
    // never needs to exceed 1 (BOS) + 160 + 31 tokens (nor max_len).
    const size_t t_cap =
        std::min<size_t>((size_t)max_len_, 1 + kMaxContextChars + kMaxCandidateChars);
    attn_scores_.assign(t_cap * t_cap, 0.0f);
    // Cap the logit tile at ~16 MiB so the vocab projection stays cache- and
    // allocator-friendly even for large vocabularies.
    logit_chunk_ =
        std::max<size_t>(1, (size_t)(16u << 20) / (sizeof(float) * (size_t)vs));
    gather_.assign(logit_chunk_ * (size_t)d, 0.0f);
    logits_.assign(logit_chunk_ * (size_t)vs, 0.0f);
    return true;
}

inline bool NeuralScorer::load_vocab(const char* vocab_json_path) {
    if (vocab_json_path == nullptr) return false;
    FILE* f = std::fopen(vocab_json_path, "rb");
    if (f == nullptr) return false;
    std::string buf;
    char tmp[65536];
    size_t n;
    while ((n = std::fread(tmp, 1, sizeof(tmp), f)) > 0) buf.append(tmp, n);
    std::fclose(f);
    if (buf.empty()) return false;

    const char* p = buf.data();
    const char* end = p + buf.size();
    // Tolerate a UTF-8 BOM.
    if (end - p >= 3 && (unsigned char)p[0] == 0xEF && (unsigned char)p[1] == 0xBB &&
        (unsigned char)p[2] == 0xBF) {
        p += 3;
    }
    std::unordered_map<uint32_t, int> parsed;
    if (!detail::json_parse_vocab(p, end, parsed) || parsed.empty()) return false;
    vocab_cp_.swap(parsed);
    return true;
}

inline void NeuralScorer::ensure_row_capacity(size_t rows) {
    if (rows <= cap_rows_) return;
    cap_rows_ = rows;
    const size_t d = d_;
    ids_.assign(rows, 0);
    x_.assign(rows * d, 0.0f);
    normed_.assign(rows * d, 0.0f);
    attn_out_.assign(rows * d, 0.0f);
    proj_.assign(rows * d, 0.0f);
    qkv_.assign(rows * 3 * d, 0.0f);
    ff_.assign(rows * (size_t)ffn_dim_, 0.0f);
}

// Encode UTF-8 text into token ids. Characters missing from the vocab are
// skipped. `max_chars` bounds the number of code points consumed, counted
// before OOV filtering; keep_last=true keeps the tail (context), false the
// head (candidates).
inline void NeuralScorer::encode_text(const std::string& text, bool keep_last,
                                      size_t max_chars, std::vector<int>& out) const {
    out.clear();
    std::vector<uint32_t> cps;
    detail::utf8_to_codepoints(text, cps);
    size_t start = 0;
    size_t n = cps.size();
    if (n > max_chars) {
        if (keep_last) start = n - max_chars;
        n = max_chars;
    }
    for (size_t i = 0; i < n; ++i) {
        const auto it = vocab_cp_.find(cps[start + i]);
        if (it != vocab_cp_.end() && (uint32_t)it->second < vocab_size_)
            out.push_back(it->second);
    }
}

// Row-wise LayerNorm: y = (x - mean) * rsqrt(var + eps) * gamma + beta.
// In-place (src == dst) is safe: each row is fully reduced before written.
inline void NeuralScorer::layer_norm_rows(const float* src, const float* gamma,
                                          const float* beta, float* dst,
                                          size_t rows) const {
    const size_t d = d_;
    for (size_t r = 0; r < rows; ++r, src += d, dst += d) {
        float mean = 0.0f;
        for (size_t k = 0; k < d; ++k) mean += src[k];
        mean /= (float)d;
        float var = 0.0f;
        for (size_t k = 0; k < d; ++k) {
            const float t = src[k] - mean;
            var += t * t;
        }
        var /= (float)d;
        const float inv = 1.0f / std::sqrt(var + kLnEps);
        for (size_t k = 0; k < d; ++k)
            dst[k] = (src[k] - mean) * inv * gamma[k] + beta[k];
    }
}

// c[rows, out_dim] = a[rows, in_dim] * w[out_dim, in_dim]^T
// (row-major, all leading dimensions equal the logical dims).
inline void NeuralScorer::gemm_nt(const float* a, const float* w, size_t rows,
                                  size_t in_dim, size_t out_dim, float* c) const {
    if (rows == 0) return;
    cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, (int)rows, (int)out_dim,
                (int)in_dim, 1.0f, a, (int)in_dim, w, (int)in_dim, 0.0f, c,
                (int)out_dim);
}

inline void NeuralScorer::add_bias_rows(float* m, const float* bias, size_t rows,
                                        size_t dim) const {
    for (size_t r = 0; r < rows; ++r) {
        float* row = m + r * dim;
        for (size_t k = 0; k < dim; ++k) row[k] += bias[k];
    }
}

// Exact GELU: 0.5x(1 + tanh(sqrt(2/pi)(x + 0.044715 x^3)))
inline void NeuralScorer::gelu_inplace(float* v, size_t n) {
    constexpr float kSqrt2Pi = 0.7978845608028654f;
    for (size_t i = 0; i < n; ++i) {
        const float x = v[i];
        v[i] = 0.5f * x * (1.0f + std::tanh(kSqrt2Pi * (x + 0.044715f * x * x * x)));
    }
}

// In-place causal softmax over a [t, t] row-major score matrix: row i only
// attends to columns 0..i, everything above the diagonal becomes 0.
inline void NeuralScorer::causal_softmax(float* s, uint32_t t) const {
    for (uint32_t i = 0; i < t; ++i) {
        float* r = s + (size_t)i * t;
        float m = r[0];
        for (uint32_t j = 1; j <= i; ++j)
            if (r[j] > m) m = r[j];
        float sum = 0.0f;
        for (uint32_t j = 0; j <= i; ++j) {
            r[j] = std::exp(r[j] - m);
            sum += r[j];
        }
        const float inv = 1.0f / sum;
        for (uint32_t j = 0; j <= i; ++j) r[j] *= inv;
        for (uint32_t j = i + 1; j < t; ++j) r[j] = 0.0f;
    }
}

// Causal multi-head attention for one packed sequence. Q/K/V are strided
// views into the [len, 3D] qkv buffer; the per-head outputs land directly at
// their column offset in the sequence's [len, D] slice of attn_out_.
inline void NeuralScorer::attention(const SeqInfo& seq) {
    const uint32_t t = seq.len;
    const uint32_t dh = d_head_;
    const int stride3d = (int)(3 * (size_t)d_);
    const float scale = 1.0f / std::sqrt((float)dh);
    const float* qkv = qkv_.data() + seq.base * 3 * (size_t)d_;
    float* attn = attn_out_.data() + seq.base * (size_t)d_;
    float* s = attn_scores_.data();
    for (uint32_t h = 0; h < n_heads_; ++h) {
        const float* qh = qkv + h * dh;
        const float* kh = qkv + d_ + h * dh;
        const float* vh = qkv + 2 * (size_t)d_ + h * dh;
        // scores = (Q K^T / sqrt(d_head)) with the scale folded into alpha.
        cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, (int)t, (int)t,
                    (int)dh, scale, qh, stride3d, kh, stride3d, 0.0f, s, (int)t);
        causal_softmax(s, t);
        // out_head = P V, written into column slice [h*dh, (h+1)*dh).
        cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, (int)t, (int)dh,
                    (int)t, 1.0f, s, (int)t, vh, stride3d, 0.0f, attn + h * dh,
                    (int)d_);
    }
}

// Full pre-LN transformer forward over the packed batch. x_ ends up holding
// the final LayerNorm-ed hidden states.
inline void NeuralScorer::forward(size_t total_rows, const std::vector<SeqInfo>& seqs) {
    const size_t d = d_;
    const size_t d3 = 3 * d;
    const size_t nd = total_rows * d;

    // Embedding lookup + learned positions.
    for (const SeqInfo& seq : seqs) {
        const int* ids = ids_.data() + seq.base;
        float* x = x_.data() + seq.base * d;
        for (uint32_t t = 0; t < seq.len; ++t) {
            const float* e = w_embed_ + (size_t)ids[t] * d;
            const float* pe = w_pos_ + (size_t)t * d;
            float* row = x + (size_t)t * d;
            for (size_t k = 0; k < d; ++k) row[k] = e[k] + pe[k];
        }
    }

    for (uint32_t li = 0; li < n_layers_; ++li) {
        const LayerWeights& w = layers_[li];

        // x += Attn(LN1(x))
        layer_norm_rows(x_.data(), w.ln1_g, w.ln1_b, normed_.data(), total_rows);
        gemm_nt(normed_.data(), w.qkv_w, total_rows, d, d3, qkv_.data());
        add_bias_rows(qkv_.data(), w.qkv_b, total_rows, d3);
        for (const SeqInfo& seq : seqs) attention(seq);
        gemm_nt(attn_out_.data(), w.ao_w, total_rows, d, d, proj_.data());
        add_bias_rows(proj_.data(), w.ao_b, total_rows, d);
        for (size_t i = 0; i < nd; ++i) x_[i] += proj_[i];

        // x += FF(LN2(x))
        layer_norm_rows(x_.data(), w.ln2_g, w.ln2_b, normed_.data(), total_rows);
        gemm_nt(normed_.data(), w.ff1_w, total_rows, d, ffn_dim_, ff_.data());
        add_bias_rows(ff_.data(), w.ff1_b, total_rows, ffn_dim_);
        gelu_inplace(ff_.data(), total_rows * (size_t)ffn_dim_);
        gemm_nt(ff_.data(), w.ff2_w, total_rows, ffn_dim_, d, proj_.data());
        add_bias_rows(proj_.data(), w.ff2_b, total_rows, d);
        for (size_t i = 0; i < nd; ++i) x_[i] += proj_[i];
    }

    // Final LayerNorm, in place (row-local, safe).
    layer_norm_rows(x_.data(), w_lnf_g_, w_lnf_b_, x_.data(), total_rows);
}

inline std::vector<float> NeuralScorer::score(const std::string& context,
                                              const std::vector<std::string>& candidates) {
    std::vector<float> out(candidates.size(), 0.0f);
    if (!is_loaded() || vocab_cp_.empty() || candidates.empty()) return out;

    // Shared context: encode once, keep the LAST 160 code points.
    std::vector<int> ctx_ids;
    encode_text(context, /*keep_last=*/true, kMaxContextChars, ctx_ids);

    // Per-candidate encoding + sequence planning. All sequences are packed
    // back-to-back so every linear layer is a single GEMM for the batch.
    std::vector<std::vector<int>> cand_ids(candidates.size());
    std::vector<SeqInfo> seqs;
    seqs.reserve(candidates.size());
    size_t total_rows = 0;
    for (size_t ci = 0; ci < candidates.size(); ++ci) {
        std::vector<int>& ids = cand_ids[ci];
        encode_text(candidates[ci], /*keep_last=*/false, kMaxCandidateChars, ids);
        if (ids.size() > (size_t)max_len_ - 1) ids.resize((size_t)max_len_ - 1);
        if (ids.empty()) continue;  // nothing scoreable
        SeqInfo seq;
        seq.cand_n = (uint32_t)ids.size();
        seq.ctx_n = (uint32_t)std::min<size_t>(ctx_ids.size(),
                                               (size_t)max_len_ - 1 - seq.cand_n);
        seq.len = 1 + seq.ctx_n + seq.cand_n;
        seq.base = total_rows;
        seq.cand_ids = ids.data();
        seq.cand_idx = (uint32_t)ci;
        total_rows += seq.len;
        seqs.push_back(seq);
    }
    if (seqs.empty()) return out;

    ensure_row_capacity(total_rows);

    // Pack ids: [BOS, context tail..., candidate...].
    for (const SeqInfo& seq : seqs) {
        int* dst = ids_.data() + seq.base;
        *dst++ = kBosId;
        for (uint32_t k = 0; k < seq.ctx_n; ++k)
            dst[k] = ctx_ids[ctx_ids.size() - seq.ctx_n + k];
        dst += seq.ctx_n;
        for (uint32_t k = 0; k < seq.cand_n; ++k) dst[k] = seq.cand_ids[k];
    }

    forward(total_rows, seqs);

    // Output logits are only needed at positions that predict a candidate
    // token: hidden state at position p predicts token p+1, so candidate
    // char k is predicted by row base + ctx_n + k. Gather those rows (chunked
    // to bound the logit tile) instead of projecting the whole sequence.
    std::vector<size_t> need_row;
    std::vector<int> need_target;
    std::vector<uint32_t> need_cand;
    need_row.reserve(total_rows);  // upper bound; avoids regrowth below
    need_target.reserve(total_rows);
    need_cand.reserve(total_rows);
    for (const SeqInfo& seq : seqs) {
        const size_t pred_base = seq.base + seq.ctx_n;
        for (uint32_t k = 0; k < seq.cand_n; ++k) {
            need_row.push_back(pred_base + k);
            need_target.push_back(seq.cand_ids[k]);
            need_cand.push_back(seq.cand_idx);
        }
    }

    const size_t d = d_;
    const size_t n_need = need_row.size();
    for (size_t c0 = 0; c0 < n_need; c0 += logit_chunk_) {
        const size_t n = std::min(logit_chunk_, n_need - c0);
        for (size_t i = 0; i < n; ++i)
            std::memcpy(gather_.data() + i * d, x_.data() + need_row[c0 + i] * d,
                        d * sizeof(float));
        // logits = gather @ head_w^T
        cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, (int)n,
                    (int)vocab_size_, (int)d, 1.0f, gather_.data(), (int)d, w_head_,
                    (int)d, 0.0f, logits_.data(), (int)vocab_size_);
        for (size_t i = 0; i < n; ++i) {
            const float* lg = logits_.data() + i * (size_t)vocab_size_;
            float m = lg[0];
            for (size_t v = 1; v < (size_t)vocab_size_; ++v)
                if (lg[v] > m) m = lg[v];
            double sum_exp = 0.0;
            for (size_t v = 0; v < (size_t)vocab_size_; ++v)
                sum_exp += std::exp((double)lg[v] - (double)m);
            const float lse = m + (float)std::log(sum_exp);
            // logP(target) = logit[target] - logsumexp(logits)
            out[need_cand[c0 + i]] += lg[need_target[c0 + i]] - lse;
        }
    }
    return out;
}

}  // namespace nlm
}  // namespace mohu
