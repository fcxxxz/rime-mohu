-- Contributed to Project Mohu by jack2game (https://github.com/ksqsf/rime-mohu/pull/61)
-- Unicode
-- 复制自： https://github.com/shewer/librime-lua-script/blob/main/lua/component/unicode.lua
-- 示例：输入 U62fc 得到「拼」
-- 触发前缀默认为 recognizer/patterns/unicode 的第 2 个字符，即 U
-- 2024.02.26: 限定编码最大值

local semantic_meta = nil
do
    local ok, module = pcall(require, "mohu_semantic_meta")
    if ok and type(module) == "table" and type(module.bind) == "function" then
        semantic_meta = module
    end
end

local function bind_unicode_provenance(candidate, code_point)
    if semantic_meta == nil then
        return
    end
    pcall(semantic_meta.bind, candidate, {
        provenance_version = "mohu-unicode/v1",
        source = "unicode",
        candidate_type = "unicode",
        protected = true,
        synthetic = true,
        native_score_kind = "unavailable",
        code_point = code_point,
    })
end

local function unicode(input, seg, env)
    -- 获取 recognizer/patterns/unicode 的第 2 个字符作为触发前缀
    env.unicode_keyword = env.unicode_keyword or
        env.engine.schema.config:get_string('recognizer/patterns/unicode'):sub(2, 2) or 'U'
    if seg:has_tag("unicode") and env.unicode_keyword ~= '' and input:sub(1, 1) == env.unicode_keyword then
        local ucodestr = input:match(env.unicode_keyword .. "(%x+)")
        if ucodestr and #ucodestr > 1 then
            local code = tonumber(ucodestr, 16)
            if code > 0x10FFFF then
                local overflow = Candidate("unicode", seg.start, seg._end, "数值超限！", "")
                bind_unicode_provenance(overflow, nil)
                yield(overflow)
                return
            end
            local text = utf8.char(code)
            local primary = Candidate("unicode", seg.start, seg._end, text, string.format("U+%x", code))
            bind_unicode_provenance(primary, code)
            yield(primary)
            if code < 0x10000 then
                for i = 0, 15 do
                    local text = utf8.char(code * 16 + i)
                    local near = Candidate("unicode", seg.start, seg._end, text, string.format("U+%x~%x", code, i))
                    bind_unicode_provenance(near, code * 16 + i)
                    yield(near)
                end
            end
        end
    end
end

return unicode
