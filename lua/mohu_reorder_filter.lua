-- Mohu Reorder Filter
-- Copyright (c) 2023, 2024, 2025, 2026 ksqsf
--
-- Ver: 0.3.2
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 0.3.2: 两字 native 候选独立输出：两字终态只可能来自两音节带辅码的
--        输入（用户已显式消歧），不再要求词库也存在同文本。
--
-- 0.3.1: 修复重构引入的 bug。
--
-- 0.3.0: 重构，少许性能优化。
--
-- 0.2.3: 允许单字在4码输入时也被重排。
--
-- 0.2.2: 进一步放宽匹配条件，允许多字词候选被重排。
--
-- 0.2.1: 放宽匹配条件，允许带辅的候选也被重排。
--
-- 0.2.0: 修复诸多问题。
--
-- 0.1.5: 少许性能优化。
--
-- 0.1.4: 配合 mohu_pin。
--
-- 0.1.3: 修复一个导致候选重复输出的 bug。
--
-- 0.1.2: 配合 show_chars_anyway 设置。从 show_chars_anyway 设置起，
-- fixed 输出有可能出现在 script 之后！此情况只覆写 comment 而不做重排。
--
-- 0.1.1: 要求候选项合并时 preedit 也匹配，以防御一种边角情况（挂接某
-- 些第三方码表时可能出现）。
--
-- 0.1.0: 本文件的主要作用是用 script 候选覆盖对应的 table 候选，从而
-- 解决字频维护问题。例如：原本用 mau 输入三简字「码」时，该候选是从
-- table 输出的，不会增加 script 翻译器用户词典的「码」字的字频。而长
-- 期使用时，很有可能会使用 mau 键入「祃」等较生僻的字，而这些生僻字反
-- 而是从 script 翻译器输出的，会增加这些字的字频。这个问题会导致在长
-- 期使用后，组词时会导致常用的「码」反而排在其他生僻字后面。该 filter
-- 的主要作用就是重排 table 和 script 翻译器输出，让简码对应的候选也变
-- 成 script 候选，从而解决字频问题。
--
-- 必须与 mohu_express_translator v0.5.0 以上版本联用。

local Top = {}
local native_sentence_types = {
    mohu_zrm = true,
    mohu_flypy = true,
    mohu_zrm_personal = true,
    mohu_flypy_personal = true,
}
local native_independent_min_length = 5

function Top.init(env)
    -- At most THRESHOLD smart candidates are subject to reordering,
    -- for performance's sake.
    env.reorder_threshold = 50
    env.quick_code_indicator = env.engine.schema.config:get_string("mohu/quick_code_indicator") or "⚡️"
    env.pin_indicator = env.engine.schema.config:get_string("mohu/pin/indicator") or "📌"
end

function Top.fini(env)
end

--------------------------------------------------------------------------------
-- 状态机定义
--
-- 输入的候选格式是：
--   [pinned]* [fixed1]* [native]* smart1{1} [fixed2]* smart2+
-- native 候选有独立身份，不参与 fixed -> smart 替换，输出在 fixed 与 smart 之间。
-- 当词库候选存在时，native 只允许输出词库也能覆盖的文本；这样模型只负责
-- 在词库可行集合内排序，不会用静态码表绕过用户词造出错误分词。例外是
-- >=5 字候选与两字候选：两字终态候选只可能在两音节带辅码的输入上产生
-- （更多音节的输入无法被两段消费完），用户已显式消歧，独立输出。
--
-- + kCollecting   收集 pinned, fixed1, smart1
-- + kMatching     碰到了 smart2，且还有一些候选等待匹配
-- + kDone         匹配完成，直传所有剩余候选
--------------------------------------------------------------------------------
local kCollecting  = 0
local kMatching    = 1
local kDone        = 2

function Top.func(t_input, env)
    local ctx = {
        phase = kCollecting,  -- 当前状态
        fixed_list = {},      -- 等待匹配的固定候选
        fixed_next = 1,       -- 下一个待匹配的固定候选匹配的
        native_list = {},     -- 原生整句候选不参与 fixed/smart 身份替换
        smart_list = {},      -- 等待匹配的整句候选
        trailing_list = {},   -- fixed 匹配完成后暂存的其余候选
        lexicon_texts = {},   -- 本轮非 native 候选的文本集合
        threshold = env.reorder_threshold,
        pin_set = {},         -- 候选是否是 pinned

        -- 用于处理 smart1
        delay_slot = {},      -- 延迟槽
        additional_check = 0  -- 转移到 kMatching 前额外需要看到的 smart 数量
    }

    for cand in t_input:iter() do
        if cand:get_genuine().type == "punct" then
            yield(cand)
        elseif native_sentence_types[cand:get_genuine().type] then
            table.insert(ctx.native_list, cand)
        elseif ctx.phase == kDone then
            ctx.lexicon_texts[cand.text] = true
            table.insert(ctx.trailing_list, cand)
        elseif ctx.phase == kCollecting then
            ctx.lexicon_texts[cand.text] = true
            Top.handle_collecting(env, ctx, cand)
        else
            ctx.lexicon_texts[cand.text] = true
            Top.handle_matching(env, ctx, cand)
        end
    end

    Top.flush(env, ctx, true)
end

--------------------------------------------------------------------------------
-- 状态转移
--------------------------------------------------------------------------------

function Top.handle_collecting(env, ctx, cand)
    -- print('handle_collecting: ' .. cand.text .. ', type=' .. cand.type .. ', comment=' .. cand.comment)

    -- 以下是固定候选
    if cand.type == "pinned" then
        -- Pin 输出, 需要额外检查 smart1
        table.insert(ctx.fixed_list, cand)
        ctx.pin_set[cand.text] = true
        ctx.additional_check = 1

    elseif cand.comment == "`F" then
        -- 码表输出（且非 Pin）
        if not ctx.pin_set[cand.text] then
            table.insert(ctx.fixed_list, cand)
        end

        -- 以下是 smart 候选
    elseif ctx.additional_check > 0 then
        -- 看到了 smart1，只记录它。在 MATCHING 阶段再处理。
        table.insert(ctx.delay_slot, cand)
        ctx.additional_check = ctx.additional_check - 1

    else
        -- 看到了 smart2，转向 kMatching 状态。
        ctx.phase = kMatching

        -- 可能收集到了 smart1，先处理。
        for _, c in ipairs(ctx.delay_slot) do
            Top.handle_matching(env, ctx, c)
        end
        ctx.delay_slot = {}

        -- 处理当前看到的 smart2。
        if ctx.phase == kDone then
            Top.yield_exact(env, cand)
        else
            Top.handle_matching(env, ctx, cand)
        end
    end
end

function Top.handle_matching(env, ctx, cand)
    if ctx.threshold == 0 then
        ctx.phase = kDone
        table.insert(ctx.trailing_list, cand)
        return
    else
        ctx.threshold = ctx.threshold - 1
    end

    -- print('handle_matching: ' .. cand.text .. ', threshold=' .. tostring(ctx.threshold))

    table.insert(ctx.smart_list, cand)
    while ctx.fixed_next <= #ctx.fixed_list do
        local fcand = ctx.fixed_list[ctx.fixed_next]
        if not Top.reorderable(fcand) then
            Top.yield_exact(env, fcand)
            ctx.fixed_next = ctx.fixed_next + 1
        else
            local si, scand = Top.find_matching_scand(ctx, fcand)
            if si == nil then
                break
            end
            Top.yield_smart_in_place_of_fixed(env, scand, fcand)
            ctx.fixed_next = ctx.fixed_next + 1
            table.remove(ctx.smart_list, si)
        end
    end
    if ctx.fixed_next > #ctx.fixed_list then
        ctx.phase = kDone
    end
end

--------------------------------------------------------------------------------
-- 辅助函数
--------------------------------------------------------------------------------

--- 在 smart_list 中查找匹配 fcand 的 scand，返回下标和 scand 对象。
function Top.find_matching_scand(ctx, fcand)
    for si = #ctx.smart_list, 1, -1 do
        local scand = ctx.smart_list[si]
        if Top.candidate_match(scand, fcand) then
            return si, scand
        end
    end
    return nil, nil
end

--- 输出所有剩下的候选。
function Top.flush(env, ctx, include_delay_slot)
    for i = ctx.fixed_next, #ctx.fixed_list do
        Top.yield_exact(env, ctx.fixed_list[i])
    end
    for _, c in ipairs(ctx.native_list) do
        local text_length = utf8.len(c.text) or 0
        local native_type = c:get_genuine().type
        local is_personal = native_type == "mohu_zrm_personal" or
            native_type == "mohu_flypy_personal"
        -- text_length == 2：两音节带辅码输入的两字终态，独立输出（见文件头说明）。
        if next(ctx.lexicon_texts) == nil or ctx.lexicon_texts[c.text]
            or text_length >= native_independent_min_length
            or text_length == 2 or is_personal then
            Top.yield_exact(env, c)
        end
    end
    if include_delay_slot then
        -- 只在完全匹配完毕后才清空延迟槽
        for _, c in ipairs(ctx.delay_slot) do
            Top.yield_exact(env, c)
        end
        ctx.delay_slot = {}
    end
    for _, c in ipairs(ctx.smart_list) do
        Top.yield_exact(env, c)
    end
    for _, c in ipairs(ctx.trailing_list) do
        Top.yield_exact(env, c)
    end
    ctx.fixed_list = {}
    ctx.native_list = {}
    ctx.smart_list = {}
    ctx.trailing_list = {}
    ctx.lexicon_texts = {}
end

--- cand 是否有可能被重排（即是否有可能是 smart 输出）。
function Top.reorderable(cand)
    local len = utf8.len(cand.text)
    return (len > 1 and #cand.preedit >= 2 * len) or (len == 1 and #cand.preedit <= 5)
end

--- 检查 scand 是否可以替代 fcand 。
---
--- Preedit 检查确保 scand 不单单对应于从输入的前缀。
function Top.candidate_match(scand, fcand)
    if scand.text ~= fcand.text then
        return false
    end
    local spreedit = scand.preedit
    local fpreedit = fcand.preedit
    if spreedit == fpreedit then
        return true
    end
    return (#fpreedit <= #spreedit and #fpreedit >= #spreedit - math.floor((#spreedit + 1) / 3) + 1)
        and spreedit:gsub('%s', '') == fpreedit
end

--- 输出候选但恢复简快码提示符。若非简快码，则直接输出。
function Top.yield_exact(env, cand)
    if cand.comment == "`F" then
        cand.comment = env.quick_code_indicator
    end
    yield(cand)
end

--- 用 scand 替代 fcand 并输出。
function Top.yield_smart_in_place_of_fixed(env, scand, fcand)
    if fcand.comment == "`F" then
        scand.comment = env.quick_code_indicator .. scand.comment
    elseif fcand.type == "pinned" then
        scand.comment = env.pin_indicator
    end
    yield(scand)
end

return Top

-- Local Variables:
-- lua-indent-level: 4
-- End:
