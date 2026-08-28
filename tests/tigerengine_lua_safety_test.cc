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

  push_method(state, "create");
  lua_pushstring(state, model_path.c_str());
  lua_pushstring(state, lexicon_path.c_str());
  lua_pushinteger(state, static_cast<lua_Integer>(1) << 40);
  expect_lua_error(state, "create", 3);
  expect_valid_status(state, handle);

  push_method(state, "free");
  lua_pushinteger(state, handle);
  assert(lua_pcall(state, 1, 0, 0) == LUA_OK);
  lua_close(state);
  alarm(0);
  std::puts("tigerengine Lua safety tests passed");
  return 0;
}
