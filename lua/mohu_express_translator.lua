-- Mohu Translator (for Express Editor)
-- Copyright (c) 2023, 2024, 2025, 2026 ksqsf
--
-- Ver: 0.12.2
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 0.12.2: 五码词辅候选消费辅码，未匹配时才回退到四码词
--
-- 0.12.1: 造词模式阻止输出简码码表多字词
--
-- 0.12.0: 引入惰性加载
--
-- 0.11.0: 引入 quick_code_in_sentence_making 配置项。
-- 该配置项改进了魔虎最初的一项设计（造词时禁止输出固定选项）以维持造词能力，
-- 但事实上配合 reorder_filter 可以取消这一限制。
-- (辅筛模式此前的 fix/use_dict 已经实现该功能。)
--
-- 0.10.1: 配合主方案变更的次要修改。
--
-- 0.10.0: 增加 inject_prioritize 支持。
--
-- 0.9.0: show_words_anyway 和 show_chars_anyway 分别更名为
-- inject_fixed_words 和 inject_fixed_chars。为保持兼容性，原名还可以
-- 继续使用（优先级高于新名），但未来可能被删除。
--
-- 0.8.1: 支持 word_filter_match_indicator。
--
-- 0.8.0: 适配 hint_filter，支持词辅提前提示。
--
-- 0.7.2: 修正词辅在三字词可能不生效的问题。
--
-- 0.7.1: 修正词辅与整句辅的一处兼容性问题，并优化了性能。
--
-- 0.7.0: 定义 show_words_anyway、show_chars_anyway 和固词模式同时开启
-- 时的语义。
--
-- 0.6.1, 0.6.2: show_words_anyway 在四码时跳过二字词。
--
-- 0.6.0: 增加 show_chars_anyway 和 show_words_anyway 设置，允许将
-- fixed 码表的单字全码放置在第二位，不再需要打全码。如输入「jwrg」就
-- 可以在第二位得到「佳」，按分号选取之。
--
-- 0.5.2: 修复与 quick_code_hint 的兼容性问题。
--
-- 0.5.1: 修复「出简让全」的性能问题。
--
-- 0.5.0: 修复词库维护问题。使用简码键入的字的字频，不会被增加到
-- script translator 的用户词库中，导致长时间使用后，生僻字的字频反而
-- 更高，构词和整句会被干扰。
--
-- 在方案中引用时，需增加 @with_reorder 标记，并把
-- mohu_reorder_filter 添加为第一个 filter。
--
-- 0.4.2: 修复内存泄露。
--
-- 0.4.0: 增加词辅功能。
--
-- 0.3.2: 允许用户自定义出简让全的各项设置：是否启用、延迟几位候选、是
-- 否显示简快码提示。
--
-- 0.3.1: 允许自定义简快码提示符。
--
-- 0.3.0: 增加单字输出的出简让全。
--
-- 0.2.0: 增加固定二字词模式。
--
-- 0.1.0: 本翻译器用于解决 Rime 原生的翻译流程中，多翻译器会互相干扰、
-- 导致造词机能受损的问题。以「惊了」造词为例：用户输入 jym le，选择第
-- 一个字「惊」后，再选「了」字，这时候将无法造出「惊了」这个词。这是
-- 因为「了」是从码表翻译器输出的，在 script 翻译器的视角看来，并不知
-- 道用户输出了「惊了」两个字，所以造不出词。
--
-- 目前版本的解决方法是：用户选过字后，临时禁用 table 翻译器，使得
-- script 可以看到所有输入，从而解决造词问题。

local mohu = require("mohu")
local contextual = require("mohu_contextual_translator")
local top = {}

local kAny = 0
local kChar = 1
local kWord = 2

function top.init(env)
    -- Rime 组件
    contextual.init_runtime_pair(
        env,
        "multi_short_code",
        "table_translator@fixed",
        "table_translator@fixed_legacy"
    )
    contextual.init_pair(env, "smart")
    env.rfixed_cache = {}
    env.rfixed = function()
        local multi = not env.engine.context:get_option("multi_short_code")
        local key = multi and "legacy" or "unique"
        if not env.rfixed_cache[key] then
            local section = multi and "fixed_legacy" or "fixed"
            local dictionary = env.engine.schema.config:get_string(section .. "/dictionary")
                or (multi and "mohu_zrm_fixed_legacy" or "mohu_zrm_fixed")
            env.rfixed_cache[key] = ReverseLookup(dictionary)
        end
        return env.rfixed_cache[key]
    end

    -- 简快码相关配置项
    env.quick_code_indicator = env.engine.schema.config:get_string("mohu/quick_code_indicator") or "⚡️"
    env.quick_code_in_sentence_making = mohu.get_config_bool(env, "mohu/quick_code_in_sentence_making", true)
    if env.name_space == 'with_reorder' then
        -- `F 表示码表输出，会被 reorder_filter 重排
        env.quick_code_indicator = '`F'
    else
        -- 若不启用 reorder_filter，则不允许造句时产生码表输出
        env.quick_code_in_sentence_making = false
    end

    -- 出简让全相关配置项
    env.ijrq_enable = env.engine.schema.config:get_bool("mohu/ijrq/enable")
    env.ijrq_defer = env.engine.schema.config:get_int("mohu/ijrq/defer") or env.engine.schema.config:get_int("menu/page_size") or 5
    env.ijrq_hint = env.engine.schema.config:get_bool("mohu/ijrq/show_hint")
    env.ijrq_suffix = env.engine.schema.config:get_string("mohu/ijrq/suffix") or 'o'
    env.enable_word_filter = env.engine.schema.config:get_bool("mohu/enable_word_filter")
    env.word_filter_match_indicator = env.engine.schema.config:get_string("mohu/word_filter_match_indicator")
    env.inject_fixed_chars = env.engine.schema.config:get_bool("mohu/show_chars_anyway") or env.engine.schema.config:get_bool("mohu/inject_fixed_chars")
    env.inject_fixed_words = env.engine.schema.config:get_bool("mohu/show_words_anyway") or env.engine.schema.config:get_bool("mohu/inject_fixed_words")

    local inject_prioritize = env.engine.schema.config:get_string("mohu/inject_prioritize")
    if inject_prioritize == 'word' then
        env.inject_prioritize = kWord
    elseif inject_prioritize == 'char' then
        env.inject_prioritize = kChar
    else
        env.inject_prioritize = kAny
    end

    env.quick_code_indicator_skip_chars = env.engine.schema.config:get_bool("mohu/quick_code_indicator_skip_chars") or false

    -- output 状态
    env.output_i = 0
    env.output_injected_secondary = {}
end

function top.fini(env)
    contextual.fini_runtime_pair(env)
    contextual.fini_pair(env)
    env.rfixed = nil
    env.rfixed_cache = nil
    env.output_injected_secondary = nil
    collectgarbage()
end

function top.apply_word_filter_hint(cand, enabled, indicator)
    if not enabled then
        cand.comment = ""
    elseif indicator ~= nil then
        cand.comment = indicator
    end
end

function top.func(input, seg, env)
    top.output_begin(env)

    -- 每 10% 的翻译触发一次 GC
    if math.random() < 0.1 then
        collectgarbage()
    end

    local input_len = utf8.len(input)
    local inflexible = env.engine.context:get_option("inflexible")
    -- 简码提示开启时，出简让全不再重复输出简码 comment。
    local quick_code_hint = env.engine.context:get_option("quick_code_hint")
    local aux_hint = env.engine.context:get_option("aux_hint")
    local indicator = env.quick_code_indicator

    -- 用户尚未选过字时，调用码表。
    local is_sentence_making = not (env.engine.context.input == input)
    if not is_sentence_making or env.quick_code_in_sentence_making then
        local fixed_res = contextual.get_runtime(env):query(input, seg)
        -- 如果输入长度为 4，只输出 2 字词。
        if fixed_res ~= nil then
            if (input_len == 4) then
                if inflexible and env.inject_fixed_words and env.inject_fixed_chars then
                    -- 如果固词, inject_fixed_words 和 inject_fixed_chars 同时打开，则理解为挂接用法，直接输出码表。
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, true, function(_) return true end)
                elseif inflexible and env.inject_fixed_words then
                    -- 固词 + 长词 = 只有词
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, false, function(_) return true end)
                elseif inflexible and env.inject_fixed_chars then
                    -- 固词 + 单字 = 只有单字和二字词
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, true, function(len) return len == 2 end)
                elseif inflexible then
                    -- 如果只打开固词模式，则 *只* 优先输出 2 字词
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, false, function(len) return len == 2 end)
                else
                    -- 普通模式下，固定单字永远先于词语、英文和整句候选。
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, true, nil)
                end
            elseif input_len < 4 then          -- 造句模式下，只使用固定单字（词语无法固定）
                local words = nil
                if not is_sentence_making then
                    words = function(_) return true end
                end
                top.output_fixed_chars_first(env, fixed_res, is_sentence_making, true, words)
            elseif not is_sentence_making then  -- input_len > 4，输出所有
                for cand in fixed_res:iter() do
                    top.output_from_fixed(env, cand, is_sentence_making)
                end
            end
        end

    end

    local fixed_triggered = env.output_i > 0

    -- 注入到首选后的选项
    -- 目前的用例：在动词模式下处理 inject_fixed_chars 和 inject_fixed_words
    -- 注意，为了提高常规情况（inject_prioritize = kAny）的性能，
    -- (1) 在此种情况下，下面的代码会直接修改 env.output_injected_secondary
    -- (2) inject_prioritize != kAny 时会先把结果寄存在 inject_chars 和 inject_words 中
    --     在遍历完成后才得到 env.output_injected_secondary
    env.output_injected_secondary = {}
    local inject_has_priority = env.inject_prioritize and (env.inject_prioritize ~= kAny)
    local inject_chars = {}  -- valid only when inject_has_priority
    local inject_words = {}  -- valid only when inject_has_priority
    local num_injections = 0 -- valid only when inject_has_priority
    if (not fixed_triggered and input_len == 4) then
        for cand in mohu.query_translation(contextual.get_runtime(env), input, seg, nil) do
            local cand_len = utf8.len(cand.text)
            if (env.inject_fixed_chars and cand_len == 1) or (env.inject_fixed_words and cand_len > 2 and not is_sentence_making) then
                if cand_len ~= 1 or (cand_len == 1 and not env.quick_code_indicator_skip_chars) then
                    cand:get_genuine().comment = indicator
                end
                if not inject_has_priority then
                    table.insert(env.output_injected_secondary, cand)
                else
                    num_injections = num_injections + 1
                    if cand_len == 1 then
                        inject_chars[num_injections] = cand
                    else
                        inject_words[num_injections] = cand
                    end
                end
            end
        end
    end
    if inject_has_priority then
        if env.inject_prioritize == kChar then
            env.output_injected_secondary = top.append_lists(num_injections, inject_chars, inject_words)
        elseif env.inject_prioritize == kWord then
            env.output_injected_secondary = top.append_lists(num_injections, inject_words, inject_chars)
        else
            log.error("env.inject_prioritize has an invalid value: " .. tostring(env.inject_prioritize))
        end
    end

    -- 词辅在正常输出之前，以提高其优先级
    if env.enable_word_filter and (input_len == 5 or input_len == 7) then
        local real_input = input:sub(1, input_len - 1)
        local user_ac = input:sub(input_len, input_len)
        local iter = top.raw_query_smart(env, real_input, seg, true)
        for cand in iter do
            local len_match = (input_len == 7 and #cand.preedit == 8) or (input_len == 5 and #cand.preedit == 5)
            local idx = len_match and cand.comment:find(user_ac)
            local only_sp = (cand.preedit:sub(3,3) == ' ') and (#cand.preedit < 6 or cand.preedit:sub(6,6) == ' ')
            if only_sp and idx then
                cand._end = cand._end + 1
                cand.preedit = input
                top.apply_word_filter_hint(cand, aux_hint, env.word_filter_match_indicator)
                top.output(env, cand)
            end
            if #cand.preedit <= 2 then
                break
            end
        end
    end

    -- smart 在 fixed 之后输出。
    -- 当需要词辅时，保留 comment，以「提前」（用户输入词辅前）提示辅助码。
    local smart_iter = top.raw_query_smart(env, input, seg, env.enable_word_filter and aux_hint)
    if smart_iter ~= nil then
        local ijrq_enabled = env.ijrq_enable
            and (env.engine.context.input == input)
            and ((input_len == 4) or (input_len == 5 and input:sub(5,5) == env.ijrq_suffix))
        if not ijrq_enabled then
            -- 不启用出简让全时
            for cand in smart_iter do
                top.output(env, cand)
            end
        elseif input_len == 4 then
            local candidates = {}
            for cand in smart_iter do
                table.insert(candidates, cand)
            end
            local short_codes = {}
            local has_short_code = function(cand)
                if utf8.len(cand.text) ~= 1 then
                    return false
                end
                local fixed_codes = env.rfixed():lookup(cand.text)
                for code in fixed_codes:gmatch("%S+") do
                    if #code < 4 and string.sub(input, 1, #code) == code then
                        short_codes[cand] = code
                        return true
                    end
                end
                return false
            end
            local ordered = top.order_exact_four_candidates(
                candidates,
                has_short_code,
                env.ijrq_defer
            )
            for _, cand in ipairs(ordered) do
                local short_code = short_codes[cand]
                if short_code
                    and env.ijrq_hint
                    and cand.preedit:sub(1, 4) == input
                    and not quick_code_hint
                then
                    cand.comment = short_code
                end
                top.output(env, cand)
            end
        else
            -- 启用出简让全时
            local immediate_set = {}
            local deferred_set = {}
            for cand in smart_iter do
                local cand_len = utf8.len(cand.text)
                local defer = false
                -- 如果输出有词，说明在拼词，用户很可能要使用高频字，故此时停止出简让全。
                if (ijrq_enabled and cand_len > 1) then
                    ijrq_enabled = false
                end
                if (ijrq_enabled and cand_len == 1) then
                    local fixed_codes = env.rfixed():lookup(cand.text)
                    for code in fixed_codes:gmatch("%S+") do
                        if #code < 4
                            and string.sub(input, 1, #code) == code
                        then
                            defer = true
                            if env.ijrq_hint and cand.preedit:sub(1,4) == input:sub(1,4) and not quick_code_hint then
                                cand.comment = code
                            end
                            break
                        end
                    end
                end
                if (not defer) then
                    table.insert(immediate_set, cand)
                else
                    table.insert(deferred_set, cand)
                end
            end
            for i = 1, math.min(env.ijrq_defer, #immediate_set) do
                top.output(env, immediate_set[i])
            end
            for i = 1, #deferred_set do
                top.output(env, deferred_set[i])
            end
            for i = math.min(env.ijrq_defer, #immediate_set) + 1, #immediate_set do
                top.output(env, immediate_set[i])
            end
        end
    end

    -- 最后：如果 smart 输出为空，并且 fixed 之前没有调用过，此时再尝试调用一下
    if env.output_i == 0 then
        for cand in mohu.query_translation(contextual.get_runtime(env), input, seg, nil) do
            if not is_sentence_making or utf8.len(cand.text) == 1 then
                cand.comment = indicator
                yield(cand)
            end
        end
    end
end

---Order exact four-key smart candidates for character-level IJRQ.
---@param candidates table[]
---@param has_short_code fun(candidate: table): boolean
---@param defer_count integer
---@return table[]
function top.order_exact_four_candidates(candidates, has_short_code, defer_count)
    local ijrq_enabled = true
    local immediate_set = {}
    local deferred_set = {}

    for _, cand in ipairs(candidates) do
        local cand_len = utf8.len(cand.text)
        if ijrq_enabled and cand_len > 1 then
            ijrq_enabled = false
        end
        if ijrq_enabled and cand_len == 1 and has_short_code(cand) then
            table.insert(deferred_set, cand)
        else
            table.insert(immediate_set, cand)
        end
    end

    local result = {}
    local immediate_count = math.min(defer_count, #immediate_set)
    for index = 1, immediate_count do
        table.insert(result, immediate_set[index])
    end
    for _, cand in ipairs(deferred_set) do
        table.insert(result, cand)
    end
    for index = immediate_count + 1, #immediate_set do
        table.insert(result, immediate_set[index])
    end
    return result
end

-- | 每次 translation 开始前应该初始化 output 状态
function top.output_begin(env)
    env.output_i = 0
    env.output_injected_secondary = {}
end

-- | 支持候选注入的 yield
function top.output(env, cand)
    -- 注意：需要保证 spelling hint 仅对 3 字以下词开启
    yield(cand)
    env.output_i = env.output_i + 1
    if env.output_i == 1 then
        -- drain injected cands
        local cands = env.output_injected_secondary
        env.output_injected_secondary = {}
        for i, c in pairs(cands) do
            top.output(env, c)
        end
    end
end

function top.output_char_from_fixed(env, cand)
    if not env.quick_code_indicator_skip_chars then
        cand.comment = env.quick_code_indicator
    end
    top.output(env, cand)
end

function top.output_word_from_fixed(env, cand, is_sentence_making)
    if not is_sentence_making or utf8.len(cand.text) == 2 then
        cand.comment = env.quick_code_indicator
        top.output(env, cand)
    end
end

function top.output_from_fixed(env, cand, is_sentence_making)
    if utf8.len(cand.text) == 1 then
        top.output_char_from_fixed(env, cand)
    else
        top.output_word_from_fixed(env, cand, is_sentence_making)
    end
end

function top.output_fixed_chars_first(env, translation, is_sentence_making, include_chars, include_word)
    local chars = {}
    local words = {}
    for cand in translation:iter() do
        local cand_len = utf8.len(cand.text)
        if include_chars and cand_len == 1 then
            table.insert(chars, cand)
        elseif cand_len > 1 and include_word and include_word(cand_len) then
            table.insert(words, cand)
        end
    end
    for _, cand in ipairs(chars) do
        top.output_char_from_fixed(env, cand)
    end
    for _, cand in ipairs(words) do
        top.output_word_from_fixed(env, cand, is_sentence_making)
    end
end

-- | Query the smart translator for input, and transform the comment
-- | for candidates whose length is 2 or 3 characters long.
function top.raw_query_smart(env, input, seg, with_comment)
    local transform = function(cand)
        local cand_len = utf8.len(cand.text)
        if cand_len == 2 or cand_len == 3 then
            if with_comment then
                cand:get_genuine().comment = cand.comment:gsub("[a-z]+;([a-z])[a-z] ?", "%1")
            else
                cand:get_genuine().comment = ""
            end
        else
            cand:get_genuine().comment = ""
        end
        return cand
    end
    return mohu.query_translation(contextual.get(env), input, seg, transform)
end

-- Merge non-nil values in l1 and l2 into a new list.
function top.append_lists(max, l1, l2)
    local result = {}
    for i = 1, max do
        if l1[i] ~= nil then
            table.insert(result, l1[i])
        end
    end
    for i = 1, max do
        if l2[i] ~= nil then
            table.insert(result, l2[i])
        end
    end
    return result
end

return top

-- Local Variables:
-- lua-indent-level: 4
-- End:
