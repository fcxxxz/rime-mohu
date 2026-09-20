-- Mohu Sentence Visibility Filter
-- Copyright (c) 2026 ksqsf
--
-- Ver: 0.4.0
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 搜狗式整句菜单：深候选池（native 上限 20 条、express 全长词组）在
-- 显示前的过滤器流上完整参与竞争——word_order 跨候选调频与神经重排
-- 都不依赖显示——本 filter 只调显示层：长句输入时菜单前 N 条是句形
-- 候选（类型不限，覆盖全输入、≥sentence_min_chars 字、非缩写），
-- 其余句形候选押后到全部词组候选之后，菜单观感与商业整句输入法一致
-- （首条整句 + 词组）。
--
-- 0.4.0: 移除 ≤4 字 _personal 免配额豁免。引擎的 personal 标记是
--        路径级继承（解码状态沿边传播），全码 3 字变体只要搭乘共享
--        个人子边（qygfda 的 X跟打 全走「跟打」个人边）就整族带
--        _personal，豁免把押后上限整个吞掉（2026-09-16 用户实测
--        qygfda 仍刷屏）。押后不是删除（0.2.0 教训），学习词计入
--        配额后仍可选可强化：排第一的学习词占前排配额位（个人词先验
--        保证），同族兄弟进押后尾部（上限 N）；引擎评分池与学习层
--        不受显示裁剪影响。
--
-- 0.3.0: 新增 tiger/sentence_deferred_candidates 押后尾部上限（-1=全部
--        保留，默认，兼容未配置本键的 schema；≥0 只保留押后句形中排名
--        最前 N 条）。动机：全码 3 字输入（qygfda→X跟打 20 条变体）没有
--        词组候选可垫，押后尾部原样跟出等于没裁；深尾部变体实际选中
--        都走辅码消歧，截断只动显示层，不碰引擎评分池与学习可见性
--        （≤4 字 _personal 豁免当时保留，0.4.0 移除）。
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
-- 选词）。个人路径变体（mohu_*_personal）与普通整句同样计入配额
-- ——用户模型会把反复输入的长句学成 personal 类型，路径级继承又让
-- 搭乘共享个人边的短变体整族带标记，豁免它等于豁免全部；押后（而非
-- 删除）保证它们始终可达。重排正确性不受影响：
-- word_order 在上游已看过完整候选池。
--
-- 挂接：mohu_*.schema.yaml filters 列表，mohu_word_order_filter 之后、
-- candidate_override 之前（模型重排先看全池，用户显式覆盖后置不被裁）。
-- 配置：tiger/sentence_visible_candidates（默认 1，clamp 0–50；
-- 0 = 全部按原始顺序显示，押后上限随之失效）、tiger/sentence_min_chars
-- （默认 3，clamp 2–20）、tiger/sentence_deferred_candidates（默认 -1
-- 全保留，clamp -1–50；0 = 押后句形全部不再显示）。

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
  -- 押后尾部上限：-1 = 全部保留（默认，未配置本键的 schema 行为不变）；
  -- ≥0 = 押后的句形候选只保留排名最前 N 条，其余不再显示。
  env._sv_deferred = math.floor(config_number(cfg, "tiger/sentence_deferred_candidates", -1, -1, 50))
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
-- 变体（用户模型会把长句学成 personal 类型，路径级继承还会让搭乘共享
-- 个人边的全码短变体整族带 _personal——qygfda 的 X跟打 实测）同样
-- 挤占「整句观感」。0.4.0 起短 personal 不再豁免：排第一的学习词占
-- 前排配额位（个人词先验保证），同族兄弟进押后尾部仍可选（xspizi 的
-- 熊皮子/熊罴子学习闭环不断）。
-- 句形 = 覆盖到活动段末尾 ＋ 达到 sentence_min_chars ＋ 字数不超过
-- 覆盖段音节容量（字母数/2；超过即声母简码/缩写匹配，如 abjh→阿波
-- 罗计划）。覆盖终点取 composition 末段 _end 而非 #input：caret 移到
-- 句中时段被截短，段内句形候选永远覆盖不到整个输入末尾，会被当成
-- 部分跨度词组候选逃过配额裁剪（句尾减一处的编辑点上 20 条句形流
-- 刷屏，2026-09-20）；caret 在句尾时末段即全输入，与旧判定等价。
local function quota_sentence(env, cand)
  local genuine = cand.get_genuine and cand:get_genuine() or cand
  local t = genuine.type
  if t == "punct" or t == "pinned" then return false end
  local text = genuine.text
  if type(text) ~= "string" then return false end
  local len = utf8.len(text)
  if not len or len < env._sv_min_chars then return false end
  local ctx = env.engine and env.engine.context
  local ctx_input = ctx and ctx.input
  if type(ctx_input) ~= "string" or #ctx_input == 0 then return false end
  local finish_limit = #ctx_input
  local comp_ok, comp = pcall(function() return ctx.composition end)
  if comp_ok and comp ~= nil then
    local seg_ok, seg = pcall(function() return comp:back() end)
    if seg_ok and seg ~= nil then
      local seg_end = tonumber(seg._end)
      if seg_end and seg_end >= 0 and seg_end <= #ctx_input then
        finish_limit = seg_end
      end
    end
  end
  local finish = tonumber(genuine._end)
  if finish ~= finish_limit then return false end
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
  local deferred = {}  -- 超配额句形候选：押后到词组之后；超出尾部上限的丢弃
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
  local keep = env._sv_deferred < 0 and #deferred
      or math.min(env._sv_deferred, #deferred)
  for i = 1, keep do yield(deferred[i]) end
end

return F
