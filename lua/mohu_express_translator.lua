-- Mohu Translator (for Express Editor)
-- Copyright (c) 2023, 2024, 2025, 2026 ksqsf
--
-- Ver: 0.18.1
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 0.18.1: 显式全码 5 键（yyxx+o）：未整段消费输入的候选（音节桶单字）
--         不再参与出简让全位移，统一排在让全字之后（华南虎 mywjo→命
--         第 1；zrm jwwjo→家、lgwjo→冷同步修复）。4 键行为不变。
--
-- 0.18.0: 4 键读音在词库没有任何二字词条时，smart 层的单字拼装候选
-- 让出首选给注入的四码简词（读音有词的码位一律不换，无论该词是不是
-- 首选；注入列表混有出简字的码位也不换）。词条判定用
-- lua/<dict>.word2_codes.txt（make dict 生成），资产缺失时规则自动关闭。
--
-- 0.17.0: 混合码位（竞争字与二字词同码）词行走 smart 动态排序、可调频；
-- 行序只决定字的落位（词前字固顶、词后字保次选）。纯字码位仍按主表
-- 行序块输出。简快码/让位类候选不参与调频的语义不变。
--
-- 0.16.0: 撞码判定排除出简让全的单字行：短码是当前输入前缀的字已被
-- IJRQ 让全、不在码位顶部竞争，不再冻结二字词行序；这类码位回到
-- smart 权重序（可调频）。真撞码（庳 bixu 类无更短码）行为不变。
--
-- 0.15.0: 四码单字与二字词撞码时直接采用主码表行序；三字以上简词仍为
-- 次位补充候选。没有单字撞码时保留 smart 调频与缺词补出，不再读取避让表。
--
-- 0.13.0: 四码普通模式的固顶字词排序仍按旧的字频/让位机制处理。
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
-- 主字词表配置统一使用 inject_table_chars / inject_table_words。
-- 旧版注入配置别名已移除，不再维护两套命名。
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

local semantic_meta = nil
do
    local ok, module = pcall(require, "mohu_semantic_meta")
    if ok and type(module) == "table" and type(module.bind) == "function" then
        semantic_meta = module
    end
end

-- 语义谱系：fixed/smart 候选在离开查询边界前绑定不可变来源事实。
-- 绑定失败不影响候选输出；二次绑定（缓冲重放）被静默忽略。
local function lexical_translator_name(env, translator)
    if translator ~= nil and translator == env.static_translator then
        return "smart_static"
    end
    if translator ~= nil and translator == env.code_table then
        return "code_table"
    end
    return "smart"
end

local function bind_lexical_provenance(env, cand, source, translator_name)
    if semantic_meta == nil then
        return
    end
    local genuine_type = nil
    local dynamic_type = nil
    if cand.get_genuine ~= nil then
        local ok, genuine = pcall(cand.get_genuine, cand)
        if ok and genuine ~= nil and type(genuine.type) == "string" then
            genuine_type = genuine.type
        end
    end
    if cand.get_dynamic_type ~= nil then
        local ok, dynamic = pcall(cand.get_dynamic_type, cand)
        if ok and type(dynamic) == "string" then
            dynamic_type = dynamic
        end
    end
    pcall(semantic_meta.bind, cand, {
        provenance_version = "mohu-lexical/v1",
        source = source,
        candidate_type = cand.type,
        genuine_type = genuine_type,
        dynamic_type = dynamic_type,
        lexical_translator = translator_name,
        native_score_kind = "unavailable_rime_lexical",
        query_preedit = cand.preedit,
    })
end

local kAny = 0
local kChar = 1
local kWord = 2

function top.init(env)
    -- Rime 组件
    env.code_table = Component.Translator(env.engine, "", "table_translator@translator")
    contextual.init_pair(env, "smart")
    local dictionary = env.engine.schema.config:get_string("translator/dictionary") or "mohu_zrm"
    env.code_table_reverse = ReverseLookup(dictionary)
    env.rfixed = function() return env.code_table_reverse end
    env.word2_dictionary = dictionary

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
    env.inject_table_chars = env.engine.schema.config:get_bool("mohu/inject_table_chars")
    env.inject_table_words = env.engine.schema.config:get_bool("mohu/inject_table_words")

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
    env.code_table = nil
    contextual.fini_pair(env)
    env.rfixed = nil
    env.code_table_reverse = nil
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
    local smart_iter

    -- 用户尚未选过字时，调用码表。
    local is_sentence_making = not (env.engine.context.input == input)
    if not is_sentence_making or env.quick_code_in_sentence_making then
        local fixed_res = env.code_table:query(input, seg)
        -- 如果输入长度为 4，只输出 2 字词。
        if fixed_res ~= nil then
            if (input_len == 4) then
                if inflexible and env.inject_table_words and env.inject_table_chars then
                    -- 如果固词, inject_table_words 和 inject_table_chars 同时打开，则理解为挂接用法，直接输出码表。
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, true, function(_) return true end)
                elseif inflexible and env.inject_table_words then
                    -- 固词 + 长词 = 只有词
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, false, function(_) return true end)
                elseif inflexible and env.inject_table_chars then
                    -- 固词 + 单字 = 只有单字和二字词
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, true, function(len) return len == 2 end)
                elseif inflexible then
                    -- 如果只打开固词模式，则 *只* 优先输出 2 字词
                    top.output_fixed_chars_first(env, fixed_res, is_sentence_making, false, function(len) return len == 2 end)
                else
                    local rows, fixed_words = {}, {}
                    for cand in fixed_res:iter() do
                        rows[#rows + 1] = cand
                        if utf8.len(cand.text) == 2 then fixed_words[#fixed_words + 1] = cand end
                    end
                    if not top.output_four_code_collision(env, rows, is_sentence_making, input) and #fixed_words > 0 then
                        -- No character collision: retain smart ranking, while
                        -- keeping maintained aliases such as 几乎=johu visible.
                        local buffer
                        buffer, smart_iter = top.peek_smart_prefix(env, input, seg, env.enable_word_filter and aux_hint)
                        for _, cand in ipairs(top.collect_missing_fixed_words(fixed_words, buffer)) do
                            bind_lexical_provenance(env, cand, "fixed", lexical_translator_name(env, env.code_table))
                            top.output_word_from_fixed(env, cand, is_sentence_making)
                        end
                        if env.inject_table_words and env.output_i > 0 and not is_sentence_making then
                            for _, cand in ipairs(rows) do
                                if utf8.len(cand.text) > 2 then
                                    bind_lexical_provenance(env, cand, "fixed", lexical_translator_name(env, env.code_table))
                                    cand.comment = indicator
                                    top.output(env, top.secondary_word(cand))
                                end
                            end
                        end
                    end
                end
            elseif input_len < 4 then
                -- Exact short-code candidates follow the editable table, including
                -- word-before-character rows. During partial sentence selection,
                -- retain the existing character-only restriction.
                top.output_table_order(env, fixed_res, is_sentence_making)
            elseif not is_sentence_making then
                top.output_table_order(env, fixed_res, false)
            end
        end

    end

    local fixed_triggered = env.output_i > 0

    -- 注入到首选后的选项
    -- 目前的用例：在动词模式下处理 inject_table_chars 和 inject_table_words
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
        local fixed_name = lexical_translator_name(env, env.code_table)
        for cand in mohu.query_translation(env.code_table, input, seg, nil) do
            bind_lexical_provenance(env, cand, "fixed", fixed_name)
            local cand_len = utf8.len(cand.text)
            if (env.inject_table_chars and cand_len == 1) or (env.inject_table_words and cand_len > 2 and not is_sentence_making) then
                if cand_len ~= 1 or (cand_len == 1 and not env.quick_code_indicator_skip_chars) then
                    cand:get_genuine().comment = indicator
                end
                local injected = cand_len > 2 and top.secondary_word(cand) or cand
                if not inject_has_priority then
                    table.insert(env.output_injected_secondary, injected)
                else
                    num_injections = num_injections + 1
                    if cand_len == 1 then
                        inject_chars[num_injections] = injected
                    else
                        inject_words[num_injections] = injected
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
    -- 缺词检查若预取了 smart 流，继续重放同一查询。
    if smart_iter == nil then
        smart_iter = top.raw_query_smart(env, input, seg, env.enable_word_filter and aux_hint)
    end
    if smart_iter ~= nil then
        if top.composition_yields_to_secondary(env, input) then
            local cands = env.output_injected_secondary
            env.output_injected_secondary = {}
            for _, c in ipairs(cands) do
                top.output(env, c)
            end
        end
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
                local code = top.char_short_code_prefix(env, cand.text, input)
                if code then
                    short_codes[cand] = code
                    return true
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
            -- 显式全码 5 键（yyxx+suffix）：未整段消费输入的候选（音节桶
            -- 溢出的单字/词语，preedit 只覆盖前 2-4 键）不参与让全位移，
            -- 且排在让全字之后——显式全码针对的是精确码位，音节填充词
            -- 不应把目标字顶下去（华南虎 mywjo→命、zrm jwwjo→家）。
            local partial_set = {}
            for cand in smart_iter do
                local cand_len = utf8.len(cand.text)
                local defer = false
                local genuine = cand.get_genuine and cand:get_genuine() or cand
                local finish = tonumber(cand._end or genuine._end) or #input
                local partial = finish ~= #input
                -- 如果输出有词，说明在拼词，用户很可能要使用高频字，故此时停止出简让全。
                if (not partial and ijrq_enabled and cand_len > 1) then
                    ijrq_enabled = false
                end
                if (not partial and ijrq_enabled and cand_len == 1) then
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
                if partial then
                    table.insert(partial_set, cand)
                elseif (not defer) then
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
            for i = 1, #partial_set do
                top.output(env, partial_set[i])
            end
        end
    end

    -- 最后：如果 smart 输出为空，并且 fixed 之前没有调用过，此时再尝试调用一下
    if env.output_i == 0 then
        local fixed_name = lexical_translator_name(env, env.code_table)
        for cand in mohu.query_translation(env.code_table, input, seg, nil) do
            bind_lexical_provenance(env, cand, "fixed", fixed_name)
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

---Return exact two-character fixed-code words absent from the smart prefix.
---The helper is pure so the short-code fallback can be regression-tested
---without constructing a Rime Translation userdata stream.
function top.collect_missing_fixed_words(fixed_words, smart_candidates)
    local smart_texts = {}
    for _, cand in ipairs(smart_candidates or {}) do
        smart_texts[cand.text] = true
    end
    local missing = {}
    for _, cand in ipairs(fixed_words or {}) do
        if not smart_texts[cand.text] then
            missing[#missing + 1] = cand
        end
    end
    return missing
end

---Replay buffered candidates first, then resume the remaining stream.
---@param buffer table
---@param iter function
---@return function
function top.chain_candidates(buffer, iter)
    local index = 0
    return function()
        index = index + 1
        if index <= #buffer then
            return buffer[index]
        end
        return iter()
    end
end

---Buffer the smart prefix once for maintained word-alias fallback.
---Return the prefix and an iterator that replays it before resuming.
---@param env table
---@param input string
---@param seg table
---@param with_comment boolean
---@return table, function|nil
function top.peek_smart_prefix(env, input, seg, with_comment)
    local buffer = {}
    local iter = top.raw_query_smart(env, input, seg, with_comment)
    if iter == nil then return buffer, nil end
    for _ = 1, 20 do
        local cand = iter()
        if cand == nil then break end
        buffer[#buffer + 1] = cand
    end
    return buffer, top.chain_candidates(buffer, iter)
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
        for _, c in ipairs(cands) do
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

-- Candidate userdata may be rewrapped between translators and filters, so
-- Lua weak-key provenance cannot carry this runtime positioning contract.
-- Keep the original table candidate as genuine; IJRQ removes the outer marker
-- before explicit candidate management and display.
function top.secondary_word(cand)
    return ShadowCandidate(cand, "mohu_secondary_word", cand.text, cand.comment, false)
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

function top.output_table_order(env, translation, is_sentence_making)
    local name = lexical_translator_name(env, env.code_table)
    for cand in translation:iter() do
        if not is_sentence_making or utf8.len(cand.text) == 1 then
            bind_lexical_provenance(env, cand, "fixed", name)
            top.output_from_fixed(env, cand, is_sentence_making)
        end
    end
end

-- Exact character/word collisions use one authority: table row order.
-- Longer abbreviations remain secondary and never take a primary slot.
---Return the character's short code when it prefixes the current input,
---i.e. the exact condition IJRQ uses to yield a full-code char (出简让全).
---@param env table
---@param char string
---@param input string
---@return string|nil
function top.char_short_code_prefix(env, char, input)
    if not input or not env.rfixed then return nil end
    local fixed_codes = env.rfixed():lookup(char)
    for code in fixed_codes:gmatch("%S+") do
        if #code < 4 and string.sub(input, 1, #code) == code then
            return code
        end
    end
    return nil
end

function top.output_four_code_collision(env, rows, is_sentence_making, input)
    -- 撞码只算竞争单字：出简让全的字（有 <4 码且为当前输入前缀）不在
    -- 码位顶部竞争，不参与撞码判定。
    local competing = {}
    local first_word_index = nil
    for i, cand in ipairs(rows) do
        local cand_len = utf8.len(cand.text)
        if cand_len == 1 then
            if top.char_short_code_prefix(env, cand.text, input) == nil then
                competing[#competing + 1] = i
            end
        elseif cand_len == 2 and first_word_index == nil then
            first_word_index = i
        end
    end
    if #competing == 0 then return false end
    if first_word_index ~= nil then
        -- 混合码位（竞争字与二字词同码）：词行走 smart 动态排序、可调频，
        -- 行序只决定字的落位——词行前的字固顶输出；词行后的字保次选。
        -- 未固顶时不在本函数注入，交由既有首选后注入块统一处理（含
        -- 出简字与简快词），避免重复；固顶后注入块被 fixed_triggered
        -- 跳过，尾随字在此排队。
        local name = lexical_translator_name(env, env.code_table)
        local led = false
        for _, i in ipairs(competing) do
            local cand = rows[i]
            bind_lexical_provenance(env, cand, "fixed", name)
            if i < first_word_index then
                top.output_from_fixed(env, cand, is_sentence_making)
                led = true
            elseif led then
                env.output_injected_secondary[#env.output_injected_secondary + 1] = cand
            end
        end
        return false
    end
    -- 纯字码位（无二字词行）：保持主表行序块输出。
    local primary, secondary = {}, {}
    local name = lexical_translator_name(env, env.code_table)
    for _, cand in ipairs(rows) do
        bind_lexical_provenance(env, cand, "fixed", name)
        if utf8.len(cand.text) <= 2 then
            primary[#primary + 1] = cand
        elseif env.inject_table_words and not is_sentence_making then
            secondary[#secondary + 1] = cand
        end
    end
    for _, cand in ipairs(secondary) do
        cand.comment = env.quick_code_indicator
        env.output_injected_secondary[#env.output_injected_secondary + 1] = top.secondary_word(cand)
    end
    for _, cand in ipairs(primary) do
        top.output_from_fixed(env, cand, is_sentence_making)
    end
    return true
end

function top.output_fixed_chars_first(env, translation, is_sentence_making, include_chars, include_word)
    local chars = {}
    local words = {}
    local fixed_name = lexical_translator_name(env, env.code_table)
    for cand in translation:iter() do
        bind_lexical_provenance(env, cand, "fixed", fixed_name)
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
-- 4 键读音在词库没有任何二字词条、主表也没有基础行时，smart 层的首个
-- 候选必然是单字拼装而非词条，此时让注入的四码简词先出（2026-10-13
-- 定案：读音有词的码位一律不换，无论该词是不是首选；注入列表混有
-- 单字——出简让全的字——的码位不换，保持既有让位语义）。
function top.composition_yields_to_secondary(env, input)
    if env.output_i ~= 0 or type(input) ~= "string" or #input ~= 4 then
        return false
    end
    if not env.word2_dictionary then
        return false
    end
    local cands = env.output_injected_secondary
    if cands == nil or #cands == 0 then
        return false
    end
    for _, c in ipairs(cands) do
        if utf8.len(c.text) < 3 then
            return false
        end
    end
    return not mohu.word2_codes_has_entry(env.word2_dictionary, input)
end

function top.raw_query_smart(env, input, seg, with_comment)
    local translator = contextual.get_for_input(env, input)
    local translator_name = lexical_translator_name(env, translator)
    local transform = function(cand)
        bind_lexical_provenance(env, cand, "smart", translator_name)
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
    return mohu.query_translation(translator, input, seg, transform)
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
