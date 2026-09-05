#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/* Handles become invalid permanently after tiger_engine_free. */
int tiger_engine_create(const char* model_path, const char* lexicon_path,
                        int beam_width, int all_ranks_always,
                        char* error, int error_capacity);
/* Replace the personal phrase snapshot. Rows are code<TAB>text<TAB>commits. */
int tiger_engine_set_personal_lexicon(int handle, const char* rows);
/* Apply one positive commit delta immediately. Code is normalized bare
 * double-pinyin; an existing static edge is updated instead of duplicated. */
int tiger_engine_adjust_personal(int handle, const char* code, const char* text,
                                 int commits_delta);
/* Chunked snapshot transaction: begin, append newline-terminated row
 * batches, then commit. Decode keeps the previous snapshot until commit. */
int tiger_engine_personal_begin(int handle);
int tiger_engine_personal_append(int handle, const char* rows);
int tiger_engine_personal_commit(int handle);
int tiger_engine_personal_abort(int handle);
/* Adaptive user trigram layer: feed committed text (UTF-8). Returns
 * 0 = no change, 1 = applied (decode cache invalidated), -1 = error. */
int tiger_engine_update_user_model(int handle, const char* text);
/* static_weight is the static model's share in (0, 1]; 1 disables the layer. */
int tiger_engine_set_user_model_weight(int handle, double static_weight);
/* Reading prior weight in [0, 4]: scales the per-entry log P(reading|char)
 * prior derived from the lexicon's optional 5th column (reading-conditional
 * frequency). 0 disables, default 1.0. Returns 1 applied, 0 no change,
 * -1 error. Old lexicons without the column stay neutral at any weight. */
int tiger_engine_set_reading_prior_weight(int handle, double weight);
/* Snapshot blob of the user layer; caller owns and must free() the buffer.
 * *size_out receives the byte length (the blob is binary and may contain
 * NUL bytes). Empty model yields ""; NULL on error. */
char* tiger_engine_user_model_export(int handle, size_t* size_out);
/* Replace the user layer from a snapshot blob (binary, may contain NUL
 * bytes, explicit length); -1 on corrupt input. */
int tiger_engine_user_model_import(int handle, const char* blob, size_t blob_size);
/* Cross-commit left context: pass the WHOLE latest commit text (same source
 * as librime's GetPrecedingText); the engine extracts the trailing CJK
 * characters itself (character trigram window; window_chars <= 0 means the
 * default 2, values above 2 are clamped to 2 by the current model). Empty or
 * CJK-free text clears it. Returns 0 = no change, 1 = applied (decode cache
 * invalidated), -1 = error. */
int tiger_engine_set_decode_context(int handle, const char* text, int window_chars);
/* Explicit standalone word-level scoring model (MHKNM01). Optional: a
 * container model (MHCTN01) or a word-mode primary already provides the
 * word layer; an explicit load takes precedence. Returns 0 on success,
 * -1 on error (the engine itself stays usable). */
int tiger_engine_load_word_scorer(int handle, const char* model_path);
/* Batch word-level context scoring: candidates is '\n'-joined UTF-8 text
 * (candidate_count entries); out_scores receives logP(candidate | trailing
 * words of context_text). OOV candidates get -20 (no signal). window_words
 * <= 0 means the default 2 (values above 2 are clamped). Returns the count
 * written, or -1 on error. */
int tiger_engine_context_word_scores(int handle, const char* context_text,
                                     const char* candidates, int candidate_count,
                                     int window_words, double* out_scores);
/* Batch character-level continuation scoring (octagram-style): out_scores[i]
 * is the sum of logP(codepoint | last 2 CJK chars of context and previously
 * consumed chars) over candidate i's codepoints. Empty context yields the
 * BOS baseline (use the difference as a frequency-free lift). Char-level
 * primary models only; word_mode engines return -1. */
int tiger_engine_context_char_scores(int handle, const char* context_text,
                                     const char* candidates, int candidate_count,
                                     double* out_scores);
/* Read/write snapshot files through UTF-8 paths. The writer uses a flushed
 * same-directory temporary file and atomically replaces an existing target.
 * The caller owns and must free() the read buffer. */
char* tiger_read_snapshot_file(const char* path, size_t* size_out);
int tiger_atomic_write_snapshot_file(const char* path, const char* blob, size_t size);
void tiger_engine_free(int handle);
/* include_early is a deprecated ABI compatibility flag; the canonical Lua
 * translator always passes 0 and never exposes early-commit results. */
int tiger_decode(int handle, const char* raw, int include_early,
                 char* output, int output_capacity, double* elapsed_ms);
int tiger_decode_full(int handle, const char* raw, int include_early,
                      char* output, int output_capacity);
int tiger_status(int handle, char* output, int output_capacity);
const char* tiger_last_error(void);

#ifdef __cplusplus
}
#endif
