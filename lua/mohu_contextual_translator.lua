-- Runtime switch between composition-only and cross-commit contextual ordering.
--
-- 两种模式始终开启 contextual_suggestions，以保留同一输入串内的上下文
-- 加权。「单次候选调频」在上屏后追加空历史，阻止下一次输入读取上文；
-- 「跨候选调频」则保留 librime 的默认上屏历史。

local Module = {}

local function canonical_input(raw)
    if type(raw) ~= "string" then return "" end
    return raw:gsub("[ \t\r\n]", ""):lower()
end

function Module.init_pair(env, name)
    env.contextual_translator_name = name
    env.contextual_translator = Component.Translator(
        env.engine,
        "",
        "script_translator@" .. name
    )
    env.static_translator = nil

    local config = env.engine.schema and env.engine.schema.config
    local long_input_length = nil
    local is_llm_schema = false
    if config and type(config.get_string) == "function" then
        local ok, value = pcall(config.get_string, config, "tiger/candidate_type")
        is_llm_schema = ok and type(value) == "string" and value ~= ""
    end
    if is_llm_schema and config and type(config.get_int) == "function" then
        local ok, value = pcall(config.get_int, config, "tiger/long_input_length")
        if ok and type(value) == "number" then
            long_input_length = value
        end
    end
    if long_input_length == nil and is_llm_schema and config and type(config.get_string) == "function" then
        local ok, value = pcall(config.get_string, config, "tiger/long_input_length")
        if ok then long_input_length = tonumber(value) end
    end
    env.contextual_long_input_length =
        (type(long_input_length) == "number" and long_input_length >= 1)
        and math.floor(long_input_length) or nil

    -- 长句方案在装载期预建 smart_static，避免长输入的第一个按键
    -- 付出 script_translator 构建成本（词典打开、Memory 装配等）。
    if env.contextual_long_input_length ~= nil and env.static_translator == nil then
        local ok = pcall(function()
            env.static_translator = Component.Translator(
                env.engine,
                "",
                "script_translator@smart_static"
            )
            env.static_translator.contextual_suggestions = true
        end)
        if not ok then env.static_translator = nil end
    end

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

function Module.get_for_input(env, input)
    local raw = canonical_input(input)
    local threshold = env.contextual_long_input_length
    if type(threshold) ~= "number" or #raw < threshold then
        return Module.get(env)
    end
    if env.static_translator == nil then
        env.static_translator = Component.Translator(
            env.engine,
            "",
            "script_translator@smart_static"
        )
    end
    if env.static_translator.contextual_suggestions ~= true then
        env.static_translator.contextual_suggestions = true
    end
    return env.static_translator
end

function Module.fini_pair(env)
    if env.contextual_commit_notifier ~= nil then
        env.contextual_commit_notifier:disconnect()
        env.contextual_commit_notifier = nil
    end
    env.contextual_translator = nil
    env.static_translator = nil
    env.contextual_translator_name = nil
    env.contextual_long_input_length = nil
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
