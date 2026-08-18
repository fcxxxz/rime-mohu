-- Runtime switch between composition-only and cross-commit contextual ordering.
--
-- 两种模式始终开启 contextual_suggestions，以保留同一输入串内的上下文
-- 加权。「单次候选调频」在上屏后追加空历史，阻止下一次输入读取上文；
-- 「跨候选调频」则保留 librime 的默认上屏历史。

local Module = {}

function Module.init_pair(env, name)
    env.contextual_translator_name = name
    env.contextual_translator = Component.Translator(
        env.engine,
        "",
        "script_translator@" .. name
    )
    env.static_translator = nil

    local context = env.engine.context
    env.contextual_commit_notifier = context.commit_notifier:connect(function(ctx)
        if not ctx:get_option("contextual_order") then
            ctx.commit_history:push("mohu_contextual", "")
        end
    end)
end

function Module.get(env)
    if env.contextual_translator.contextual_suggestions ~= true then
        env.contextual_translator.contextual_suggestions = true
    end
    return env.contextual_translator
end

function Module.fini_pair(env)
    if env.contextual_commit_notifier ~= nil then
        env.contextual_commit_notifier:disconnect()
        env.contextual_commit_notifier = nil
    end
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
