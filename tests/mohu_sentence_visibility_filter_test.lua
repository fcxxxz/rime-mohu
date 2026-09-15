package.path = "./lua/?.lua;./tiger_sentence_native/?.lua;" .. package.path

-- 整句候选显示调整测试（lua/mohu_sentence_visibility_filter.lua）：
-- 默认前 1 条句形候选在前，其余句形候选押后到全部词组之后（不删除，
-- 可翻页到达）；0 恢复全部按原始顺序显示；句形判定不限类型
-- （native/personal/express 全长词组同样计入配额；例外：≤4 字的
-- _personal 是用户词库，不占配额）；稳定边界：punct/pinned、⚡️/📌
-- 标记、不足 5 字、声母简码（字数超过音节容量）、未覆盖到输入末尾的
-- 部分跨度候选一律不押后；重排池不受影响（本 filter 只动显示层，
-- 上游 word_order 已看过全池）。

local failures = 0
local function check(name, ok, detail)
  if ok then
    print("pass: " .. name)
  else
    print("fail: " .. name .. (detail and (" [" .. detail .. "]") or ""))
    failures = failures + 1
  end
end

local filter = require("mohu_sentence_visibility_filter")

local yielded = {}
yield = function(candidate) yielded[#yielded + 1] = candidate end

-- span 缺省 = 覆盖整个输入（句形候选）；finish 传短值 = 部分跨度。
local function candidate(kind, text, comment, span)
  local value = {
    type = kind,
    text = text,
    comment = comment or "",
    start = span and span.start or 0,
    _end = span and span.finish or nil,
  }
  if value._end == nil then value.full = true end
  function value:get_genuine() return self end
  return value
end

local function make_env(config)
  config = config or {}
  local ctx = { input = config.input or "aaaaaaaaaaaaaaaa" }
  local function finish_of(value)
    if value and value.full then return #ctx.input end
    return value and value.finish or nil
  end
  -- full 标记的候选在运行期补齐 _end（模拟覆盖到输入末尾）。
  local mt = getmetatable(ctx) or {}
  local engine = {
    context = ctx,
    schema = {
      config = {
        get_string = function(_, key)
          if key == "mohu/quick_code_indicator" then return "⚡️" end
          if key == "mohu/pin/indicator" then return "📌" end
          return config[key]
        end,
        get_int = function(_, key)
          local value = config[key]
          return type(value) == "number" and value or nil
        end,
      },
    },
  }
  return { engine = engine, _finish_of = finish_of }
end

local function run_filter(env, candidates)
  for _, cand in ipairs(candidates) do
    if cand.full and cand._end == nil then
      cand._end = #env.engine.context.input
    end
  end
  yielded = {}
  local input = {
    iter = function()
      local index = 0
      return function()
        index = index + 1
        return candidates[index]
      end
    end,
  }
  filter.func(input, env)
  return yielded
end

local function texts_of(list)
  local texts = {}
  for index = 1, #list do texts[index] = list[index].text end
  return texts
end

local function same_texts(a, b)
  if #a ~= #b then return false end
  for index = 1, #a do
    if a[index] ~= b[index] then return false end
  end
  return true
end

local sentences = {
  "今天天气怎么样",
  "今天天气怎么养",
  "今天天起怎么样",
  "进天天起怎么养",
}

-- 1) 默认（1 条）：首条句形候选在前，其余句形押后到词组之后（不删除）。
do
  local env = make_env(nil)
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm", sentences[1]),
    candidate("mohu_zrm", sentences[2]),
    candidate("phrase", "今天"),
    candidate("mohu_zrm", sentences[3]),
  })
  check("default fronts the first sentence and defers the rest",
        same_texts(texts_of(out), { sentences[1], "今天", sentences[2], sentences[3] }))
end

-- 2) 类型不限：长 personal 与 express 全长词组同样计入配额（押后）。
do
  local env = make_env(nil)
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm_personal", sentences[1]),
    candidate("mohu_zrm_personal", sentences[2]),
    candidate("phrase", "今天天气怎么样啊"),
    candidate("mohu_flypy", sentences[3]),
  })
  check("long personal and express variants share the quota",
        same_texts(texts_of(out),
                   { sentences[1], sentences[2], "今天天气怎么样啊", sentences[3] }))
end

-- 3) N=3：前 3 条句形候选在前，其余押后。
do
  local env = make_env({ ["tiger/sentence_visible_candidates"] = 3 })
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm", sentences[1]),
    candidate("phrase", "今天"),
    candidate("mohu_zrm", sentences[2]),
    candidate("mohu_zrm", sentences[3]),
    candidate("mohu_zrm", sentences[4]),
    candidate("phrase", "天气"),
  })
  check("N=3 fronts three sentences and defers the fourth",
        same_texts(texts_of(out),
                   { sentences[1], "今天", sentences[2], sentences[3], "天气", sentences[4] }))
end

-- 4) 0 = 全部显示（旧行为）。
do
  local env = make_env({ ["tiger/sentence_visible_candidates"] = 0 })
  filter.init(env)
  local input = {
    candidate("mohu_zrm", sentences[1]),
    candidate("mohu_zrm", sentences[2]),
    candidate("phrase", "今天"),
  }
  local out = run_filter(env, input)
  check("0 disables trimming entirely",
        same_texts(texts_of(out), texts_of(input)))
end

-- 5) 不足门槛字数的候选（默认 3：两字词与辅码消歧输入）不押后；
-- 三字全跨候选计入配额（lirotk→李若桃 20 条同类变体刷屏问题），
-- 超配额的押后而非删除。
do
  local env = make_env(nil)
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm", "魔虎"),
    candidate("mohu_zrm_personal", "回家"),
    candidate("mohu_zrm", "李若桃"),
    candidate("mohu_zrm", "厉若桃"),
  })
  check("two-char candidates stay visible, third is deferred not dropped",
        same_texts(texts_of(out), { "魔虎", "回家", "李若桃", "厉若桃" }))
end

-- 5c) ≤4 字 _personal（用户词库，含用户层增益标记的学习词）不占配额：
-- xspizi→熊皮子/熊罴子 与默认整句、词组同时可见（2026-09-15 修复）。
do
  local env = make_env({ input = "xspizi" })
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_flypy_personal", "熊皮子"),
    candidate("mohu_flypy_personal", "熊罴子"),
    candidate("sentence", "兄痞子"),
    candidate("phrase", "熊皮"),
    candidate("phrase", "熊罴"),
  })
  check("short personal words bypass the sentence quota",
        same_texts(texts_of(out),
                   { "熊皮子", "熊罴子", "兄痞子", "熊皮", "熊罴" }))
end

-- 5b) tiger/sentence_min_chars 可调回更大的保护范围（如 5）。
do
  local env = make_env({ ["tiger/sentence_min_chars"] = 5 })
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm", "一心一意"),
    candidate("mohu_zrm", "一心二意"),
  })
  check("sentence_min_chars=5 protects four-char candidates",
        #out == 2)
end

-- 6) ⚡️ 简码与 📌 固顶标记、punct/pinned 类型不裁。
do
  local env = make_env(nil)
  filter.init(env)
  local out = run_filter(env, {
    candidate("punct", "，"),
    candidate("mohu_zrm", sentences[1], "⚡️xx"),
    candidate("mohu_zrm", sentences[2], "📌xx"),
    candidate("mohu_zrm", sentences[3]),
    candidate("pinned", "置顶"),
  })
  check("marked, punct and pinned candidates stay visible",
        same_texts(texts_of(out),
                   { "，", sentences[1], sentences[2], sentences[3], "置顶" }))
end

-- 7) 声母简码豁免：字数超过覆盖段音节容量的候选不裁。
do
  -- 输入前 5 字母 abjhx（容量 2），7 字简码词不裁；跨全输入（17 字母，
  -- 容量 8）的 6 字句正常裁剪。
  local env = make_env({ input = "abjhx" .. string.rep("a", 12) })
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm", "阿波罗登月计划", nil, { start = 0, finish = 5 }),
    candidate("mohu_zrm", "今天天气怎么养", nil, { start = 0, finish = 17 }),
    candidate("mohu_zrm", "今天天起怎么养", nil, { start = 0, finish = 17 }),
  })
  check("quick-code abbreviation candidates stay visible",
        same_texts(texts_of(out), { "阿波罗登月计划", "今天天气怎么养", "今天天起怎么养" }))
end

-- 8) 部分跨度候选（未覆盖到输入末尾）不裁——词组选词路径。
do
  local env = make_env({ input = "wojntmhfxdni" })
  filter.init(env)
  local out = run_filter(env, {
    candidate("phrase", "我很", nil, { start = 0, finish = 4 }),
    candidate("phrase", "想你", nil, { start = 0, finish = 12 }),
    candidate("phrase", "想拟", nil, { start = 0, finish = 12 }),
  })
  check("partial-span candidates stay visible",
        same_texts(texts_of(out), { "我很", "想你", "想拟" }))
end

-- 9) 全拼整句（字数=音节容量）超配额押后到词组之后。
do
  local env = make_env({ input = "wojntmhfxdni" })
  filter.init(env)
  local out = run_filter(env, {
    candidate("mohu_zrm", "我今天很想你"),
    candidate("mohu_zrm", "我今天很像你"),
  })
  check("full-pinyin sentence still defers beyond quota",
        #out == 2 and out[1].text == "我今天很想你" and out[2].text == "我今天很像你")
end

-- 10) 配置 clamp：负数与超界值（>50 → 50）落在合法区间，非法值回默认 1。
do
  local env = make_env({ ["tiger/sentence_visible_candidates"] = -5 })
  filter.init(env)
  check("negative config clamps to 0 (unlimited)", env._sv_visible == 0)

  env = make_env({ ["tiger/sentence_visible_candidates"] = 999 })
  filter.init(env)
  check("oversized config clamps to 50", env._sv_visible == 50)

  env = make_env({ ["tiger/sentence_visible_candidates"] = "not-a-number" })
  filter.init(env)
  check("invalid config falls back to default 1", env._sv_visible == 1)
end

if failures > 0 then
  print(string.format("%d failure(s)", failures))
  os.exit(1)
end
print("all checks passed")
