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
/* Chunked snapshot transaction: begin, append newline-terminated row
 * batches, then commit. Decode keeps the previous snapshot until commit. */
int tiger_engine_personal_begin(int handle);
int tiger_engine_personal_append(int handle, const char* rows);
int tiger_engine_personal_commit(int handle);
int tiger_engine_personal_abort(int handle);
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
