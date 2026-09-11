package.path = "./lua/?.lua;" .. package.path

-- 语义导出元数据 ABI 测试（lua/mohu_semantic_meta.lua）：
--   A) bind/resolve 基本契约：单次绑定、浅拷贝不可变、重复绑定拒绝；
--   B) get_genuine() 链解析：多层 ShadowCandidate 式包装、未绑定、
--      自引用与环防御、深度上限；
--   C) 最终菜单解析：每个最终槽位恰有一份 provenance，缺失/歧义/复用
--      或非连续菜单全单拒绝，重复显示文本仍保持独立；
--   D) 字节→码点跨度转换：ASCII、多字节、边界落在码点中间、
--      非法 UTF-8、越界与倒置；
--   E) exportable_raw_input：纯 ASCII 放行，非 ASCII / 非字符串拒绝。

local failures = 0
local function check(name, ok, detail)
  if ok then
    print("pass: " .. name)
  else
    print("fail: " .. name .. (detail and (" [" .. detail .. "]") or ""))
    failures = failures + 1
  end
end

local meta = require("mohu_semantic_meta")

-- ======================================================================
-- A) bind/resolve 基本契约
-- ======================================================================
do
  local producer_meta = {
    text = "拐角处",
    type = "mohu_zrm",
    score = -37.125,
    personal = false,
  }
  local cand = { type = "mohu_zrm", text = "拐角处" }
  local ok, err = meta.bind(cand, producer_meta)
  check("A1 bind accepts producer metadata", ok == true, tostring(err))

  local resolved = meta.resolve(cand)
  check("A2 resolve returns the bound record",
        type(resolved) == "table" and resolved.score == -37.125)

  producer_meta.score = 999
  check("A3 bind is a shallow copy immune to producer mutation",
        meta.resolve(cand).score == -37.125)

  local again, reason = meta.bind(cand, { score = 1 })
  check("A4 rebinding the same candidate is rejected",
        again == false and reason == "candidate already bound", tostring(reason))
  check("A5 rejected rebind does not alter the original record",
        meta.resolve(cand).score == -37.125)
end

do
  local not_candidate = "string"
  local ok1 = meta.bind(not_candidate, {})
  local ok2, reason2 = meta.bind({}, "not-a-table")
  check("A6 bind rejects non-candidate and non-table meta",
        ok1 == false and ok2 == false and reason2 == "meta must be a table")
end

-- ======================================================================
-- B) get_genuine() 链解析
-- ======================================================================
local function plain_candidate(fields)
  local value = {}
  for key, item in pairs(fields or {}) do value[key] = item end
  function value:get_genuine() return self end
  return value
end

local function shadow_of(inner)
  local wrapper = { type = "mohu_reordered" }
  function wrapper:get_genuine() return inner end
  return wrapper
end

do
  local genuine = plain_candidate({ text = "你好", score = -12.5 })
  local ok = meta.bind(genuine, { text = "你好", score = -12.5, origin = "native" })
  local resolved = meta.resolve(shadow_of(shadow_of(genuine)))
  check("B1 resolve pierces nested shadow wrappers",
        ok and resolved ~= nil and resolved.origin == "native" and
        resolved.score == -12.5)
end

do
  local unbound = plain_candidate({ text = "未绑定" })
  check("B2 resolve of an unbound terminal candidate returns nil",
        meta.resolve(shadow_of(unbound)) == nil)
  check("B3 resolve of a non-table returns nil",
        meta.resolve(42) == nil and meta.resolve(nil) == nil and
        meta.resolve("text") == nil)
end

do
  local looping = {}
  function looping:get_genuine() return looping end
  check("B4 self-referential genuine resolves to nil",
        meta.resolve(looping) == nil)

  local a, b = {}, {}
  function a:get_genuine() return b end
  function b:get_genuine() return a end
  check("B5 genuine cycles resolve to nil", meta.resolve(a) == nil)
end

do
  local terminal = plain_candidate({ text = "深层" })
  assert(meta.bind(terminal, { text = "深层", origin = "deep" }))
  local current = terminal
  for _ = 1, 8 do current = shadow_of(current) end
  local deep_ok = meta.resolve(current)
  local beyond = shadow_of(current)
  local beyond_result = meta.resolve(beyond)
  check("B6 resolve reaches a binding within the depth limit",
        deep_ok ~= nil and deep_ok.origin == "deep")
  check("B7 resolve beyond the depth limit returns nil",
        beyond_result == nil)
end

do
  -- 外层直接绑定的包装优先于内层绑定：解析由外向内。
  local inner = plain_candidate({ text = "内层" })
  assert(meta.bind(inner, { origin = "inner" }))
  local wrapper = shadow_of(inner)
  assert(meta.bind(wrapper, { origin = "wrapper" }))
  check("B8 outermost binding wins during resolution",
        meta.resolve(wrapper).origin == "wrapper")
end

do
  local broken = {}
  function broken:get_genuine() error("boom") end
  check("B9 a genuine getter that errors resolves to nil",
        meta.resolve(broken) == nil)
end

-- ======================================================================
-- C) 最终菜单 provenance 收集
-- ======================================================================
local function final_candidate(text, origin)
  local candidate = plain_candidate({ text = text, type = "mohu_zrm" })
  assert(meta.bind(candidate, { origin = origin }))
  return candidate
end

do
  local first = final_candidate("重复显示", "native-first")
  local second = final_candidate("重复显示", "native-second")
  local records, reason = meta.resolve_menu({ shadow_of(first), shadow_of(second) })
  check("C1 final menu keeps same-text candidates as distinct bindings",
        type(records) == "table" and reason == nil and #records == 2 and
        records[1].origin == "native-first" and records[2].origin == "native-second")
end

do
  local bound = final_candidate("已绑定", "native")
  local records, reason = meta.resolve_menu({ shadow_of(bound), plain_candidate({ text = "未绑定" }) })
  check("C2 one missing producer binding rejects the whole final menu",
        records == nil and reason == "candidate 2 has no provenance", tostring(reason))
end

do
  local inner = final_candidate("内层", "native")
  local outer = shadow_of(inner)
  assert(meta.bind(outer, { origin = "override" }))
  local records, reason = meta.resolve_menu({ outer })
  check("C3 multiple bindings in one genuine chain reject the final menu",
        records == nil and reason == "candidate 1 has ambiguous provenance", tostring(reason))
end

do
  local inner = final_candidate("复用", "native")
  local records, reason = meta.resolve_menu({ shadow_of(inner), shadow_of(inner) })
  check("C4 a provenance binding cannot identify two final menu slots",
        records == nil and reason == "candidate 2 reuses provenance", tostring(reason))
end

do
  local records, reason = meta.resolve_menu({ [1] = final_candidate("一", "one"), [3] = final_candidate("三", "three") })
  check("C5 sparse final menu arrays are rejected before observation",
        records == nil and reason == "menu must be a contiguous array", tostring(reason))
  local empty_records, empty_reason = meta.resolve_menu({})
  check("C6 empty final menus are rejected before observation",
        empty_records == nil and empty_reason == "menu must not be empty", tostring(empty_reason))
end

-- ======================================================================
-- D) 字节 → 码点跨度转换
-- ======================================================================
do
  local start, finish = meta.codepoint_span("niho", 0, 4)
  check("D1 ASCII full-input span converts exactly",
        start == 0 and finish == 4,
        string.format("%s,%s", tostring(start), tostring(finish)))
  local part_start, part_end = meta.codepoint_span("niho", 2, 4)
  check("D2 ASCII partial span converts exactly",
        part_start == 2 and part_end == 4)
  local empty_start, empty_end = meta.codepoint_span("niho", 2, 2)
  check("D3 zero-width ASCII span is valid",
        empty_start == 2 and empty_end == 2)
end

do
  -- "a你b"：字节 a=1，你=2..4（3 字节），b=5；码点 a=0，你=1，b=2。
  local raw = "a你b"
  local s1, e1 = meta.codepoint_span(raw, 1, 4)
  check("D4 multibyte span covering 你 converts to code points",
        s1 == 1 and e1 == 2,
        string.format("%s,%s", tostring(s1), tostring(e1)))
  local s2, e2 = meta.codepoint_span(raw, 0, 5)
  check("D5 full multibyte input converts to (0, 3)",
        s2 == 0 and e2 == 3)
  local s3, e3 = meta.codepoint_span(raw, 0, 1)
  check("D6 ASCII-leading partial span converts to (0, 1)",
        s3 == 0 and e3 == 1)
end

do
  local raw = "a你b"
  check("D7 start inside a multibyte sequence is rejected",
        select(1, meta.codepoint_span(raw, 2, 5)) == nil)
  check("D8 end inside a multibyte sequence is rejected",
        select(1, meta.codepoint_span(raw, 0, 3)) == nil)
  check("D9 end before start is rejected",
        select(1, meta.codepoint_span(raw, 3, 1)) == nil)
  check("D10 offsets beyond the raw length are rejected",
        select(1, meta.codepoint_span(raw, 0, 6)) == nil and
        select(1, meta.codepoint_span(raw, 6, 6)) == nil)
  check("D11 negative offsets are rejected",
        select(1, meta.codepoint_span(raw, -1, 2)) == nil)
  check("D12 non-string raw is rejected",
        select(1, meta.codepoint_span(123, 0, 1)) == nil)
  check("D13 fractional offsets are rejected",
        select(1, meta.codepoint_span(raw, 0.5, 1)) == nil)
end

do
  check("D14 invalid UTF-8 lead byte is rejected",
        select(1, meta.codepoint_span("\xFF\xFE", 0, 1)) == nil)
  check("D15 truncated UTF-8 sequence is rejected",
        select(1, meta.codepoint_span("你", 0, 2)) == nil)
  check("D16 stray continuation byte is rejected",
        select(1, meta.codepoint_span("a\x80b", 0, 1)) == nil)
  local empty_start, empty_end = meta.codepoint_span("", 0, 0)
  check("D17 empty raw span (0, 0) is valid",
        empty_start == 0 and empty_end == 0)
end

-- ======================================================================
-- D) exportable_raw_input
-- ======================================================================
do
  check("D1 pure ASCII raw input is exportable",
        meta.exportable_raw_input("niho") == true and
        meta.exportable_raw_input("gyjc'clve") == true and
        meta.exportable_raw_input("") == true)
  check("D2 non-ASCII raw input is rejected",
        meta.exportable_raw_input("n你") == false and
        meta.exportable_raw_input("你") == false)
  check("D3 non-string raw input is rejected",
        meta.exportable_raw_input(nil) == false and
        meta.exportable_raw_input(42) == false)
end

os.exit(failures == 0 and 0 or 1)
