// semantic_http_e2e.cc — 端到端验收宿主：
// 链接 liblua.a 提供 luaL_* 符号，dlopen 部署目录的 libtigerengine.dylib，
// 经 lua 绑定调 semantic_http_score 走真实 HTTP 服务，输出与离线一致性。
#include <cstdio>
#include <cstring>
#include <string>
extern "C" {
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
}

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s <menus.json 摘要文件: 每行 ctx\\tcand1|cand2|...|native...>\n", argv[0]);
    return 2;
  }
  const char* dylib = argc > 2 ? argv[2]
      : "/Users/fuchuxuan/Library/Rime/mohu/runtime/libtigerengine.dylib";
  lua_State* L = luaL_newstate();
  luaL_openlibs(L);
  lua_getglobal(L, "package");
  lua_getfield(L, -1, "loadlib");
  if (!lua_isfunction(L, -1)) { std::fprintf(stderr, "no loadlib\n"); return 1; }
  lua_pushstring(L, dylib);
  lua_pushstring(L, "luaopen_tigerengine");
  if (lua_pcall(L, 2, 1, 0) != LUA_OK) {
    std::fprintf(stderr, "loadlib failed: %s\n", lua_tostring(L, -1));
    return 1;
  }
  if (lua_pcall(L, 0, 1, 0) != LUA_OK) {
    std::fprintf(stderr, "luaopen failed: %s\n", lua_tostring(L, -1));
    return 1;
  }
  lua_setglobal(L, "tigerengine");

  FILE* f = std::fopen(argv[1], "r");
  if (!f) { std::fprintf(stderr, "open %s failed\n", argv[1]); return 1; }
  char line[8192];
  int n = 0, agree = 0;
  while (std::fgets(line, sizeof(line), f)) {
    line[std::strcspn(line, "\n")] = 0;
    if (!line[0]) continue;
    // 格式: ctx \t cand1|cand2|... \t n1|n2|... \t expect_pick
    char* ctx = std::strtok(line, "\t");
    char* cands_s = std::strtok(nullptr, "\t");
    char* nat_s = std::strtok(nullptr, "\t");
    char* expect_s = std::strtok(nullptr, "\t");
    if (!ctx || !cands_s || !nat_s || !expect_s) continue;

    lua_getglobal(L, "tigerengine");
    lua_getfield(L, -1, "semantic_http_score");
    lua_pushstring(L, "http://127.0.0.1:8765");
    lua_pushstring(L, ctx);
    // candidates 表
    lua_createtable(L, 8, 0);
    lua_createtable(L, 8, 0);
    int ci = 1, ni = 1;
    for (char* t = std::strtok(cands_s, "|"); t; t = std::strtok(nullptr, "|")) {
      lua_pushstring(L, t);
      lua_rawseti(L, -3, ci++);
    }
    for (char* t = std::strtok(nat_s, "|"); t; t = std::strtok(nullptr, "|")) {
      lua_pushnumber(L, std::atof(t));
      lua_rawseti(L, -2, ni++);
    }
    if (lua_pcall(L, 4, 2, 0) != LUA_OK) {
      const char* err = lua_tostring(L, -1);
      std::fprintf(stderr, "[%d] call failed: %s\n", n, err ? err : "?");
      lua_pop(L, 3);
      continue;
    }
    if (lua_istable(L, -2)) {
      lua_Integer top = 1;
      for (lua_Integer i = 2; i <= ci - 1; ++i) {
        lua_rawgeti(L, -2, i);
        lua_Number v = lua_tonumber(L, -1);
        lua_pop(L, 1);
        lua_rawgeti(L, -2, top);
        lua_Number tv = lua_tonumber(L, -1);
        lua_pop(L, 1);
        if (v > tv) top = i;
      }
      if (n < 2) {
        lua_rawgeti(L, -2, 1);
        std::fprintf(stderr, "[dbg %d] s1=%.4f top=%lld expect=%s\n",
                     n, lua_tonumber(L, -1), static_cast<long long>(top),
                     expect_s);
        lua_pop(L, 1);
      }
      agree += (static_cast<int>(top) - 1 == std::atoi(expect_s));
      ++n;
    } else {
      const char* err = lua_isstring(L, -1) ? lua_tostring(L, -1) : "?";
      std::fprintf(stderr, "[%d] non-table result: %s\n", n, err);
    }
    lua_pop(L, 3);
  }
  std::fclose(f);
  std::printf("E2E: %d/%d menus agree with offline pick\n", agree, n);
  return 0;
}
