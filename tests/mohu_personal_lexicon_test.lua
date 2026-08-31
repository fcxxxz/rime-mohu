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

print("personal lexicon tests passed")
