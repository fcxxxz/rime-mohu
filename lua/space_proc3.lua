--- by 荒
--[[
空码标顶清屏

将 space_proc3.lua 文件复制到个人文件夹的 lua 目录下。
然后找到 tiger.schema.yaml，在 engine/processors 下 recognizer 一行的下方加入：

  - lua_processor@*space_proc3 #标顶空码不上屏

顺序如下：

engine:
  processors:
    - ascii_composer
    - recognizer
    - lua_processor@*space_proc3
    - key_binder
]]
local space_proc = {}

local kRejected = 0 -- 拒: 不作響應, 由操作系統做默認處理
local kAccepted = 1 -- 收: 由rime響應該按鍵
local kNoop     = 2 -- 無: 請下一個processor繼續看

local nametosymbol = {}

--- 取出输入中当前正在翻译的一部分
---@param context Context
local function current(context)
  local segment = context.composition:toSegmentation():back()
  if not segment then
    return nil
  end
  return context.input:sub(segment.start + 1, segment._end)
end

---@param ch string
local function is_punct_char(ch)
  if not ch or utf8.len(ch) ~= 1 then
    return false
  end
  -- 将所有非字母数字与空白字符视为标点
  return rime_api.regex_match(ch, "[^0-9A-Za-z\\s]")
end

--- 检查字符串中是否包含大写字母（任意位置）
---@param str string
local function has_uppercase(str)
  if not str then return false end
  -- 检查整个字符串中任意位置的大写字母
  return string.match(str, "%u") ~= nil
end

--- 检查是否以反斜杠开头
---@param str string
local function starts_with_backslash(str)
  return str and string.sub(str, 1, 1) == "\\"
end

---@class SpaceEnv: Env
---@field hasecho boolean

---@param env SpaceEnv
function space_proc.init(env)
  env.hasecho = false
  local translators = env.engine.schema.config:get_list("engine/translators");
  if not translators then return end
  for i = 0, translators.size do
    local translator = translators:get_at(i)
    if not translator then
      goto continue
    end
    if translator:get_value():get_string() == "echo_translator" then
      env.hasecho = true
    end
    ::continue::
  end
end

---@param key_event KeyEvent
---@param env SpaceEnv
function space_proc.func(key_event, env)
  local context = env.engine.context
  -- 忽略修饰/释放键
  if key_event:release() or key_event:alt() or key_event:ctrl() or key_event:caps() then
    return kNoop
  end

  local seg = context.composition:back()
  if not seg then
    return kNoop
  end

  -- 当前输入（preedit 内容对应的编码）
  local input = current(context)
  if not input or input == "" then
    return kNoop
  end

  -- 特殊处理：以反斜杠开头的符号输入不算空码
  if starts_with_backslash(input) then
    return kNoop
  end

  -- 数字后的 ./: 由 punctuator 暂存；空格或后续数字应交回 punctuator 提交。
  if seg.has_tag and seg:has_tag("punct_number") then
    return kNoop
  end

  -- 检查是否为空码状态（无候选词）
  local isEmptyCode = false
  if not context:has_menu() then
    isEmptyCode = true  -- 候选区无候选
  elseif env.hasecho then
    local first = seg:get_selected_candidate()
    isEmptyCode = first and first.text == input  -- 首选等于输入内容
  end

  if not isEmptyCode then
    return kNoop
  end

  local repr = key_event:repr()
  -- 跳过常见控制键（注意：空格键不再跳过）
  if repr == "BackSpace" or repr == "Escape" or repr == "Return" or repr == "Tab" then
    return kNoop
  end

  -- 处理空格键
  if repr == "space" then 
    -- 处理含有大写字母的编码
    if has_uppercase(input) then
      env.engine:commit_text(input)
    end
    -- 清空预编辑区
    context:clear()
    return kAccepted
  end

  -- 处理标点符号
  local incoming = utf8.char(key_event.keycode)
  if not incoming or utf8.len(incoming) ~= 1 or not is_punct_char(incoming) then
    return kNoop
  end

  -- 处理含有大写字母的编码
  if has_uppercase(input) then
    env.engine:commit_text(input .. incoming)
  end

  -- 完全清空预编辑区
  context:clear()
  return kAccepted
end

return space_proc
