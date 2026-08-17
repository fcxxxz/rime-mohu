-- Runtime switch between contextual and context-free ordering.
--
-- 只使用一个 script_translator 实例：静态排序时动态关闭
-- contextual_suggestions（语法模型上下文加权），而不另建实例。
-- 若另建实例，其 Memory 不会参与提交记忆，导致用户词典停止
-- 记录词频，表现为「固词」。

local Module = {}

function Module.init_pair(env, name)
    env.contextual_translator_name = name
    env.contextual_translator = Component.Translator(
        env.engine,
        "",
        "script_translator@" .. name
    )
    env.static_translator = nil
end

function Module.get(env)
    local contextual = env.engine.context:get_option("contextual_order")
    if env.contextual_translator.contextual_suggestions ~= contextual then
        env.contextual_translator.contextual_suggestions = contextual
    end
    return env.contextual_translator
end

function Module.fini_pair(env)
    env.contextual_translator = nil
    env.static_translator = nil
    env.contextual_translator_name = nil
end

function Module.init_runtime_pair(env, option_name, primary_name, alternate_name)
    env.runtime_option_name = option_name
    env.runtime_primary = Component.Translator(env.engine, "", primary_name)
    env.runtime_alternate_name = alternate_name
    env.runtime_alternate = nil
end

function Module.get_runtime(env)
    if env.engine.context:get_option(env.runtime_option_name) then
        return env.runtime_primary
    end
    if not env.runtime_alternate then
        env.runtime_alternate = Component.Translator(
            env.engine,
            "",
            env.runtime_alternate_name
        )
    end
    return env.runtime_alternate
end

function Module.fini_runtime_pair(env)
    env.runtime_primary = nil
    env.runtime_alternate = nil
    env.runtime_option_name = nil
    env.runtime_alternate_name = nil
end

function Module.init(env)
    Module.init_pair(env, "translator")
end

function Module.func(input, seg, env)
    local translation = Module.get(env):query(input, seg)
    if translation then
        for candidate in translation:iter() do
            yield(candidate)
        end
    end
end

function Module.fini(env)
    Module.fini_pair(env)
    collectgarbage()
end

local fixed_selector = {}

function fixed_selector.init(env)
    Module.init_runtime_pair(
        env,
        "multi_short_code",
        "table_translator@fixed",
        "table_translator@fixed_legacy"
    )
end

function fixed_selector.func(input, seg, env)
    local translation = Module.get_runtime(env):query(input, seg)
    if translation then
        for candidate in translation:iter() do
            yield(candidate)
        end
    end
end

function fixed_selector.fini(env)
    Module.fini_runtime_pair(env)
    collectgarbage()
end

Module.fixed_selector = fixed_selector

local fixed_static_selector = {}

function fixed_static_selector.init(env)
    Module.init_runtime_pair(
        env,
        "multi_short_code",
        "table_translator@translator",
        "table_translator@translator_legacy"
    )
end

function fixed_static_selector.func(input, seg, env)
    local translation = Module.get_runtime(env):query(input, seg)
    if translation then
        for candidate in translation:iter() do
            yield(candidate)
        end
    end
end

function fixed_static_selector.fini(env)
    Module.fini_runtime_pair(env)
    collectgarbage()
end

Module.fixed_static_selector = fixed_static_selector

return Module
