-- mohu_aux_translator -- 实现直接辅助码筛选的翻译器
--
-- Author: ksqsf
-- License: GPLv3
-- Version: 0.3.2
--
-- 0.3.2: 少许性能优化。
--
-- 0.3.1: 修正单字辅助码匹配。
--
-- 0.3.0: 优化性能。
--
-- 0.2.2: 支持 tab 跳转。
--
-- 0.2.1: 修正与 pin 的兼容性。
--
-- 0.2.0: 重做。支持辅助码下沉和诸多新的自定义选项。
--
-- 0.1.5: 允许自定义预取长度。
--
-- 0.1.4: 继续优化逻辑。
--
-- 0.1.3: 优化逻辑。
--
-- 0.1.2：句子优先，避免输入过程中首选长度大幅波动。一定程度上提高性能。
--
-- 0.1.1：三码优先单字。
--
-- 0.1.0: 实作。

local mohu = require("mohu")
local contextual = require("mohu_contextual_translator")
local Module = {}

-- 一些音节需要较多预取
local BIG_SYLLABLES = {
    ["ji"] = 200,
    ["ui"] = 200,
    ["yi"] = 200,
    ["ii"] = 200,
}

function Module.init(env)
    env.aux_table = mohu.load_zrmdb()
    contextual.init_pair(env, "translator")
    env.prefetch_threshold = env.engine.schema.config:get_int("mohu/prefetch") or -1

    -- 词组和单字优先设置
    env.char_priority = mohu.get_config_bool(env, "mohu/char_priority", false)
    env.char_code_len = env.char_priority and 4 or 3
    env.word_over_char_tolerance = env.engine.schema.config:get_int("mohu/word_over_char_tolerance") or 3
    env.word_over_char_adaptive = mohu.get_config_bool(env, "mohu/word_over_char_adaptive", true)

    -- 固定句子为首选?
    env.is_sentence_priority = mohu.get_config_bool(env, "mohu/sentence_priority", true)
    env.sentence_priority_length = env.engine.schema.config:get_int("mohu/sentence_priority_length") or 4

    -- 输入辅助码首选后移?
    env.is_aux_priority = mohu.get_config_bool(env, "mohu/aux_priority", true)
    env.aux_priority_defer = env.engine.schema.config:get_int("mohu/aux_priority_defer") or 3
    env.aux_priority_length = env.engine.schema.config:get_int("mohu/aux_priority_length") or 1
    env.aux_priority_indicator = env.engine.schema.config:get_string("mohu/aux_priority_indicator") or "▾"

    -- Pin 适配
    env.pin_infix = env.engine.schema.config:get_string("mohu/pin/panacea/infix") or '//'
    env.pin_indicator = env.engine.schema.config:get_string("mohu/pin/indicator") or '📌'

    -- 辅助码作用位置
    local aux_position = env.engine.schema.config:get_string("mohu/aux_position") or "any"
    if aux_position == "first" then
        env.is_aux_for_first = true
    elseif aux_position == "last" then
        env.is_aux_for_last = true
    else
        env.is_aux_for_any = true
    end

    -- ----------------
    -- 让全相关逻辑
    -- ----------------
    -- 让全基于 mohu.lua 中的 Yielder 接口实现。
    -- Yielder 的主要功能是：
    -- (1) 可以延迟候选，并且可以在正确的时机把之前延迟的候选输出出来。
    -- (2) 可以在即将真正 yield 候选时再次确认是否应该延迟。
    --     —— 下方的 before_cb 检查首选是否是之前已经出现过的。
    --          如果是，就延迟 aux_priority_defer 位。
    -- (3) 可以在真正 yield 之后通知已经 yield 了。
    --     —— 下方的 after_cb 记录首选。
    --
    -- 具体的 translate 逻辑无需关心让全，只需调用 env.y:yield 和
    -- env.y:yield_all 即可。
    local previous_word = ""
    local previous_word_aux = ""
    local before_cb = function(index, cand)
        if index > 0 and cand.comment == "" then
            return nil
        end
        local should_defer = -- 尊重 aux_priority_length
            #cand.comment == env.aux_priority_length and
            -- 输入比之前多一位辅码
            #previous_word_aux + 1 == #cand.comment and
            -- 内容一致
            cand.text == previous_word and
            previous_word_aux == cand.comment:sub(1, #previous_word_aux)
        if should_defer then
            cand.comment = cand.comment .. env.aux_priority_indicator
            return env.aux_priority_defer
        else
            return nil
        end
    end
    local after_cb = function(index, cand)
        if index == 0 then
            previous_word = cand.text
            previous_word_aux = cand.comment
        end
    end
    if env.is_aux_priority then
        env.y = mohu.Yielder.new(before_cb, after_cb)
    else
        env.y = mohu.Yielder.new(nil, nil)
    end

    -- ------------------------------------
    -- 上屏逻辑（清空辅助码和其他内部状态）
    -- ------------------------------------
    local input_sans_aux = nil

    -- 在自带的 OnSelect 之前生效，从而获取到 selected candidate
    local function on_select_pre(ctx)
        if (string.find(ctx:get_preedit().text, env.pin_infix) == nil) then
            input_sans_aux = nil

            local composition = ctx.composition
            if composition:empty() then
                return
            end

            local segment = composition:back()
            if not (segment.status == "kSelected" or segment.status == "kConfirmed") then
                return
            end
            if mohu.segment_is_reverse_lookup(segment) then
                return
            end

            local cand = segment:get_selected_candidate()
            if cand == nil then
                return
            end
            local gcand = cand:get_genuine()
            if gcand.type == "pinned" then
                return
            end
            if env.engine.context:get_option("chaifen") then
                cand = gcand
            end
            if cand and cand.comment and cand.comment ~= "" then
                local aux_match = gcand.comment:match("^[a-z]+")
                if aux_match then
                    local aux_length = #aux_match
                    input_sans_aux = ctx.input:sub(1, segment._start)
                        .. ctx.input:sub(segment._start + 1, segment._end - aux_length)
                        .. ctx.input:sub(segment._end + 1)
                end
            end
        end
    end

    -- 在自带的 OnSelect 之后生效
    local function on_select_post(ctx)
        if input_sans_aux then
            ctx.input = input_sans_aux
            if ctx.composition:has_finished_composition() then
                ctx:commit()
            end
        end
        input_sans_aux = nil
        previous_word = ""
        previous_word_aux = ""
    end

    env.notifier_pre = env.engine.context.select_notifier:connect(on_select_pre, 0)
    env.notifier_post = env.engine.context.select_notifier:connect(on_select_post)
end

function Module.fini(env)
    env.notifier_pre:disconnect()
    env.notifier_post:disconnect()
    env.aux_table = nil
    contextual.fini_pair(env)
    collectgarbage()
end

function Module.func(input, seg, env)
    env.y:reset()

    -- 每 10% 的翻译触发一次 GC
    if math.random() < 0.1 then
        collectgarbage()
    end

    local input_len = utf8.len(input) or 0
    if input_len <= env.char_code_len then
        Module.TranslateChar(env, seg, input, input_len)
    elseif input_len % 2 == 1 then
        Module.TranslateOdd(env, seg, input, input_len)
    else
        Module.TranslateEven(env, seg, input, input_len)
    end

    env.y:clear()
end

function Module.TranslateChar(env, seg, input, input_len)
    local sp = input:sub(1, 2)
    local aux = input:sub(3, 4)
    local iter = mohu.make_peekable(Module.translate_with_aux(env, seg, sp, aux))

    -- 特殊情况：若找不到被辅的字，则在用户要求 sentence_priority 时查询 nonaux
    -- 例如 mal 理解成 ma'l，输出所有二字词。
    if env.is_sentence_priority and input_len > 2 and iter:peek() and #iter:peek().comment == 0 then
        local nonaux_iter = mohu.make_peekable(Module.translate_without_aux(env, seg, input))
        for c in nonaux_iter do
            if utf8.len(c.text) == 2 then
                env.y:yield(c)
            end
        end
    end

    env.y:yield_all(iter)
end

--- 应对输入长度为奇数的情况。
--- 输入长度为奇数时，input 的末码为辅码，其余部分为双拼。
---
--- @param env table
--- @param seg Segment
--- @param input string 当前输入段对应的原始输入
--- @param input_len number 原始输入的 Unicode 字符数
function Module.TranslateOdd(env, seg, input, input_len)
    local sp = input:sub(1, input_len - 1)
    local aux = input:sub(input_len, input_len)
    local aux_iter = mohu.make_peekable(Module.translate_with_aux(env, seg, sp, aux))

    -- 处理首选。
    if env.is_sentence_priority and
        -- 在输入较长时，要求首选是句子时，总是先输出句子
        (input_len > 5 and env.is_sentence_priority) or
        -- 在5码时，检查是否有带辅二字词，如果没有，才考虑输出句子
        (input_len == 5 and
         not (aux_iter:peek() and
              utf8.len(aux_iter:peek().text) == 2 and
              #aux_iter:peek().comment > 0))
    then
        local nonaux_iter = mohu.make_peekable(Module.translate_without_aux(env, seg, input))
        if nonaux_iter:peek() and utf8.len(nonaux_iter:peek().text) >= env.sentence_priority_length then
            env.y:yield(nonaux_iter())
        end
    end

    -- 若之前已经输出了句子候选，则跳过此后一切句子。
    if env.y.index > 0 and aux_iter:peek() and aux_iter:peek().type == "sentence" then
        aux_iter:next()
    end

    -- 带辅翻译。
    env.y:yield_all(aux_iter)
end

--- 应对输入长度为偶数的情况。
--- 输入长度为偶数时，input 可能被理解为 (1) 末二码为辅 (2) 全双拼。
---
--- @param env table
--- @param seg Segment
--- @param input string 当前输入段对应的原始输入
--- @param input_len number 原始输入的 Unicode 字符数
function Module.TranslateEven(env, seg, input, input_len)
    local sp = input:sub(1, input_len - 2)
    local aux = input:sub(input_len - 1, input_len)
    local nonaux_iter = mohu.make_peekable(Module.translate_with_aux(env, seg, input))
    local aux_iter = mohu.make_peekable(Module.translate_with_aux(env, seg, sp, aux))

    if -- 要求首选固定是句子
        env.is_sentence_priority
    then
        local c = nonaux_iter:peek()
        local c_len = c and utf8.len(c.text) or 0
        if c and c_len >= env.sentence_priority_length and c_len == input_len / 2 then
            env.y:yield(nonaux_iter:next())
            -- 只输出一个句子：如果 aux 的第一个候选也是句子，就跳过
            if aux_iter:peek() and aux_iter:peek().type == "sentence" then
                aux_iter:next()
            end
        end
    end

    -- 遵守 word_over_char_tolerance：取出 tol 个 nonaux 词语，再把 aux 首选放进去。
    local pool = mohu.peekable_iter_take_while_upto(
        nonaux_iter,
        env.word_over_char_tolerance,
        function(c)
            return (c.type == "phrase" or c.type == "user_phrase") and utf8.len(c.text) == input_len / 2
    end)
    if aux_iter:peek() and #aux_iter:peek().comment > 0 then
        table.insert(pool, aux_iter())
    end
    -- 遵守调频要求
    if env.word_over_char_adaptive then
        table.sort(pool, function(a, b)
                       return a.quality > b.quality
        end)
    end
    -- 输出前 tol+1 个候选。
    for _, c in pairs(pool) do
        env.y:yield(c)
    end

    -- 输出被辅候选。
    for c in aux_iter do
        if #c.comment > 0 then
            env.y:yield(c)
        else
            -- 已经结束了！
            break
        end
    end

    -- 输出其他非辅候选。
    env.y:yield_all(nonaux_iter)
end

-- nil = unrestricted
function Module.get_prefetch_threshold(env, sp)
    local p = env.prefetch_threshold or -1
    if p <= 0 then
        return nil
    end
    if BIG_SYLLABLES[sp] then
        return math.max(BIG_SYLLABLES[sp], p)
    else
        return p
    end
end

-- 当 aux 为空时，相当于 translate_without_aux。
-- Returns a stateful iterator of <Candidate, String?>.
function Module.translate_with_aux(env, seg, sp, aux)
    if not aux or #aux == 0 then
        return Module.translate_without_aux(env, seg, sp)
    end

    local iter = Module.translate_without_aux(env, seg, sp)
    local threshold = Module.get_prefetch_threshold(env, sp)
    local matched = {}
    local unmatched = {}
    local n_matched = 0
    local n_unmatched = 0
    for cand in iter do
        if Module.candidate_match(env, cand, aux) then
            table.insert(matched, cand)
            cand.comment = aux
            n_matched = n_matched + 1
        else
            table.insert(unmatched, cand)
            n_unmatched = n_unmatched + 1
        end
        if threshold and (n_matched + n_unmatched > threshold) then
            break
        end
    end

    local i = 1
    return function()
        if i <= n_matched then
            i = i + 1
            return matched[i - 1], aux
        elseif i <= n_matched + n_unmatched then
            i = i + 1
            return unmatched[i - 1 - n_matched], nil
        else
            -- late candidates can also be matched.
            local cand = iter()
            if Module.candidate_match(env, cand, aux) then
                cand.comment = aux
                return cand, aux
            else
                return cand, nil
            end
        end
    end
end

-- Returns a stateful iterator of <Candidate, String?>.
function Module.translate_without_aux(env, seg, sp)
    local translation = contextual.get(env):query(sp, seg)
    if translation == nil then
        return function()
            return nil
        end
    end
    local advance, obj = translation:iter()
    return function()
        local c = advance(obj)
        return c, nil
    end
end

function Module.candidate_match(env, cand, aux)
    if not cand then
        return nil
    end
    if not (cand.type == "phrase" or cand.type == "user_phrase") or #cand.text == 0 then
        return false
    end

    -- 'vaux' means '^aux'; it is meant to match the beginning of an
    -- auxcode
    local vaux = " " .. aux
    local word = cand.text
    local word_len = utf8.len(word)
    local first, last = Module.get_first_and_last_codepoints(word)

    -- Check if they match
    if env.is_aux_for_any then
        if Module.char_match(env, first, vaux) or (word_len > 1 and Module.char_match(env, last, vaux)) then
            return true
        end
        if #aux == 2 and word_len > 1 then    -- word aux, the code style is meant to minimize object creation
            local first_auxcodes = env.aux_table[first]
            if not first_auxcodes then return false end
            local last_auxcodes = env.aux_table[last]
            if not last_auxcodes then return false end
            local a1 = aux:sub(1,1)
            local a2 = aux:sub(2,2)
            return (first_auxcodes:match(a1) and last_auxcodes:match(a2)) or (first_auxcodes:match(a2) and last_auxcodes:match(a1))
        end
    elseif env.is_aux_for_first then
        return Module.char_match(env, first, vaux)
    elseif env.is_aux_for_last then  -- this is stupid, why do we even support this?
        return Module.char_match(env, last, vaux)
    end

    return false
end

function Module.get_first_and_last_codepoints(word)
    local first = utf8.codepoint(word, 1)
    if not first then return nil, nil end
    local len = utf8.len(word)
    if len == 1 then
        return first, first
    end
    local last_byte_pos = utf8.offset(word, -1)
    local last = last_byte_pos and utf8.codepoint(word, last_byte_pos) or first
    return first, last
end

function Module.char_match(env, codepoint, vaux)
    local auxcodes = env.aux_table[codepoint]
    if not auxcodes then return false end
    return string.find(auxcodes, vaux, 1, true) ~= nil
end

-- NOTE: Unused old implementation, preserved for reference.
-- Current `candidate_match` uses a more GC-friendly method.
--
-- BTW, this actually looks buggy: suppose any_use=false and word is
-- single char, this will return an empty list.
function Module.aux_list(env, word)
    local aux_list = {}
    local first = nil
    local last = nil
    local any_use = env.is_aux_for_any
    for _, c in utf8.codes(word) do
        if not first then
            first = c
        end
        last = c
        -- any char
        if any_use then
            local c_aux_list = env.aux_table[c]
            if c_aux_list then
                for c_aux in c_aux_list:gmatch("%S+") do
                    table.insert(aux_list, c_aux:sub(1, 1))
                    table.insert(aux_list, c_aux)
                end
            end
        end
    end

    -- First char & last char
    if utf8.len(word) > 1 then
        if not any_use and env.is_aux_for_first then
            local c_aux_list = env.aux_table[first]
            for c_aux in c_aux_list:gmatch("%S+") do
                table.insert(aux_list, c_aux:sub(1, 1))
                table.insert(aux_list, c_aux)
            end
        end
        if not any_use and env.is_aux_for_last then
            local c_aux_list = env.aux_table[last]
            for c_aux in c_aux_list:gmatch("%S+") do
                table.insert(aux_list, c_aux:sub(1, 1))
                table.insert(aux_list, c_aux)
            end
        end

        if any_use then
            local first_aux_list = env.aux_table[first]
            local last_aux_list = env.aux_table[last]
            for aux1 in first_aux_list:gmatch("%S+") do
                for aux2 in last_aux_list:gmatch("%S+") do
                    table.insert(aux_list, aux1:sub(1, 1) .. aux2:sub(1, 1))
                end
            end
            for aux1 in last_aux_list:gmatch("%S+") do
                for aux2 in first_aux_list:gmatch("%S+") do
                    table.insert(aux_list, aux1:sub(1, 1) .. aux2:sub(1, 1))
                end
            end
        end
    end
    return aux_list
end

return Module

-- Local Variables:
-- lua-indent-level: 4
-- End:
