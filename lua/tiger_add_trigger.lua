local M = {}

local BACKSLASH = "\\"
local SUFFIX = "\\\\"

local function is_normal_code(code)
  return type(code) == "string" and code:match("^[a-z;']+$") ~= nil
end

function M.can_append(input)
  if is_normal_code(input) then
    return true
  end
  return type(input) == "string"
    and input:sub(-1) == BACKSLASH
    and is_normal_code(input:sub(1, -2))
end

function M.target_code(input)
  if type(input) ~= "string" or input:sub(-#SUFFIX) ~= SUFFIX then
    return nil
  end
  local code = input:sub(1, -#SUFFIX - 1)
  return is_normal_code(code) and code or nil
end

return M
