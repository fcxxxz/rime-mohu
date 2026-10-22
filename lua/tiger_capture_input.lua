local M = {}

local keypad_chars = {
  [0xffaa] = "*",
  [0xffab] = "+",
  [0xffac] = ",",
  [0xffad] = "-",
  [0xffae] = ".",
  [0xffaf] = "/",
  [0xffbd] = "=",
}

local chinese_half_shape_punctuation = {
  [","] = "，",
  ["."] = "。",
  ["<"] = "《",
  [">"] = "》",
  ["/"] = "、",
  ["?"] = "？",
  ["!"] = "！",
  [":"] = "：",
  ["\\"] = "、",
  ["|"] = "·",
  ["$"] = "￥",
  ["^"] = "……",
  ["("] = "（",
  [")"] = "）",
  ["_"] = "——",
  ["["] = "「",
  ["]"] = "」",
  ["{"] = "『",
  ["}"] = "』",
}

local chinese_full_shape_punctuation = {
  [" "] = "　",
  [","] = "，",
  ["."] = "。",
  ["<"] = "《",
  [">"] = "》",
  ["/"] = "／",
  ["?"] = "？",
  ["!"] = "！",
  [":"] = "：",
  ["\\"] = "、",
  ["|"] = "·",
  ["`"] = "｀",
  ["~"] = "～",
  ["@"] = "＠",
  ["#"] = "＃",
  ["%"] = "％",
  ["$"] = "￥",
  ["^"] = "……",
  ["&"] = "＆",
  ["*"] = "＊",
  ["("] = "（",
  [")"] = "）",
  ["-"] = "－",
  ["_"] = "——",
  ["+"] = "＋",
  ["["] = "「",
  ["]"] = "」",
  ["{"] = "『",
  ["}"] = "』",
}

local punctuation_pairs = {
  ["'"] = { "‘", "’" },
  ['"'] = { "“", "”" },
}

function M.keycode_to_char(keycode)
  if keycode >= 0x20 and keycode <= 0x7e then
    return string.char(keycode)
  end
  if keycode >= 0xffb0 and keycode <= 0xffb9 then
    return string.char(0x30 + keycode - 0xffb0)
  end
  return keypad_chars[keycode]
end

function M.is_printable_keysym(keycode)
  return (keycode >= 0x20 and keycode <= 0x7e)
    or (keycode >= 0xa0 and keycode <= 0xfdff)
    or (keycode >= 0x01000100 and keycode <= 0x0110ffff)
end

function M.is_modifier_key(keycode)
  return keycode >= 0xffe1 and keycode <= 0xffee
end

function M.is_shift_key(keycode)
  return keycode == 0xffe1 or keycode == 0xffe2
end

local function is_selection_char(char, select_keys)
  if char == " " or char == ";" or char == "'" then
    return true
  end
  return select_keys:find(char, 1, true) ~= nil
end

function M.classify(keycode, ascii_mode, has_query, select_keys, shifted)
  local char = M.keycode_to_char(keycode)
  if char == nil then
    if M.is_printable_keysym(keycode) then
      return "consume", nil
    end
    return nil, nil
  end

  if shifted and char:match("^[a-z]$") then
    char = char:upper()
  end

  if ascii_mode then
    return "literal", char
  end
  if has_query then
    if is_selection_char(char, select_keys) then
      return "select", char
    end
    if char:match("^[a-z]$") then
      return "query", char
    end
    return "consume", char
  end
  if char:match("^[a-z]$") then
    return "query", char
  end
  return "literal", char
end

function M.selection_index(selected_index, keycode, page_size, select_keys)
  local page_start = math.floor(selected_index / page_size) * page_size
  local char = M.keycode_to_char(keycode)
  local position
  if char == ";" then
    position = 2
  elseif char == "'" then
    position = 3
  elseif char ~= nil then
    position = select_keys:find(char, 1, true)
  end
  if position == nil or position > page_size then
    return nil
  end
  return page_start + position - 1
end

local function count_plain(text, needle)
  local count = 0
  local start = 1
  while true do
    local found = text:find(needle, start, true)
    if found == nil then
      return count
    end
    count = count + 1
    start = found + #needle
  end
end

function M.literal_char(existing_text, char, ascii_mode, ascii_punct, full_shape)
  if ascii_mode or ascii_punct then
    return char
  end
  local pair = punctuation_pairs[char]
  if pair ~= nil then
    local pair_count = count_plain(existing_text, pair[1]) + count_plain(existing_text, pair[2])
    return pair[pair_count % 2 + 1]
  end
  local punctuation = full_shape and chinese_full_shape_punctuation or chinese_half_shape_punctuation
  return punctuation[char] or char
end

return M
