#include <cassert>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <unistd.h>
#include <vector>

#include "lua-5.4.6/src/lua.hpp"
#include "tigerengine.h"

extern "C" int luaopen_tigerengine(lua_State*);

namespace {

template <typename T>
void append_value(std::vector<uint8_t>* data, const T& value) {
  const size_t offset = data->size();
  data->resize(offset + sizeof(value));
  std::memcpy(data->data() + offset, &value, sizeof(value));
}

std::string write_model() {
  std::vector<uint8_t> model;
  model.insert(model.end(), {'T', 'C', 'S', 'K', 'N', 'M', '0', '1'});
  append_value<uint32_t>(&model, 1);
  append_value<uint32_t>(&model, 1);
  append_value<uint32_t>(&model, 0);
  append_value<float>(&model, 0.1f);
  append_value<uint64_t>(&model, 0);
  append_value<int32_t>(&model, 0);
  append_value<uint64_t>(&model, 0);
  append_value<uint64_t>(&model, 0);
  const std::string path = "/tmp/mohu-tiger-lua-safety-" + std::to_string(getpid()) + ".bin";
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  assert(stream);
  stream.write(reinterpret_cast<const char*>(model.data()),
               static_cast<std::streamsize>(model.size()));
  assert(stream);
  return path;
}

std::string write_lexicon() {
  const std::string path = "/tmp/mohu-tiger-lua-safety-" + std::to_string(getpid()) + ".txt";
  std::ofstream stream(path, std::ios::trunc);
  assert(stream);
  stream << "a\t候选\t1\t1\n";
  assert(stream);
  return path;
}

void timeout_handler(int) { _exit(99); }

void push_method(lua_State* state, const char* name) {
  lua_getglobal(state, "tiger");
  lua_getfield(state, -1, name);
  lua_remove(state, -2);
  assert(lua_isfunction(state, -1));
}

void expect_lua_error(lua_State* state, const char* method, int argument_count) {
  const int status = lua_pcall(state, argument_count, 0, 0);
  assert(status != LUA_OK);
  lua_settop(state, 0);
  (void)method;
}

void expect_valid_status(lua_State* state, int handle) {
  push_method(state, "status");
  lua_pushinteger(state, handle);
  assert(lua_pcall(state, 1, 1, 0) == LUA_OK);
  assert(lua_isstring(state, -1));
  lua_settop(state, 0);
}

void expect_valid_decode(lua_State* state, int handle) {
  push_method(state, "decode");
  lua_pushinteger(state, handle);
  lua_pushliteral(state, "a");
  lua_pushboolean(state, 0);
  assert(lua_pcall(state, 3, 2, 0) == LUA_OK);
  assert(lua_isstring(state, -2));
  assert(lua_isnumber(state, -1));
  lua_settop(state, 0);
}

}  // namespace

int main() {
  std::signal(SIGALRM, timeout_handler);
  alarm(3);

  const std::string model_path = write_model();
  const std::string lexicon_path = write_lexicon();
  char error[512] = {};
  const int handle = tiger_engine_create(model_path.c_str(), lexicon_path.c_str(), 200, 1,
                                         error, sizeof(error));
  assert(handle >= 0);

  lua_State* state = luaL_newstate();
  assert(state);
  luaL_openlibs(state);
  luaopen_tigerengine(state);
  lua_setglobal(state, "tiger");

  // Character scoring keeps its first return table compatible and publishes
  // dense boolean boundary metadata as its second return value.
  push_method(state, "set_learning_context_guard");
  lua_pushinteger(state, handle);
  lua_pushboolean(state, 1);
  assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
  assert(lua_toboolean(state, -1));
  lua_settop(state, 0);
  push_method(state, "context_char_scores");
  lua_pushinteger(state, handle);
  lua_pushliteral(state, "编辑");
  lua_createtable(state, 2, 0);
  lua_pushliteral(state, "码表"); lua_rawseti(state, -2, 1);
  lua_pushliteral(state, "马标"); lua_rawseti(state, -2, 2);
  assert(lua_pcall(state, 3, 2, 0) == LUA_OK);
  assert(lua_istable(state, -2) && lua_istable(state, -1));
  assert(lua_rawlen(state, -2) == 2 && lua_rawlen(state, -1) == 2);
  lua_rawgeti(state, -1, 1);
  assert(lua_isboolean(state, -1) && !lua_toboolean(state, -1));
  lua_settop(state, 0);

  // Lua must expose the configured composed reading prior, not silently
  // leave the native default in use while direct C probes apply 1.3.
  push_method(state, "set_composed_reading_prior_weight");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 1.3);
  assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
  assert(lua_toboolean(state, -1));
  lua_settop(state, 0);
  push_method(state, "set_composed_reading_prior_weight");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 5.0);
  expect_lua_error(state, "set_composed_reading_prior_weight", 2);
  expect_valid_decode(state, handle);

  for (const char* method : {"set_auxiliary_word_edges", "set_auxiliary_context_guard"}) {
    push_method(state, method);
    lua_pushinteger(state, handle);
    if (std::strcmp(method, "set_auxiliary_word_edges") == 0) lua_pushinteger(state, 1);
    else lua_pushnumber(state, 0.5);
    assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
    assert(lua_toboolean(state, -1));
    lua_settop(state, 0);
    push_method(state, method);
    lua_pushinteger(state, handle);
    lua_pushinteger(state, 2);
    expect_lua_error(state, method, 2);
    expect_valid_decode(state, handle);
  }

  // Every bad argument must leave the C++ binding usable.  A longjmp from
  // luaL_check* used to bypass the binding mutex's destructor here.
  push_method(state, "free");
  lua_pushliteral(state, "not an integer");
  expect_lua_error(state, "free", 1);
  expect_valid_status(state, handle);

  push_method(state, "decode");
  lua_pushliteral(state, "not an integer");
  lua_pushliteral(state, "a");
  lua_pushboolean(state, 0);
  expect_lua_error(state, "decode", 3);
  expect_valid_decode(state, handle);

  push_method(state, "create");
  lua_pushstring(state, model_path.c_str());
  lua_pushstring(state, lexicon_path.c_str());
  lua_pushliteral(state, "not an integer");
  expect_lua_error(state, "create", 3);
  expect_valid_status(state, handle);

  push_method(state, "status");
  lua_pushinteger(state, static_cast<lua_Integer>(1) << 40);
  expect_lua_error(state, "status", 1);
  expect_valid_status(state, handle);

  push_method(state, "decode");
  lua_pushinteger(state, static_cast<lua_Integer>(1) << 40);
  lua_pushliteral(state, "a");
  lua_pushboolean(state, 0);
  expect_lua_error(state, "decode", 3);
  expect_valid_decode(state, handle);

  push_method(state, "set_personal_lexicon");
  lua_pushliteral(state, "not an integer");
  lua_pushliteral(state, "ab\t个人\t2\n");
  expect_lua_error(state, "set_personal_lexicon", 2);
  expect_valid_status(state, handle);

  push_method(state, "set_personal_lexicon");
  lua_pushinteger(state, handle);
  lua_pushliteral(state, "ab\t个人\t2\n");
  assert(lua_pcall(state, 2, 0, 0) == LUA_OK);
  expect_valid_decode(state, handle);

  push_method(state, "adjust_personal");
  lua_pushinteger(state, handle);
  lua_pushliteral(state, "abcd");
  lua_pushliteral(state, "个人");
  assert(lua_pcall(state, 3, 1, 0) == LUA_OK);
  assert(lua_isinteger(state, -1) && lua_tointeger(state, -1) == 1);
  lua_settop(state, 0);
  expect_valid_decode(state, handle);

  push_method(state, "set_personal_lexicon");
  lua_pushinteger(state, static_cast<lua_Integer>(1) << 40);
  lua_pushliteral(state, "ab\t个人\t2\n");
  expect_lua_error(state, "set_personal_lexicon", 2);
  expect_valid_status(state, handle);

  push_method(state, "set_word_form_weight");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 6.5);
  assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
  assert(lua_toboolean(state, -1));
  lua_settop(state, 0);
  push_method(state, "set_word_form_weight");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 16.5);
  expect_lua_error(state, "set_word_form_weight", 2);
  expect_valid_decode(state, handle);

  push_method(state, "set_user_model_gain_cap");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 6.0);
  assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
  assert(lua_toboolean(state, -1));
  lua_settop(state, 0);
  push_method(state, "set_user_model_gain_cap");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 32.5);
  expect_lua_error(state, "set_user_model_gain_cap", 2);
  expect_valid_decode(state, handle);

  push_method(state, "set_personal_edge_internal_cap");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 1.5);
  assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
  assert(lua_toboolean(state, -1));
  lua_settop(state, 0);
  push_method(state, "set_personal_edge_internal_cap");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 12.5);
  expect_lua_error(state, "set_personal_edge_internal_cap", 2);
  expect_valid_decode(state, handle);

  push_method(state, "set_bos_user_gain_cap");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 1.5);
  assert(lua_pcall(state, 2, 1, 0) == LUA_OK);
  assert(lua_toboolean(state, -1));
  lua_settop(state, 0);
  push_method(state, "set_bos_user_gain_cap");
  lua_pushinteger(state, handle);
  lua_pushnumber(state, 32.5);
  expect_lua_error(state, "set_bos_user_gain_cap", 2);
  expect_valid_decode(state, handle);

  push_method(state, "free");
  lua_pushinteger(state, handle);
  assert(lua_pcall(state, 1, 0, 0) == LUA_OK);
  lua_close(state);
  alarm(0);
  std::puts("tigerengine Lua safety tests passed");
  return 0;
}
