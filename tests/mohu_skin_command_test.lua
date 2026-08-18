package.path = "./lua/?.lua;" .. package.path

local opened = 0
package.loaded.rime_skin_editor = {
  open_from_command = function(env)
    opened = opened + 1
    env.engine.context:clear()
  end,
}

local command = require("mohu_skin_command")

local function context(input)
  return {
    input = input,
    cleared = false,
    clear = function(self)
      self.cleared = true
      self.input = ""
    end,
  }
end

for _, input in ipairs({ "/skin", "/pifu", "/pfbj" }) do
  local ctx = context(input)
  local result = command.func({ release = function() return false end }, { engine = { context = ctx } })
  assert(result == 1)
  assert(ctx.cleared)
end

for _, input in ipairs({ "\\skin", "/djs", "/dcck", "/baidu", "/skins" }) do
  local ctx = context(input)
  local result = command.func({ release = function() return false end }, { engine = { context = ctx } })
  assert(result == 2)
  assert(not ctx.cleared)
end

assert(opened == 3)
print("skin command tests passed")
