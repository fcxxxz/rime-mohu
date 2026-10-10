-- Limit long-word prefix completions without truncating the ordinary menu.
-- Part of Project Mohu; GPLv3.
-- Runs after explicit candidate management: pins, saved order and management
-- rows remain visible. Exact phrases, four-key abbreviations, partial words
-- and native sentences do not consume completion slots.
local F = {}

function F.init(env)
    local config = env.engine.schema.config
    local limit = config:get_int('mohu/word_completion_limit')
    if type(limit) ~= 'number' or limit ~= limit then limit = 3 end
    env.limit = math.max(-1, math.min(50, math.floor(limit)))
    env.pin_indicator = config:get_string('mohu/pin/indicator') or '📌'
end

function F.func(input, env)
    local seen, count = {}, 0
    for cand in input:iter() do
        local genuine = cand.get_genuine and cand:get_genuine() or cand
        local protected = cand.type == 'pinned' or genuine.type == 'pinned'
            or cand.type == 'mohu_reordered' or cand.type == 'mohu_hidden'
            or (env.pin_indicator ~= '' and type(cand.comment) == 'string'
                and cand.comment:sub(1, #env.pin_indicator) == env.pin_indicator)
        local completion = not protected and genuine.type == 'completion'
            and (utf8.len(cand.text) or 0) > 4
        if not completion or env.limit < 0 then
            yield(cand)
        elseif seen[cand.text] then
            if seen[cand.text] == 'visible' then yield(cand) end
        else
            count = count + 1
            seen[cand.text] = count <= env.limit and 'visible' or 'hidden'
            if count <= env.limit then yield(cand) end
        end
    end
end

return F
