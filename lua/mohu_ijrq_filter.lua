-- mohu_ijrq_filter.lua 单字/词语出简让全（候选合并后统一执行）
--
-- Part of Project Mohu
-- License: GPLv3
-- Version: 0.4.0

-- ChangeLog:
--
-- 0.4.0: 合并后的完整单字候选统一出简让全，固定码表不再绕过；词语规则独立。
--
-- 0.3.3: 修复 0.3.2 中的一处 nil 引用问题。
--
-- 0.3.2: 修复 completion 误被延迟的问题。
--
-- 0.3.1: 修复单字可能被本滤镜让全的问题。
--
-- 0.3.0: 增加 enable_word_defer 选项，若首选应下沉，则移动首选到该数
--        目个候选之后。
--
-- 0.2.0: 增加 enable_word_delay 选项。若用户非常熟悉辅助码，可能会在
--        输入时直接打出辅助码，这时出简让全的效果反而不是用户期望的。
--
-- 0.1.0: 实作

local mohu = require("mohu")
local Module = {}

function Module.init(env)
    local config = env.engine.schema.config
    env.char_enabled = config:get_bool("mohu/ijrq/enable") == true
    env.char_defer = config:get_int("mohu/ijrq/defer") or config:get_int("menu/page_size") or 5
    if env.char_defer < 1 then env.char_defer = config:get_int("menu/page_size") or 5 end
    env.char_hint = config:get_bool("mohu/ijrq/show_hint") == true
    env.char_suffix = config:get_string("mohu/ijrq/suffix") or "o"
    env.pin_indicator = config:get_string("mohu/pin/indicator") or "📌"
    if env.char_enabled and type(ReverseLookup) == "function" then
        local dictionary = config:get_string("translator/dictionary") or "mohu_zrm"
        local ok, reverse = pcall(ReverseLookup, dictionary)
        if ok then env.char_reverse = reverse end
    end
    env.enabled = env.engine.schema.config:get_bool("mohu/ijrq/enable_word")
    env.delay = env.engine.schema.config:get_int("mohu/ijrq/enable_word_delay") or 0
    env.defer = env.engine.schema.config:get_int("mohu/ijrq/enable_word_defer") or 1

    if env.enabled and (type(env.defer) ~= "number" or env.defer % 1 ~= 0 or env.defer <= 0) then
        log.error("mohu/enable_word_defer is not an integer >= 1! ijrq_filter is automatically disabled.")
        env.enabled = false
    end

    -- Debouncer
    env.last_timestamp = 0
    if rime_api.get_time_ms == nil then
        env.get_time_ms = function()
            return 0
        end
    else
        env.get_time_ms = rime_api.get_time_ms
    end
end

function Module.fini(env)
    env.last_input = nil
    env.last_first_cand = nil
    env.char_reverse = nil
end

-- Streaming wrapper: defer complete single-character candidates regardless
-- of whether they came from the code table or smart. Partial selections and
-- explicit pins keep their position. The dictionary itself stays unchanged.
function Module.defer_full_code_characters(iter, env, raw)
    local deferred, memo, ordinary_seen = {}, {}, {}
    local ordinary_count, next_deferred = 0, 1
    local flushing, exhausted = false, false
    local function short_code(cand)
        local genuine = cand.get_genuine and cand:get_genuine() or cand
        local text = cand.text
        if utf8.len(text) ~= 1 or cand.type == "pinned" or genuine.type == "pinned" then return nil end
        if type(cand.comment) == "string" and env.pin_indicator ~= "" and
            cand.comment:sub(1, #env.pin_indicator) == env.pin_indicator then return nil end
        local finish = tonumber(cand._end or genuine._end) or #raw
        local start = tonumber(cand.start or genuine.start) or 0
        if start ~= 0 or finish ~= #raw then return nil end
        if memo[text] ~= nil then return memo[text] or nil end
        local codes = env.char_reverse:lookup(text) or ""
        local found = false
        for code in codes:gmatch("%S+") do
            if #code < 4 and raw:sub(1, #code) == code and
                (not found or #code < #found) then found = code end
        end
        memo[text] = found
        return found or nil
    end
    return function()
        while true do
            if flushing or exhausted then
                local cand = deferred[next_deferred]
                if cand then
                    next_deferred = next_deferred + 1
                    return cand
                end
                deferred, next_deferred, flushing = {}, 1, false
                if exhausted then return nil end
            end
            local cand = iter()
            if not cand then
                exhausted = true
            else
                local short = short_code(cand)
                if short then
                    if env.char_hint and not env.engine.context:get_option("quick_code_hint") then
                        cand.comment = short
                    end
                    if ordinary_count < env.char_defer then
                        deferred[#deferred + 1] = cand
                    else
                        return cand
                    end
                else
                    -- uniquifier runs later; count distinct visible text so
                    -- duplicate streams cannot release a character too early.
                    if not ordinary_seen[cand.text] then
                        ordinary_seen[cand.text] = true
                        ordinary_count = ordinary_count + 1
                    end
                    if ordinary_count >= env.char_defer and #deferred > 0 then flushing = true end
                    return cand
                end
            end
        end
    end
end

function Module.func(t_input, env)
    if not env.enabled and (not env.char_enabled or not env.char_reverse) then
        for cand in t_input:iter() do yield(cand) end
        return
    end
    if mohu.is_reverse_lookup(env) then
        for cand in t_input:iter() do yield(cand) end
        return
    end
    local context = env.engine.context
    local input = context.input
    local input_len = utf8.len(input)
    local iter = mohu.iter_translation(t_input)
    if env.char_enabled and env.char_reverse and
        (input_len == 4 or (input_len == 5 and input:sub(5) == env.char_suffix)) then
        iter = Module.defer_full_code_characters(iter, env, input)
    end
    if not env.enabled then
        mohu.yield_all(iter)
        return
    end

    -- The idea is to save the first cand when we first reach len=4.
    -- 1) lmjx 1. 链接
    -- 2) lmjxf 链接 -- same as lmjx, so postpone it -> 1. 连接 2. 链接
    -- 3) lmjxfxxx -- write something more
    -- 4) lmjxf -- remove xxx and get lmjxf again, we should keep the candidate list stable

    -- but we can only postpone inside the same GROUP
    -- e.g. uixmw should output 实现, as there is no other words have the same code
    -- a GROUP can be defined as (1) same text length (2) same code length

    if input_len == 4 or input_len == 6 or input_len == 8 then
        local first_cand = iter()
        if not first_cand then
            return
        end
        env.last_input = input
        env.last_first_cand = first_cand:get_genuine().text
        env.last_timestamp = env.get_time_ms()
        yield(first_cand)
        mohu.yield_all(iter)
    elseif input_len == 5 or input_len == 7 or input_len == 9 then
        local iter = mohu.make_peekable(iter)
        local first = iter:peek()
        if not first then  -- No candidates
            return
        end
        local genuine = first.get_genuine and first:get_genuine() or first
        if first.type == "pinned" or genuine.type == "pinned" then
            mohu.yield_all(iter)
            return
        end
        local last_len = utf8.len(env.last_first_cand)
        local first_len = utf8.len(first.text)
        if input:sub(1,input_len-1) ~= env.last_input or last_len == 1 or last_len ~= first_len then
            mohu.yield_all(iter)
            return
        end

        -- If the user types auxcode super fast, then we should NOT
        -- attempt to postpone first cand.
        if not Module.debounce(env) then
            mohu.yield_all(iter)
            return
        end

        -- FIXME: The code is a mess.
        -- Extend Yielder to support "floating" defer.
        local postpone = false
        local initset = {}
        local pset = {}
        local defer = env.defer - 1
        for c in iter do
            -- a genuine cand may generate multiple cands
            local g = c:get_genuine()
            if g.text == env.last_first_cand then
                table.insert(pset, c)
            elseif defer > 0 then
                table.insert(initset, c)
                defer = defer - 1
            elseif defer == 0 then
                table.insert(initset, c)
                break
            end
        end

        -- yield candidates in the same group as last_first_cand
        local skipped = 0
        for i, c in ipairs(initset) do
            if utf8.len(c.text) == utf8.len(env.last_first_cand) then
                -- same group, yield first
                yield(c)
                skipped = skipped + 1
                initset[i] = nil
            else
                break
            end
        end

        -- yield deferred candidates
        for _,c in pairs(pset) do
            yield(c)
        end

        -- yield other candidates
        for i = skipped + 1, #initset do
            yield(initset[i])
        end
        for c in iter do
            yield(c)
        end
        

        -- if postpone then
        --    if real_first_cand then yield(real_first_cand) end
        --    for _,c in pairs(pset) do yield(c) end
        --    for c in iter do yield(c) end
        -- else
        --    for _,c in pairs(pset) do yield(c) end
        --    if real_first_cand then yield(real_first_cand) end
        --    for c in iter do yield(c) end
        -- end
    else
        mohu.yield_all(iter)
    end
end

--| Returns true if the current invocation is a new invocation
-- (i.e. not too close to the last invocation).
function Module.debounce(env)
    if env.delay == nil or env.delay < 1 then
        return true
    end
    local cur = env.get_time_ms()
    local last = env.last_timestamp
    env.last_timestamp = cur
    return cur - last >= env.delay
end

return Module

-- Local Variables:
-- lua-indent-level: 4
-- End:
