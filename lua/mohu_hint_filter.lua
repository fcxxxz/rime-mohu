-- Mohu Translator (for Express Editor)
-- Copyright (c) 2023-2026 ksqsf
--
-- Ver: 0.4.1
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 0.4.1: 修复 aux_table 格式未适配问题。
--
-- 0.4.0: 适配多字词重排。
--
-- 0.3.0: 增加 quick_code_hint_indicator 选项
--
-- 0.2.0: 若开启 inject_fixed_words ，则提示长词
--
-- 0.1.0: 合并原 mohu_aux_hint 和 mohu_quick_code_hint
--
local mohu = require("mohu")
local Module = {}

function Module.init(env)
    env.aux_table = nil
    env.aux_table_load_attempted = false

    env.is_auxfilter = env.name_space == "auxfilter"
    env.aux_priority_indicator = env.engine.schema.config:get_string("mohu/aux_priority_indicator") or "▾"
    env.quick_code_hint_dictionary = env.engine.schema.config:get_string("mohu/quick_code_hint_dictionary")
        or env.engine.schema.config:get_string("fixed/dictionary")
        or env.engine.schema.config:get_string("translator/dictionary")
    env.quick_code_hint_reverse = nil
    env.quick_code_hint_skip_chars = env.engine.schema.config:get_bool("mohu/quick_code_hint_skip_chars") or false
    env.quick_code_hint_indicator = env.engine.schema.config:get_string("mohu/quick_code_hint_indicator")
    if env.quick_code_hint_indicator == nil then
        env.quick_code_hint_indicator = env.engine.schema.config:get_string("mohu/quick_code_indicator")
    end
    if env.quick_code_hint_indicator == nil then
        env.quick_code_hint_indicator = "⚡"
    end

    -- 若开启 inject_fixed_words，则可以提示长词（>=3）
    env.inject_fixed_words = env.engine.schema.config:get_bool("mohu/inject_fixed_words") or false
    -- NOTE: 暂不提示二字词
end

function Module.fini(env)
    env.aux_table = nil
    env.aux_table_load_attempted = false
    env.aux_priority_indicator = nil
    env.quick_code_hint_dictionary = nil
    env.quick_code_hint_reverse = nil
    collectgarbage()
end

function Module.get_auxcode_hint(env, cand, gcand, enabled)
    if not enabled or not env.aux_table then
        return nil
    end
    local text = gcand.text
    local len = utf8.len(text)
    if len == 1 then
        local cp = utf8.codepoint(text)
        local codes = env.aux_table[cp]
        if not codes then
            return nil
        end
        return codes:sub(2)
    elseif len ~= 1 and env.is_auxfilter and (gcand.type == "phrase" or gcand.type == "user_phrase") then
        result = ""
        for i, cp in mohu.codepoints(gcand.text) do
            local cpaux = env.aux_table[cp]
            if cpaux and #cpaux > 0 then
                cpaux = cpaux:match("[a-z]+")  -- 取第一个
                if result == "" then
                    result = cpaux
                else
                    result = result .. ' ' .. cpaux
                end
            else
                return nil
            end
        end
        if #result == 0 then
            return nil
        end
        return result
    else
        return nil
    end
end

function Module.strip_aux_match_comment(comment, priority_indicator)
    local _, aux_end = comment:find("^[a-z]+")
    if not aux_end then
        return comment, false
    end
    local remainder = comment:sub(aux_end + 1)
    if #priority_indicator > 0 and remainder:sub(1, #priority_indicator) == priority_indicator then
        remainder = remainder:sub(#priority_indicator + 1)
    end
    if remainder == "" then
        return "", true
    end
    local separator = " ¦ "
    if remainder:sub(1, #separator) == separator then
        return remainder:sub(#separator + 1), true
    end
    return comment, false
end

function Module.get_quickcode_hint(env, cand, gcand)
    if not env.quick_code_hint_reverse then
        return nil
    end
    local text = gcand.text
    local len = utf8.len(text)
    if len == 1 and env.quick_code_hint_skip_chars then
        return nil
    end
    local all_codes = env.quick_code_hint_reverse:lookup(text)
    if not all_codes then
        return nil
    end
    local in_use = false
    local codes = {}
    for code in all_codes:gmatch("%S+") do
        if #code < 4 or (env.inject_fixed_words and len >= 3) then
            if code == cand.preedit:gsub("%s", "") then
                in_use = true
            else
                table.insert(codes, code)
            end
        end
    end
    if #codes == 0 and not in_use then
        return nil
    end
    local codes_hint = table.concat(codes, " ")
    if #codes_hint == 0 then
        return nil
    end
    return codes_hint
end

function Module.func(translation, env)
    local enable_aux_hint = env.engine.context:get_option("aux_hint")
    if enable_aux_hint and not env.aux_table_load_attempted then
        env.aux_table = mohu.load_zrmdb()
        env.aux_table_load_attempted = true
    end
    enable_aux_hint = enable_aux_hint and env.aux_table ~= nil

    local enable_quick_code_hint = env.engine.context:get_option("quick_code_hint")
    if enable_quick_code_hint and not env.quick_code_hint_reverse and env.quick_code_hint_dictionary then
        env.quick_code_hint_reverse = ReverseLookup(env.quick_code_hint_dictionary)
    end

    if not enable_aux_hint and not enable_quick_code_hint and not env.is_auxfilter then
        for cand in translation:iter() do
            yield(cand)
        end
        return
    end

    local major_sep = " ¦ "
    local minor_sep = env.quick_code_hint_indicator
    if #minor_sep == 0 then
        minor_sep = major_sep
    end
    for cand in translation:iter() do
        if cand.type == "punct" then
            yield(cand)
            goto continue
        end
        local gcand = cand:get_genuine()
        local auxhint = Module.get_auxcode_hint(env, cand, gcand, enable_aux_hint)
        local qchint = nil
        if enable_quick_code_hint then
            qchint = Module.get_quickcode_hint(env, cand, gcand)
        end
        if env.is_auxfilter and not enable_aux_hint then
            local display_comment, hidden = Module.strip_aux_match_comment(gcand.comment, env.aux_priority_indicator)
            if hidden then
                if qchint then
                    if #display_comment == 0 then
                        display_comment = env.quick_code_hint_indicator .. qchint
                    else
                        display_comment = display_comment .. major_sep .. env.quick_code_hint_indicator .. qchint
                    end
                end
                yield(ShadowCandidate(cand, cand.type, cand.text, display_comment, false))
                goto continue
            end
        end
        if auxhint and qchint then
            local hint = nil
            if env.is_auxfilter then
                hint = auxhint .. major_sep .. env.quick_code_hint_indicator .. qchint
            else
                hint = auxhint .. minor_sep .. qchint
            end
            if #gcand.comment == 0 or gcand.comment == env.quick_code_hint_indicator then
                gcand.comment = hint
            else
                gcand.comment = gcand.comment .. major_sep .. hint
            end
        elseif auxhint then
            if not env.is_auxfilter and #gcand.comment == 0 then
                -- 单字，不额外添加 sep
                -- 同时包括了 quick_code_hint_indicator == "" 情况
                gcand.comment = auxhint
            elseif not env.is_auxfilter and (gcand.comment == env.quick_code_hint_indicator) then
                -- 单字，已有 sep ，把 hint 添加到 sep 前面
                gcand.comment = auxhint .. gcand.comment
            else
                -- 辅筛模式，不论单字还是词组都加上 ¦
                gcand.comment = gcand.comment .. major_sep .. auxhint
            end
        elseif qchint then
            if #gcand.comment == 0 then
                gcand.comment = gcand.comment .. env.quick_code_hint_indicator .. qchint
            elseif gcand.comment == env.quick_code_hint_indicator then
                -- 已有 sep ，不再加
                gcand.comment = gcand.comment .. qchint
            else
                gcand.comment = gcand.comment .. major_sep .. env.quick_code_hint_indicator .. qchint
            end
        end
        yield(cand)
        ::continue::
    end
end

return Module

-- Local Variables:
-- lua-indent-level: 4
-- End:
