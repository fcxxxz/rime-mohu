package.path = "lua/?.lua;" .. package.path
local subject = require("mohu_personal_lexicon")._test

assert(subject.pure_double_pinyin("qygfda") == "qygfda")
assert(subject.pure_double_pinyin("qy;xx gf;yy da;zz") == "qygfda")
assert(subject.pure_double_pinyin("bad") == nil)
assert(subject.valid_utf8("晴跟打"))
assert(not subject.valid_utf8("bad\nword"))

local memory = {
  user_entries = {
    { text = "晴跟打", custom_code = "qy gf da", commit_count = 8 },
    { text = "比亚迪", custom_code = "bi ya di", commit_count = 3 },
    { text = "内置", custom_code = "ne iz", commit_count = 99 },
    { text = "零频", custom_code = "lg py", commit_count = 0 },
  },
  builtins = { ["neiz\t内置"] = true },
}
function memory:user_lookup() return true end
function memory:iter_user()
  local i = 0
  return function()
    i = i + 1
    return self.user_entries[i]
  end
end
function memory:dictiter_lookup(code)
  local key = code .. "\t内置"
  return { iter = function()
    local done = false
    return function()
      if done or not self.builtins[key] then return nil end
      done = true
      return { text = "内置" }
    end
  end }
end
local rows = require("mohu_personal_lexicon").collect(memory)
assert(#rows == 2, #rows)
assert(rows[1].text == "晴跟打")
assert(rows[2].text == "比亚迪")
local payload, count = require("mohu_personal_lexicon").serialize(rows)
assert(count == 2)
local large_rows = {}
for index = 1, 6000 do
  large_rows[index] = { code = "abcd", text = string.rep("甲", 64), commits = index }
end
local large_payload, large_count = require("mohu_personal_lexicon").serialize(large_rows)
assert(large_count == 6000)
assert(#large_payload > 1024 * 1024)

-- 快照序列化必须确定性排序：同一 userdb 两次采集产生完全相同的负载，
-- 这是 translator 层“无增长不刷新”用负载相等做判定的前提。
local module = require("mohu_personal_lexicon")
local payload_a = module.serialize(module.collect(memory))
local payload_b = module.serialize(module.collect(memory))
assert(payload_a == payload_b, "snapshot payload must be deterministic")

-- 分片扫描：行集合与整体路径一致（顺序不同：整体按提交次数排序，
-- 分片按库键序）。
local scan_state, scan_status = module.scan_begin(memory)
assert(scan_state ~= nil, scan_status)
local scan_done = false
while not scan_done do
  scan_done = module.scan_step(scan_state, 1.0)
end
local sliced_payload, sliced_count = module.scan_finish(scan_state)
assert(sliced_count == 2, sliced_count)
local function sorted_lines(text)
  local lines = {}
  for line in text:gmatch("[^\n]+") do lines[#lines + 1] = line end
  table.sort(lines)
  return lines
end
local sliced_lines = sorted_lines(sliced_payload)
local whole_lines = sorted_lines(payload_a)
assert(#sliced_lines == #whole_lines, #sliced_lines)
for index = 1, #sliced_lines do
  assert(sliced_lines[index] == whole_lines[index])
end

-- 分片扫描自身可复现：两次完整分片产出字节相同的负载。
local repeat_state = module.scan_begin(memory)
while not module.scan_step(repeat_state, 1.0) do end
assert(module.scan_finish(repeat_state) == sliced_payload,
  "sliced scan must be deterministic")

-- 条目数超过单片硬上限时必须切成多片完成。
local big_entries = {}
for index = 1, 1200 do
  big_entries[index] = {
    text = "多片验证" .. tostring(index),
    custom_code = "aa;xy bb;xy cc;xy",
    commit_count = (index % 9) + 1,
  }
end
local big_memory = { user_entries = big_entries }
function big_memory:user_lookup() return true end
function big_memory:iter_user()
  local i = 0
  return function()
    i = i + 1
    return self.user_entries[i]
  end
end
function big_memory:dictiter_lookup() return { iter = function() return function() return nil end end } end
local big_state = module.scan_begin(big_memory)
assert(big_state ~= nil)
local big_slices = 0
while not module.scan_step(big_state, 0) do
  big_slices = big_slices + 1
  assert(big_slices < 50, "scan did not finish")
end
assert(big_slices >= 2, big_slices)
local big_payload, big_count = module.scan_finish(big_state)
assert(big_count == 1200, big_count)
assert(#sorted_lines(big_payload) == 1200)

-- 空库：scan_begin 返回 nil,"empty"，调用方据此应用空负载重置 native。
local empty_memory = {}
function empty_memory:user_lookup() return false end
function empty_memory:iter_user() return function() return nil end end
local empty_state, empty_status = module.scan_begin(empty_memory)
assert(empty_state == nil and empty_status == "empty", empty_status)

print("personal lexicon tests passed")
