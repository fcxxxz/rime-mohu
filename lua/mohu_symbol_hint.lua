local hints = {
  { code = "bd", label = "标点符号" },
  { code = "bq", label = "表情" },
  { code = "pi", label = "π" },
  { code = "pai", label = "π" },
  { code = "fh", label = "符号" },
  { code = "jt", label = "箭头" },
  { code = "sx", label = "数学" },
  { code = "dw", label = "单位" },
  { code = "rqfh", label = "日期符号" },
  { code = "sjfh", label = "时间符号" },
  { code = "xqfh", label = "象棋符号" },
  { code = "jqfh", label = "节气符号" },
  { code = "sz", label = "色子" },
  { code = "py", label = "拼音" },
  { code = "zy", label = "注音" },
  { code = "tq", label = "天气" },
  { code = "yy", label = "音乐" },
  { code = "hb", label = "货币" },
  { code = "kh", label = "括号" },
  { code = "date", label = "日期" },
  { code = "rq", label = "日期" },
  { code = "cdate", label = "农历" },
  { code = "nl", label = "农历" },
  { code = "time", label = "时间" },
  { code = "sj", label = "时间" },
  { code = "week", label = "星期" },
  { code = "xq", label = "星期" },
  { code = "fjq", label = "节气" },
  { code = "jq", label = "节气" },
  { code = "gl", label = "候选管理" },
  { code = "skin", label = "皮肤编辑" },
  { code = "pifu", label = "皮肤编辑" },
  { code = "pfbj", label = "皮肤编辑" },
}

local function starts_with(text, prefix)
  return text:sub(1, #prefix) == prefix
end

local function translator(input, seg)
  if input:sub(1, 1) ~= "\\" then
    return
  end

  local prefix = input:sub(2)
  local count = 0
  for _, item in ipairs(hints) do
    if prefix ~= item.code and (prefix == "" or starts_with(item.code, prefix)) then
      count = count + 1
      local text = "\\" .. item.code .. " " .. item.label
      local candidate = Candidate("symbol_hint", seg.start, seg._end, text, "继续输入 " .. item.code)
      candidate.quality = -1000 - count
      yield(candidate)
    end
  end
end

return translator
