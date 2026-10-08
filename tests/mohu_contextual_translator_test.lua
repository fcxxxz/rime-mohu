package.path = "lua/?.lua;" .. package.path

local created = {}
local queried = {}
local yielded = {}
local history_pushes = {}
local commit_callback = nil
local commit_connection_disconnected = false

Component = {
    Translator = function(_, _, name)
        table.insert(created, name)
        return {
            name = name,
            query = function()
                queried[name] = (queried[name] or 0) + 1
                local done = false
                return {
                    iter = function()
                        return function()
                            if done then return nil end
                            done = true
                            return { text = name }
                        end
                    end,
                }
            end,
        }
    end,
}

yield = function(candidate)
    table.insert(yielded, candidate.text)
end

local contextual_order = true
local env = {
    engine = {
        context = {
            get_option = function(_, name)
                assert(name == "contextual_order")
                return contextual_order
            end,
            commit_history = {
                push = function(_, record_type, text)
                    table.insert(history_pushes, { record_type, text })
                end,
            },
            commit_notifier = {
                connect = function(_, callback)
                    commit_callback = callback
                    return {
                        disconnect = function()
                            commit_connection_disconnected = true
                        end,
                    }
                end,
            },
        },
    },
}

local selector = require("mohu_contextual_translator")
selector.init_pair(env, "translator")
assert(#created == 1)
assert(created[1] == "script_translator@translator")
assert(selector.get(env).name == "script_translator@translator")
assert(selector.get(env).contextual_suggestions == true)
assert(commit_callback ~= nil)
commit_callback(env.engine.context)
assert(#history_pushes == 0)

selector.func("test", {}, env)
assert(queried["script_translator@translator"] == 1)
assert(queried["script_translator@translator_static"] == nil)
assert(yielded[1] == "script_translator@translator")

contextual_order = false
assert(selector.get(env).name == "script_translator@translator")
assert(selector.get(env).contextual_suggestions == true)
assert(#created == 1)
commit_callback(env.engine.context)
assert(#history_pushes == 1)
assert(history_pushes[1][1] == "mohu_contextual")
assert(history_pushes[1][2] == "")

yielded = {}
selector.func("test", {}, env)
assert(queried["script_translator@translator"] == 2)
assert(queried["script_translator@translator_static"] == nil)
assert(yielded[1] == "script_translator@translator")

contextual_order = true
assert(selector.get(env).name == "script_translator@translator")
assert(selector.get(env).contextual_suggestions == true)
assert(#created == 1)

selector.fini_pair(env)
assert(env.contextual_translator == nil)
assert(env.static_translator == nil)
assert(env.contextual_commit_notifier == nil)
assert(commit_connection_disconnected)

local threshold_env = {
    engine = {
        schema = {
            config = {
                get_int = function(_, key)
                    assert(key == "tiger/long_input_length")
                    return 5
                end,
                get_string = function(_, key)
                    assert(key == "tiger/candidate_type")
                    return "mohu_zrm"
                end,
            },
        },
        context = {
            get_option = function() return true end,
            commit_history = { push = function() end },
            commit_notifier = {
                connect = function(_, callback)
                    return { disconnect = function() end }
                end,
            },
        },
    },
}
selector.init_pair(threshold_env, "translator")
assert(threshold_env.contextual_long_input_length == 5)
assert(selector.get_for_input(threshold_env, "abcd").name == "script_translator@translator")
assert(selector.get_for_input(threshold_env, "ab cd").name == "script_translator@translator")
assert(selector.get_for_input(threshold_env, "abcde").name == "script_translator@smart_static")
assert(selector.get_for_input(threshold_env, "abcdef").name == "script_translator@smart_static")
selector.fini_pair(threshold_env)

local ordinary_env = {
    engine = {
        schema = {
            config = {
                get_string = function(_, key)
                    assert(key == "tiger/candidate_type")
                    return nil
                end,
                get_int = function()
                    error("ordinary schema must not read long_input_length")
                end,
            },
        },
        context = threshold_env.engine.context,
    },
}
selector.init_pair(ordinary_env, "translator")
assert(ordinary_env.contextual_long_input_length == nil)
assert(selector.get_for_input(ordinary_env, "abcdef").name == "script_translator@translator")
selector.fini_pair(ordinary_env)

print("contextual translator tests passed")
