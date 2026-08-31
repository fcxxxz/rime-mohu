-- The native sentence translator only yields candidates; early commit is
-- intentionally absent from the runtime.
package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

local original_loadlib = package.loadlib

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

local context = {
  input = "",
  properties = {},
  options = {},
  commit_notifier = notifier(),
  update_notifier = notifier(),
}
function context:get_option(name) return self.options[name] or false end
function context:get_property(name) return self.properties[name] end
function context:set_property(name, value) self.properties[name] = value end

local config = {
  get_string = function(_, key)
    if key == "tiger/initial_quality" then return "50" end
    return nil
  end,
  get_int = function() return nil end,
}
local env = {
  engine = {
    context = context,
    schema = { config = config },
  },
}

package.preload["mohu_tiger_reranker"] = function()
  return {
    init = function() end,
    fini = function() end,
  }
end

package.loadlib = function()
  return function()
    return {
      create = function() return 1 end,
      free = function() end,
    }
  end
end

local native = dofile("tiger_sentence_native/mohu_tiger_sentence.lua")
assert(native.processor == nil,
  "native sentence module must not expose the removed processor")
native.translator.init(env)

assert(#context.commit_notifier.connections == 0,
  "sentence translator must not subscribe to commit notifications")
assert(#context.update_notifier.connections == 0,
  "sentence translator must not subscribe to update notifications")

native.translator.fini(env)
package.loadlib = original_loadlib
print("Mohu native no-early-commit test passed")
