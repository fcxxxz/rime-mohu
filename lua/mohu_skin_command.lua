local M = {}
local kAccepted = 1
local kNoop = 2

local commands = {
  ["\\skin"] = true,
  ["\\pifu"] = true,
  ["\\pfbj"] = true,
}

function M.func(key_event, env)
  if key_event and key_event.release and key_event:release() then
    return kNoop
  end

  local context = env and env.engine and env.engine.context
  if not context or not commands[context.input] then
    return kNoop
  end

  require("rime_skin_editor").open_from_command(env)
  return kAccepted
end

return M
