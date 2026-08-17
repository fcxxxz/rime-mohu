local mohu = require("mohu")
local Top = {}

local MEMO = {}

local kNoExclude = 0
local kCharTrad = 1 -- t | f
local kCharSimp = 2 -- t | j
local kCharBoth = 3 -- t | f | j
local kCharExtended = 4

local function is_manager_candidate(cand)
    if cand.get_genuine then
        cand = cand:get_genuine()
    end
    return type(cand.type) == "string" and cand.type:sub(1, 13) == "mohu_manager_"
end

function Top.init(env)
    env.charset = ReverseLookup("mohu_charset")
    env.memo = MEMO
    env.memo_cap = 3000

    local charset = env.engine.schema.config:get_string("mohu/charset") or "both"
    if charset == "simp" then
        env.exclude_charset = kCharTrad
    elseif charset == "trad" then
        env.exclude_charset = kCharSimp
    else
        env.exclude_charset = kNoExclude
    end
end

function Top.fini(env)
    env.charset = nil
    env.memo = nil
    collectgarbage()
end

function Top.func(t_input, env)
    -- 以下情况不过滤：
    -- 1. 用户选择全集
    -- 2. charset 词典未加载成功
    -- 3. 部分反查情况
    -- 4. 使用 U 输入 Unicode 码
    local extended_charset = env.engine.context:get_option("extended_charset")
    if extended_charset or env.charset == nil or mohu.is_reverse_lookup(env) or Top.IsUnicodeInput(env) then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end

    -- 根据选项计算用户需要的字集（cs）
    for cand in t_input:iter() do
        if is_manager_candidate(cand) or Top.InCharset(env, cand.text, env.exclude_charset) then
            yield(cand)
            -- log.error("passed " .. cand.text)
        else
            -- log.error("filtered " .. cand.text)
        end
    end
end

-- For each Chinese char in text, if it is not in charset, return false.
function Top.InCharset(env, text, filter_cs)
    for i, codepoint in mohu.codepoints(text) do
        local char_cs = Top.CodepointInCharset(env, codepoint)
        if char_cs == kCharExtended -- Only show it in Unrestricted mode
            or char_cs == filter_cs  -- Should be filtered
        then
            return false
        end
    end
    return true
end

function Top.CodepointInCharset(env, codepoint)
    if env.memo[codepoint] ~= nil then
        return env.memo[codepoint]
    end
    if #env.memo > env.memo_cap then
        local cnt = 0
        for k, _ in pairs(env.memo) do
            env.memo[k] = nil
            cnt = cnt + 1
            if cnt >= env.memo_cap / 2 then
                break
            end
        end
    end
    if not mohu.unicode_code_point_is_chinese(codepoint) then
        return kCharBoth
    end
    local res = env.charset:lookup(utf8.char(codepoint))
    if res == nil or res == '' then
        res = kCharExtended
    elseif res == 't' then
        res = kCharBoth
    elseif res == 'f' then
        res = kCharTrad
    elseif res == 'j' then
        res = kCharSimp
    end
    env.memo[codepoint] = res
    return res
end

function Top.IsUnicodeInput(env)
    local seg = env.engine.context.composition:back()
    if not seg then
        return false
    end
    return seg:has_tag("unicode")
end

return Top

-- Local Variables:
-- lua-indent-level: 4
-- End:
