-- Mohu Sentence Visibility Filter
-- Copyright (c) 2026 ksqsf
--
-- Ver: 0.2.0
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 搜狗式整句菜单：深候选池（native 上限 20 条、express 全长词组）在
-- 显示前的过滤器流上完整参与竞争——word_order 跨候选调频与神经重排
-- 都不依赖显示——本 filter 只调显示层：长句输入时菜单前 N 条是句形
-- 候选（类型不限，覆盖全输入、≥sentence_min_chars 字、非缩写），
-- 其余句形候选押后到全部词组候选之后（可翻页到达），菜单观感与商业
-- 整句输入法一致（首条整句 + 词组）。设 0 恢复全部按原始顺序显示。
--
-- 0.2.0: 超配额句形候选从「删除」改为「押后到词组之后」。0.1.x 的
--        直接丢弃把学习词/个人词变成不可选（2026-09-15 用户报告：
--        xspizi 学过熊皮子后仍只显示一条，无法再次选中强化，学习
--        闭环断裂），且与魔然（无裁剪）行为不一致。押后保住「首条
--        整句 + 词组」观感的同时不丢任何候选。
--
-- 不押后的稳定边界：punct/pinned；⚡️ 简码与 📌 固顶标记；不足
-- sentence_min_chars 字的候选（默认 3：两字词与辅码消歧输入）；声母
-- 简码/缩写匹配（字数超过覆盖段音节容量＝字母数/2，词表有 6 千余条
-- 如 abjh→阿波罗计划）；以及未覆盖到输入末尾的部分跨度候选（词组
-- 选词）。个人路径的长句变体（mohu_*_personal）与普通整句同样计入
-- 配额——用户模型会把反复输入的长句学成 personal 类型，豁免它等于
-- 豁免全部；押后（而非删除）保证它们始终可达。重排正确性不受影响：
-- word_order 在上游已看过完整候选池。
--
-- 挂接：mohu_*.schema.yaml filters 列表，mohu_word_order_filter 之后、
-- candidate_override 之前（模型重排先看全池，用户显式覆盖后置不被裁）。
-- 配置：tiger/sentence_visible_candidates（默认 1，clamp 0–50）、
-- tiger/sentence_min_chars（默认 3，clamp 2–20）。

local F = {}

-- 整句判定门槛：候选文本不足该字数视为两字词/辅码消歧输入，不参与
-- 显示裁剪。默认 3——三字及以上同类变体刷屏与长句同为「整句观感」
-- 问题（2026-09-09 实测 lirotk 出 20 条三字变体）。
local default_sentence_min_chars = 3

local function config_number(cfg, key, default, lo, hi)
  local value
  local ok_int, n = pcall(cfg.get_int, cfg, key)
  if ok_int and type(n) == "number" then value = n end
  if value == nil then
    local ok_str, s = pcall(cfg.get_string, cfg, key)
    if ok_str then value = tonumber(s) end
  end
  if type(value) ~= "number" or value ~= value then return default end
  return math.min(hi, math.max(lo, value))
end

function F.init(env)
  local cfg = env.engine.schema.config
  env._sv_visible = math.floor(config_number(cfg, "tiger/sentence_visible_candidates", 1, 0, 50))
  env._sv_min_chars = math.floor(config_number(cfg, "tiger/sentence_min_chars",
    default_sentence_min_chars, 2, 20))
  env._sv_quick = ""
  env._sv_pin = ""
  pcall(function()
    env._sv_quick = cfg:get_string("mohu/quick_code_indicator") or "⚡️"
    env._sv_pin = cfg:get_string("mohu/pin/indicator") or "📌"
  end)
end

function F.fini(env)
end

-- 候选是否计入显示限额（true = 占用一个整句显示名额）。
-- 句形判定不限类型：native 整句、express 全长词组、_personal 个人路径
-- 变体（用户模型会把长句变体学成 personal 类型，2026-09-09 实测全部
-- 以 mohu_zrm_personal 出现）同样挤占「整句观感」。例外：≤4 字的
-- _personal 是用户词库的一部分（含 2026-09-15 起引擎按用户层增益
-- 标记的学习词，如 xspizi→熊皮子/熊罴子），不占配额、按原序输出，
-- 保证学习词与默认首选同时可见可选；反复输入的长句仍计入配额押后。
-- 句形 = 覆盖到输入末尾（与 word_order 的 consumes_current_input 同型
-- 判定）＋ 达到 sentence_min_chars ＋ 字数不超过覆盖段音节容量
-- （字母数/2；超过即声母简码/缩写匹配，如 abjh→阿波罗计划）。
local function quota_sentence(env, cand)
  local genuine = cand.get_genuine and cand:get_genuine() or cand
  local t = genuine.type
  if t == "punct" or t == "pinned" then return false end
  local text = genuine.text
  if type(text) ~= "string" then return false end
  local len = utf8.len(text)
  if not len or len < env._sv_min_chars then return false end
  if (t == "mohu_zrm_personal" or t == "mohu_flypy_personal") and len <= 4 then
    return false
  end
  local ctx = env.engine and env.engine.context
  local ctx_input = ctx and ctx.input
  if type(ctx_input) ~= "string" or #ctx_input == 0 then return false end
  local finish = tonumber(genuine._end)
  if finish ~= #ctx_input then return false end
  local start_pos = tonumber(genuine.start) or 0
  local seg = ctx_input:sub(start_pos + 1, finish)
  local letters = select(2, seg:gsub("%a", "%1"))
  if len > math.floor(letters / 2) then return false end
  local comment = genuine.comment
  if type(comment) == "string" and comment ~= "" then
    if env._sv_quick ~= "" and comment:sub(1, #env._sv_quick) == env._sv_quick then
      return false
    end
    if env._sv_pin ~= "" and comment:sub(1, #env._sv_pin) == env._sv_pin then
      return false
    end
  end
  return true
end

function F.func(input, env)
  if env._sv_visible <= 0 then
    for cand in input:iter() do yield(cand) end
    return
  end
  local emitted = 0
  local deferred = {}  -- 超配额句形候选：押后到词组之后，不删除
  for cand in input:iter() do
    if quota_sentence(env, cand) then
      emitted = emitted + 1
      if emitted <= env._sv_visible then
        yield(cand)
      else
        deferred[#deferred + 1] = cand
      end
    else
      yield(cand)
    end
  end
  for i = 1, #deferred do yield(deferred[i]) end
end

return F
