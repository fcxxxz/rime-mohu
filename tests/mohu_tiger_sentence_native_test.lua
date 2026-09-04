package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

local original_loadlib = package.loadlib
local yielded = {}
local create_calls = 0
local free_calls = 0
local decode_output

rime_api = {
  get_user_data_dir = function() return "/tmp/mohu-tiger-native-test" end,
}
log = { error = function() end }

local function notifier()
  return {
    connections = {},
    connect = function(self, callback)
      local connection = { callback = callback, disconnected = false }
      function connection:disconnect() self.disconnected = true end
      self.connections[#self.connections + 1] = connection
      return connection
    end,
  }
end

local function context()
  local value = {
    input = "",
    properties = {},
    options = {},
    commit_notifier = notifier(),
    update_notifier = notifier(),
  }
  function value:get_property(name) return self.properties[name] end
  function value:set_property(name, state) self.properties[name] = state end
  function value:get_option(name) return self.options[name] or false end
  function value:set_option(name, state) self.options[name] = state end
  function value:is_composing() return self.input ~= "" end
  function value:has_menu() return self.input ~= "" end
  return value
end

local ctx = context()
local commit_calls = 0
function ctx:commit_text(_) commit_calls = commit_calls + 1 end
local engine = {
  context = ctx,
  schema = {
    config = {
      get_string = function(_, key)
        if key == "tiger/initial_quality" then return "50" end
        return nil
      end,
      get_int = function() return nil end,
    },
  },
}
local env = { engine = engine }

package.preload["mohu_tiger_reranker"] = function()
  return {
    init = function() end,
    fini = function() end,
    rerank = function() return nil end,
  }
end

Candidate = function(kind, start_pos, end_pos, text, comment)
  return {
    type = kind,
    start = start_pos,
    _end = end_pos,
    text = text,
    comment = comment,
  }
end
yield = function(candidate) yielded[#yielded + 1] = candidate end

decode_output = table.concat({
  "0 0 0 0 2 0",
  "原生句\tab cd ef\t0\t0\t1\t3:2,6:4",
  "原生候选\tab cd ef\t-1\t-1\t1\t3:2,6:4",
  "",
}, "\n")
package.loadlib = function()
  return function()
    return {
      create = function()
        create_calls = create_calls + 1
        return 7
      end,
      free = function(handle)
        assert(handle == 7)
        free_calls = free_calls + 1
      end,
      decode = function() return decode_output, 0.2 end,
    }
  end
end

local native = dofile("tiger_sentence_native/mohu_tiger_sentence.lua")
native.translator.init(env)
assert(create_calls == 1, "translator must initialize one native engine")
assert(#ctx.commit_notifier.connections == 0,
  "removing early commit must remove commit hooks")
assert(#ctx.update_notifier.connections == 0,
  "removing early commit must remove update hooks")

local segment = {
  start = 0,
  _end = 6,
  has_tag = function(_, tag) return tag == "abc" end,
}
ctx.input = "abcdef"
local input_before_translation = ctx.input
native.translator.func("abcdef", segment, env)
assert(#yielded == 2, "native translator must yield complete sentence candidates")
assert(commit_calls == 0, "native translator must never commit text")
assert(ctx.input == input_before_translation,
  "native translator must not rewrite Context.input")
assert(yielded[1].type == "mohu_zrm",
  "native candidates must retain their independent identity")
assert(yielded[1].quality == 50, "native candidates must use configured quality")
assert(yielded[1].preedit == "ab cd ef",
  "native candidates must preserve the segmented preedit")

yielded = {}
ctx.input = "abcd"
native.translator.func("abcd", segment, env)
assert(#yielded == 0,
  "a single two-syllable word must remain under the normal smart translator")

yielded = {}
local punct = {
  start = 0,
  _end = 6,
  has_tag = function() return false end,
}
native.translator.func("abcdef", punct, env)
assert(#yielded == 0, "non-abc segments must be ignored")

-- A malformed native response must fail open without constructing candidates.
decode_output = table.concat({
  "0 0 0 0 1 0",
  "坏响应\tab cd ef\tnot-a-number\t0\t1\t",
  "",
}, "\n")
yielded = {}
native.translator.func("abcdef", segment, env)
assert(#yielded == 0, "malformed native scores must fail open")

native.translator.fini(env)
assert(free_calls == 1, "shared native engine must be freed once")
package.loadlib = original_loadlib
print("Mohu native sentence integration tests passed")
