// libtigerengine 的 Lua 5.4 绑定（luaopen_tigerengine）。
// 编译用 -undefined dynamic_lookup，加载时绑定宿主（librime-lua）的 Lua 符号。
// Lua 侧用法：
//   local t = require("tigerengine")            -- 或 package.loadlib(path, "luaopen_tigerengine")()
//   local h, err = t.create(model, lexicon, beam, all_ranks)
//   local text, ms = t.decode(h, raw, include_early)
//   text 首行: truncated early_truncated uses_incomplete prefers_incomplete n_final n_early
//              consensus_complete consensus_text_bytes consensus_raw_length visible_consensus
//   其后每候选: text \t segmented \t score \t confidence \t max_rank \t pathmap
//   t.status(h), t.last_error(), t.free(h)
#include "lua-5.4.6/src/lua.hpp"
#include "tigerengine.h"

#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <string>
#include <vector>

namespace {

char g_out[1 << 20];  // 单线程：Rime 引擎内 Lua 串行调用
std::mutex g_lua_binding_mutex;

int l_create(lua_State* L) {
  const char* model = luaL_checkstring(L, 1);
  const char* lex = luaL_checkstring(L, 2);
  lua_Integer beam_value = luaL_optinteger(L, 3, 200);
  luaL_argcheck(L, beam_value >= std::numeric_limits<int>::min() &&
                       beam_value <= std::numeric_limits<int>::max(),
                3, "beam width is out of range");
  int beam = (int)beam_value;
  int all_ranks = luaL_opt(L, lua_toboolean, 4, 1);
  char err[512] = {0};
  int h;
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    h = tiger_engine_create(model, lex, beam, all_ranks, err, sizeof(err));
  }
  if (h < 0) {
    lua_pushnil(L);
    lua_pushstring(L, err);
    return 2;
  }
  lua_pushinteger(L, h);
  return 1;
}

int l_free(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    tiger_engine_free((int)handle_value);
  }
  return 0;
}

int l_decode(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* raw = luaL_checkstring(L, 2);
  int early = lua_toboolean(L, 3);
  int h = (int)handle_value;
  double ms = 0;
  int rc;
  char error[512] = {0};
  std::string output;
  try {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_decode(h, raw, early, g_out, sizeof(g_out), &ms);
    if (rc < 0) {
      snprintf(error, sizeof(error), "%s", tiger_last_error());
    } else {
      output.assign(g_out);
    }
  } catch (...) {
    luaL_error(L, "native decode allocation failed");
    return 0;
  }
  if (rc < 0) {
    lua_pushnil(L);
    lua_pushstring(L, error);
    return 2;
  }
  lua_pushlstring(L, output.data(), output.size());
  lua_pushnumber(L, ms);
  return 2;
}

int l_set_personal_lexicon(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* rows = luaL_checkstring(L, 2);
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_set_personal_lexicon((int)handle_value, rows);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc != 0) return luaL_error(L, "%s", error[0] ? error : "personal lexicon update failed");
  return 0;
}

int l_personal_begin(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_personal_begin((int)handle_value);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc != 0) return luaL_error(L, "%s", error[0] ? error : "personal transaction failed");
  return 0;
}

int l_personal_append(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* rows = luaL_checkstring(L, 2);
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_personal_append((int)handle_value, rows);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc != 0) return luaL_error(L, "%s", error[0] ? error : "personal transaction append failed");
  return 0;
}

int l_personal_commit(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_personal_commit((int)handle_value);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc != 0) return luaL_error(L, "%s", error[0] ? error : "personal transaction commit failed");
  return 0;
}

int l_personal_abort(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_personal_abort((int)handle_value);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc != 0) return luaL_error(L, "%s", error[0] ? error : "personal transaction abort failed");
  return 0;
}

int l_update_user_model(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* text = luaL_checkstring(L, 2);
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_update_user_model((int)handle_value, text);
    if (rc < 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc < 0) return luaL_error(L, "%s", error[0] ? error : "user model update failed");
  lua_pushinteger(L, rc);  // 0 = 无变化，1 = 已应用
  return 1;
}

int l_set_decode_context(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* text = luaL_checkstring(L, 2);
  const int window = lua_isnoneornil(L, 3) ? 0 : (int)luaL_checkinteger(L, 3);
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_set_decode_context((int)handle_value, text, window);
    if (rc < 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc < 0) return luaL_error(L, "%s", error[0] ? error : "decode context update failed");
  lua_pushinteger(L, rc);  // 0 = 无变化，1 = 已应用
  return 1;
}

int l_load_word_scorer(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* path = luaL_checkstring(L, 2);
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_load_word_scorer((int)handle_value, path);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc != 0) {
    lua_pushboolean(L, 0);
    lua_pushstring(L, error[0] ? error : "word scorer load failed");
    return 2;
  }
  lua_pushboolean(L, 1);
  return 1;
}

int l_context_word_scores(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* context = luaL_checkstring(L, 2);
  luaL_checktype(L, 3, LUA_TTABLE);
  const int window = lua_isnoneornil(L, 4) ? 0 : (int)luaL_checkinteger(L, 4);
  const lua_Integer n = luaL_len(L, 3);
  luaL_argcheck(L, n >= 0 && n <= 256, 3, "candidate count out of range");
  // 候选拼接与类型检查在锁外完成（luaL_* 可能抛 Lua 错误，不可持锁）。
  std::string joined;
  if (n > 0) {
    for (lua_Integer i = 1; i <= n; ++i) {
      lua_rawgeti(L, 3, i);
      const char* text = luaL_checkstring(L, -1);
      if (i > 1) joined.push_back('\n');
      joined.append(text);
      lua_pop(L, 1);
    }
  }
  std::vector<double> scores((size_t)n, 0.0);
  int rc = 0;
  char error[512] = {0};
  if (n > 0) {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_context_word_scores((int)handle_value, context,
                                          joined.c_str(), (int)n, window,
                                          scores.data());
    if (rc < 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc < 0) {
    lua_pushnil(L);
    lua_pushstring(L, error[0] ? error : "word scores failed");
    return 2;
  }
  lua_createtable(L, (int)n, 0);
  for (lua_Integer i = 0; i < n; ++i) {
    lua_pushnumber(L, scores[(size_t)i]);
    lua_rawseti(L, -2, i + 1);
  }
  return 1;
}

int l_context_char_scores(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  const char* context = luaL_checkstring(L, 2);
  luaL_checktype(L, 3, LUA_TTABLE);
  const lua_Integer n = luaL_len(L, 3);
  luaL_argcheck(L, n >= 0 && n <= 256, 3, "candidate count out of range");
  // 候选拼接与类型检查在锁外完成（luaL_* 可能抛 Lua 错误，不可持锁）。
  std::string joined;
  if (n > 0) {
    for (lua_Integer i = 1; i <= n; ++i) {
      lua_rawgeti(L, 3, i);
      const char* text = luaL_checkstring(L, -1);
      if (i > 1) joined.push_back('\n');
      joined.append(text);
      lua_pop(L, 1);
    }
  }
  std::vector<double> scores((size_t)n, 0.0);
  int rc = 0;
  char error[512] = {0};
  if (n > 0) {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_context_char_scores((int)handle_value, context,
                                          joined.c_str(), (int)n, scores.data());
    if (rc < 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc < 0) {
    lua_pushnil(L);
    lua_pushstring(L, error[0] ? error : "char scores failed");
    return 2;
  }
  lua_createtable(L, (int)n, 0);
  for (lua_Integer i = 0; i < n; ++i) {
    lua_pushnumber(L, scores[(size_t)i]);
    lua_rawseti(L, -2, i + 1);
  }
  return 1;
}

int l_set_user_model_weight(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  double weight = luaL_checknumber(L, 2);
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_set_user_model_weight((int)handle_value, weight);
    if (rc != 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc < 0) return luaL_error(L, "%s", error[0] ? error : "user model weight update failed");
  lua_pushboolean(L, 1);
  return 1;
}

int l_user_model_export(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  char* blob;
  size_t blob_size = 0;
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    blob = tiger_engine_user_model_export((int)handle_value, &blob_size);
  }
  if (!blob) {
    lua_pushnil(L);
    lua_pushstring(L, "user model export failed");
    return 2;
  }
  lua_pushlstring(L, blob, blob_size);  // 二进制 blob，可能含 NUL
  std::free(blob);
  return 1;
}

int l_user_model_import(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  size_t blob_size = 0;
  const char* blob = luaL_checklstring(L, 2, &blob_size);
  if (blob_size == 0) {
    return luaL_error(L, "user model snapshot is empty");
  }
  int rc;
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_engine_user_model_import((int)handle_value, blob, blob_size);
    if (rc < 0) std::snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  if (rc < 0) return luaL_error(L, "%s", error[0] ? error : "user model import failed");
  lua_pushinteger(L, rc);
  return 1;
}

int l_status(lua_State* L) {
  lua_Integer handle_value = luaL_checkinteger(L, 1);
  luaL_argcheck(L, handle_value >= std::numeric_limits<int>::min() &&
                       handle_value <= std::numeric_limits<int>::max(),
                1, "engine handle is out of range");
  int h = (int)handle_value;
  char buf[1024];
  int rc;
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    rc = tiger_status(h, buf, sizeof(buf));
  }
  if (rc != 0) {
    lua_pushnil(L);
    return 1;
  }
  lua_pushstring(L, buf);
  return 1;
}

int l_last_error(lua_State* L) {
  char error[512] = {0};
  {
    std::lock_guard<std::mutex> lock(g_lua_binding_mutex);
    snprintf(error, sizeof(error), "%s", tiger_last_error());
  }
  lua_pushstring(L, error);
  return 1;
}

}  // namespace

extern "C" {
int luaopen_tigerengine(lua_State* L) {
  static const luaL_Reg funcs[] = {
      {"create", l_create},
      {"free", l_free},
      {"decode", l_decode},
      {"set_personal_lexicon", l_set_personal_lexicon},
      {"personal_begin", l_personal_begin},
      {"personal_append", l_personal_append},
      {"personal_commit", l_personal_commit},
      {"personal_abort", l_personal_abort},
      {"update_user_model", l_update_user_model},
      {"set_decode_context", l_set_decode_context},
      {"load_word_scorer", l_load_word_scorer},
      {"context_word_scores", l_context_word_scores},
      {"context_char_scores", l_context_char_scores},
      {"set_user_model_weight", l_set_user_model_weight},
      {"user_model_export", l_user_model_export},
      {"user_model_import", l_user_model_import},
      {"status", l_status},
      {"last_error", l_last_error},
      {nullptr, nullptr},
  };
  luaL_newlib(L, funcs);
  return 1;
}
}
