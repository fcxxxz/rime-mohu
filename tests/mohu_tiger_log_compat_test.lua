package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

-- Tiger weasel builds register the global `log` as a plain function instead of
-- the librime-lua table.  The sentence translator must tolerate both shapes
-- and must preload lua54.dll next to the Windows engine DLL, because the OS
-- loader never searches the engine DLL's own directory for dependencies.

local original_config = package.config
local original_loadlib = package.loadlib
local function_log_calls = {}

rime_api = {
  get_user_data_dir = function() return "/tmp/mohu-tiger-log-compat-test" end,
}

local function make_env()
  return {
    engine = {
      context = {
        input = "ufqyhfmimh",
        properties = {},
        options = {},
      },
      schema = {
        config = {
          get_string = function() return nil end,
          get_int = function() return nil end,
        },
      },
    },
  }
end

local segment = {
  start = 0,
  _end = 6,
  has_tag = function(_, tag) return tag == "abc" end,
}

local yielded = {}
yield = function(candidate) yielded[#yielded + 1] = candidate end
Candidate = function(kind, start_pos, end_pos, text, comment)
  return { type = kind, start = start_pos, _end = end_pos, text = text, comment = comment }
end

local function fresh_translator(loadlib_stub)
  local calls = {}
  package.loadlib = function(path, symbol)
    calls[#calls + 1] = path .. "|" .. tostring(symbol)
    return loadlib_stub(path, symbol)
  end
  package.preload["mohu_tiger_reranker"] = function()
    return {
      init = function() end,
      fini = function() end,
      rerank = function() return nil end,
    }
  end
  return dofile("tiger_sentence_native/mohu_tiger_sentence.lua"), calls
end

-- 1. Function-shaped `log` plus a failing loadlib must not abort component
--    initialization; the failure must reach the function-shaped logger.
do
  log = function(message)
    function_log_calls[#function_log_calls + 1] = message
  end
  package.config = "\\" .. original_config:sub(2)
  local native, calls = fresh_translator(function()
    return nil, "The specified module could not be found."
  end)
  local env = make_env()
  native.translator.init(env)
  assert(#function_log_calls >= 1,
    "a loadlib failure must be reported even when log is a function")
  assert(function_log_calls[1]:find("loadlib", 1, true),
    "the first logged message must describe the loadlib failure")
  assert(#calls == 3,
    "on Windows lua54.dll and libwinpthread-1.dll must be preloaded before the engine DLL")
  assert(calls[1]:find("mohu_llm/runtime/lua54%.dll|%*", 1) ~= nil,
    "the first loadlib call must preload runtime/lua54.dll with '*'")
  assert(calls[2]:find("mohu_llm/runtime/libwinpthread%-1%.dll|%*", 1) ~= nil,
    "the second loadlib call must preload runtime/libwinpthread-1.dll with '*'")
  yielded = {}
  native.translator.func("ufqyhfmimh", segment, env)
  assert(#yielded == 0, "a failed engine must not yield sentence candidates")
end

-- 2. Table-shaped `log` keeps working and a successful loadlib stays silent.
do
  local errors = {}
  log = { error = function(message) errors[#errors + 1] = message end }
  local native, calls = fresh_translator(function()
    return function()
      return {
        create = function() return 7 end,
        free = function() end,
        decode = function() return "0 0 0 0 0 0\n", 0 end,
      }
    end
  end)
  local env = make_env()
  native.translator.init(env)
  assert(#errors == 0, "a healthy engine must not log errors")
  assert(#calls == 3, "the engine DLL must be loaded once after the preloads")
  assert(calls[3]:find("libtigerengine%.dll|luaopen_tigerengine", 1) ~= nil,
    "the final loadlib call must open the engine module")
  yielded = {}
  native.translator.func("ufqyhfmimh", segment, env)
  assert(#yielded == 0, "an empty decode result must not yield candidates")
end

-- 3. A missing `log` global must never abort the translator.
do
  log = nil
  local native, calls = fresh_translator(function()
    return nil, "The specified module could not be found."
  end)
  local env = make_env()
  native.translator.init(env)
  assert(#calls == 3, "load attempts must still happen without a logger")
end

-- 4. Non-Windows engine libraries must not trigger the lua54.dll preload.
do
  log = { error = function() end }
  package.config = "/" .. original_config:sub(2)
  local native, calls = fresh_translator(function()
    return nil, "engine unavailable"
  end)
  local env = make_env()
  native.translator.init(env)
  assert(#calls == 1, "dylib deployments must load the engine directly")
  assert(calls[1]:find("lua54%.dll") == nil,
    "the lua54.dll preload is Windows-only")
end

-- 5. A corrupted first decode (lua runtime mismatch between the bundled
--    lua54.dll and the weasel's embedded Lua) must disable the engine once
--    instead of yielding garbage on every keystroke.
do
  log = { error = function() end }
  local decode_calls = 0
  local native = fresh_translator(function()
    return function()
      return {
        create = function() return 9 end,
        free = function() end,
        decode = function()
          decode_calls = decode_calls + 1
          return "\1corrupted", 0
        end,
      }
    end
  end)
  local env = make_env()
  native.translator.init(env)
  assert(decode_calls == 1,
    "the runtime canary must decode exactly once during init")
  yielded = {}
  native.translator.func("ufqyhfmimh", segment, env)
  assert(decode_calls == 1,
    "a mismatched engine must not decode again after the canary rejects it")
  assert(#yielded == 0,
    "a mismatched engine must not yield sentence candidates")
end

package.loadlib = original_loadlib
package.config = original_config
log = nil
print("Mohu log compatibility tests passed")
