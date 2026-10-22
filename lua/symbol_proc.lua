--- by 晴
--[[
候选唯一时标点符号顶字；次选键可跳过 emoji 和符号联想

放在 key_binder 前面：

engine:
  processors:
    - ascii_composer
    - recognizer
    - lua_processor@*space_proc3
    - lua_processor@*symbol_proc #候选唯一时符号顶字
    - key_binder

有第二候选时不处理，继续交给 key_binder 的符号选重。
单引号是三选键，只有出现第三候选时才不处理。
唯一候选上屏后放行当前符号，让后续 speller、punctuator 等继续触发快符、反查或标点。
]]

local symbol_proc = {}

local kAccepted = 1
local kNoop = 2
local kSmartScanChunkSize = 32
local kSmartScanLimit = 512

local function configured_enabled(env)
  local schema = env and env.engine and env.engine.schema
  local config = schema and schema.config
  if not config or not config.get_bool then
    return true
  end

  local ok, value = pcall(function()
    return config:get_bool("smart_candidate_selection/enabled")
  end)
  return not ok or type(value) ~= "boolean" or value
end

local function is_han(codepoint)
  return (codepoint >= 0x4e00 and codepoint <= 0x9fff)
      or (codepoint >= 0x3400 and codepoint <= 0x4dbf)
      or (codepoint >= 0x20000 and codepoint <= 0x2a6df)
      or (codepoint >= 0x2a700 and codepoint <= 0x2b73f)
      or (codepoint >= 0x2b740 and codepoint <= 0x2b81f)
      or (codepoint >= 0x2b820 and codepoint <= 0x2ceaf)
      or (codepoint >= 0x2ceb0 and codepoint <= 0x2ebe0)
      or (codepoint >= 0x30000 and codepoint <= 0x3134a)
      or (codepoint >= 0x31350 and codepoint <= 0x323af)
      or (codepoint >= 0x2ebf0 and codepoint <= 0x2ee5f)
      or (codepoint >= 0x323b0 and codepoint <= 0x3347f)
end

local function is_text_candidate(cand)
  local text = cand and cand.text
  if not text or text == "" then
    return false
  end

  local ok, codepoint = pcall(utf8.codepoint, text, 1)
  if not ok or not codepoint then
    return false
  end
  return is_han(codepoint)
      or (codepoint >= string.byte("a") and codepoint <= string.byte("z"))
      or (codepoint >= string.byte("A") and codepoint <= string.byte("Z"))
      or (codepoint >= string.byte("0") and codepoint <= string.byte("9") and cand.type ~= "simplified")
end

local function smart_choice_rank(key_event)
  if key_event:shift() then
    return nil
  end
  local repr = key_event:repr()
  if repr == "semicolon" or key_event.keycode == string.byte(";") then
    return 2
  end
  if repr == "apostrophe" or key_event.keycode == string.byte("'") then
    return 3
  end
  return nil
end

local function is_explicit_symbol_input(input)
  local first = tostring(input or ""):sub(1, 1)
  return first == "\\" or first == ";"
end

local function is_symbol_key(key_event)
  local keycode = key_event.keycode
  if key_event:repr() == "space" or keycode < 0x21 or keycode > 0x7e then
    return false
  end

  local ch = string.char(keycode)
  return rime_api.regex_match(ch, "[^0-9A-Za-z\\s]")
end

local function first_candidate(seg)
  if not seg or not seg.menu then
    return nil
  end
  seg.menu:prepare(1)
  return seg.menu:get_candidate_at(0)
end

local function second_candidate(seg)
  if not seg or not seg.menu then
    return nil
  end
  seg.menu:prepare(2)
  return seg.menu:get_candidate_at(1)
end

local function third_candidate(seg)
  if not seg or not seg.menu then
    return nil
  end
  seg.menu:prepare(3)
  return seg.menu:get_candidate_at(2)
end

local function smart_select(key_event, env, context, seg)
  if not env.smart_candidate_selection_enabled then
    return nil, false
  end

  local rank = smart_choice_rank(key_event)
  local page_size = env.engine.schema.page_size
  if not rank or is_explicit_symbol_input(context.input)
      or (seg.selected_index or 0) >= page_size then
    return nil, false
  end

  local text_count = 0
  local first_text_index = nil
  local scan_from = 0
  local target = math.min(math.max(page_size, 1), kSmartScanLimit)
  while target > 0 do
    seg.menu:prepare(target)
    local exhausted = false
    for index = scan_from, target - 1 do
      local cand = seg.menu:get_candidate_at(index)
      if not cand then
        exhausted = true
        break
      end
      if is_text_candidate(cand) then
        text_count = text_count + 1
        first_text_index = first_text_index or index
        if text_count == rank then
          context:select(index)
          return kAccepted, true
        end
      end
    end
    if exhausted or target >= kSmartScanLimit then
      break
    end
    scan_from = target
    target = math.min(target + kSmartScanChunkSize, kSmartScanLimit)
  end

  if first_text_index ~= nil then
    context:select(first_text_index)
    context:confirm_current_selection()
    return kNoop, true
  end
  return nil, false
end

function symbol_proc.init(env)
  env.smart_candidate_selection_enabled = configured_enabled(env)
end

function symbol_proc.func(key_event, env)
  if key_event:release() or key_event:alt() or key_event:ctrl()
      or key_event:caps() then
    return kNoop
  end

  if not is_symbol_key(key_event) then
    return kNoop
  end

  local context = env.engine.context
  if not context:has_menu() then
    return kNoop
  end

  local seg = context.composition:back()
  local first = first_candidate(seg)
  if not first or first.text == context.input then
    return kNoop
  end

  local smart_result, smart_handled = smart_select(key_event, env, context, seg)
  if smart_handled then
    return smart_result
  end

  if key_event:repr() == "apostrophe" or key_event.keycode == string.byte("'") then
    if third_candidate(seg) then
      return kNoop
    end
  else
    if second_candidate(seg) then
      return kNoop
    end
  end

  context:confirm_current_selection()
  return kNoop -- 放行当前符号，让后续处理器继续触发快符、反查或标点
end

return symbol_proc
