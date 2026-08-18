package.path = "./lua/?.lua;" .. package.path

package.loaded.mohu = {
    codepoints = function(text)
        return utf8.codes(text)
    end,
    is_reverse_lookup = function()
        return false
    end,
    unicode_code_point_is_chinese = function()
        return true
    end,
}

local charset_filter = require("mohu_charset_filter")

local yielded = {}
yield = function(candidate)
    yielded[#yielded + 1] = candidate
end

local env = {
    charset = {
        lookup = function()
            return ""
        end,
    },
    memo = {},
    memo_cap = 10,
    exclude_charset = 0,
    engine = {
        context = {
            composition = {
                back = function()
                    return nil
                end,
            },
            get_option = function()
                return false
            end,
        },
    },
}

local function translation(candidate)
    return {
        iter = function()
            local emitted = false
            return function()
                if emitted then
                    return nil
                end
                emitted = true
                return candidate
            end
        end,
    }
end

local manager_candidate = { type = "mohu_manager_record_h", text = "寜" }
charset_filter.func(translation(manager_candidate), env)
assert(#yielded == 1 and yielded[1] == manager_candidate)

yielded = {}
charset_filter.func(translation({ type = "phrase", text = "寜" }), env)
assert(#yielded == 0)

print("charset filter manager passthrough: ok")
