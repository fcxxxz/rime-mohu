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

#include <cstring>
#include <limits>
#include <mutex>
#include <string>

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
      {"status", l_status},
      {"last_error", l_last_error},
      {nullptr, nullptr},
  };
  luaL_newlib(L, funcs);
  return 1;
}
}
