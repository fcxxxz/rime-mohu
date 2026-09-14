package.path = "./lua/?.lua;" .. package.path

local state_dir = (os.getenv("TMPDIR") or "/tmp") .. "/mohu_option_sync_test"
os.execute("mkdir -p " .. state_dir .. "/lua")
os.remove(state_dir .. "/lua/option_state_data.lua")

local fake_ms = 1000
rime_api = {
    get_user_data_dir = function()
        return state_dir
    end,
    get_time_ms = function()
        return fake_ms
    end,
}

local function make_context(initial)
    local options = {}
    for name, value in pairs(initial or {}) do
        options[name] = value and true or false
    end
    local handlers = {}
    local ctx = {}
    ctx.option_update_notifier = {
        connect = function(_, fn)
            table.insert(handlers, fn)
            return { disconnect = function() end }
        end,
    }
    function ctx:get_option(name)
        return options[name] and true or false
    end
    function ctx:set_option(name, value)
        options[name] = value and true or false
        for _, fn in ipairs(handlers) do
            fn(ctx, name)
        end
    end
    return ctx
end

local function make_env(schema_id, initial)
    local ctx = make_context(initial)
    local env = {
        engine = {
            context = ctx,
            schema = { schema_id = schema_id },
        },
    }
    return env, ctx
end

local key_press = { release = function() return false end }

local option_sync = require("option_sync")
local option_state = require("option_state")

-- 1. 首次初始化：无 reset 的会话按默认值补齐
local env_a, ctx_a = make_env("mohu_zrm", { neural_rerank = true })
option_sync.init(env_a)
assert(ctx_a:get_option("contextual_order") == true, "contextual_order 默认开")
assert(ctx_a:get_option("quick_code_hint") == false, "quick_code_hint 默认关")
assert(ctx_a:get_option("neural_rerank") == false, "neural_rerank 无持久值时默认关")
assert(option_state.get("neural_rerank", true, true) == false, "默认关闭写入持久状态")

-- 2. 新会话恢复：另一个应用的会话初始化时读到文件值
local env_b, ctx_b = make_env("mohu_flypy")
option_sync.init(env_b)

-- 3. 已打开会话的实时跟随：A 切换后，B 打字时同步
ctx_a:set_option("quick_code_hint", true)
fake_ms = fake_ms + 1000 -- 越过 250ms 节流
option_sync.func(key_press, env_b)
assert(ctx_b:get_option("quick_code_hint") == true, "B 会话按键后跟随 A 的修改")

-- 4. 其他方案只同步通用名单
local env_d, ctx_d = make_env("mohu_zrm")
option_sync.init(env_d)
fake_ms = fake_ms + 1000
ctx_a:set_option("multi_short_code", true)
option_sync.func(key_press, env_d)
assert(ctx_d:get_option("multi_short_code") == true, "通用开关跨方案同步")

for _, schema_id in ipairs({ "mohu_zrm", "mohu_flypy", "other_schema" }) do
    local count = 0
    for _, name in ipairs(option_sync._test_option_names(schema_id)) do
        if name == "neural_rerank" then
            count = count + 1
        end
    end
    assert(count == 1, schema_id .. " 同步名单恰好包含一次 neural_rerank")
end

local function assert_neural_state(ctx, expected, message)
    assert(ctx:get_option("neural_rerank") == expected, message)
    local persisted = assert(loadfile(state_dir .. "/lua/option_state_data.lua"))()
    assert(persisted.neural_rerank == expected, message .. "（磁盘持久值）")
end

local function follow(env)
    fake_ms = fake_ms + 1000
    assert(option_sync.func(key_press, env) == 2, "同步不拦截按键")
end

for _, transition in ipairs({
    { source = ctx_a, target = ctx_b, env = env_b, enabled = true },
    { source = ctx_b, target = ctx_a, env = env_a, enabled = false },
    { source = ctx_b, target = ctx_a, env = env_a, enabled = true },
    { source = ctx_a, target = ctx_b, env = env_b, enabled = false },
}) do
    transition.source:set_option("neural_rerank", transition.enabled)
    assert_neural_state(transition.source, transition.enabled, "菜单切换即时生效并保存")
    follow(transition.env)
    assert_neural_state(transition.target, transition.enabled, "开关双向跨环境跟随")
    assert(transition.source:get_option("contextual_order") == true, "神经开关不关闭源会话 V5")
    assert(transition.target:get_option("contextual_order") == true, "神经同步不关闭目标会话 V5")
end

ctx_a:set_option("contextual_order", false)
ctx_a:set_option("neural_rerank", true)
follow(env_b)
assert_neural_state(ctx_b, true, "V5 关闭时仍可独立开启神经开关")
assert(ctx_b:get_option("contextual_order") == false, "开启神经开关不自动开启 V5")
ctx_b:set_option("contextual_order", true)
follow(env_a)
assert_neural_state(ctx_a, true, "V5 切换不改变神经开关")
assert(ctx_a:get_option("contextual_order") == true, "V5 仍可独立开启")

option_sync.fini(env_a)
option_sync.fini(env_b)
option_sync.fini(env_d)
for _, enabled in ipairs({ true, false }) do
    option_state.set("contextual_order", not enabled)
    package.loaded.option_sync = nil
    package.loaded.option_state = nil
    option_sync = require("option_sync")
    option_state = require("option_state")
    local restarted, restarted_ctx = make_env("mohu_flypy", {
        neural_rerank = not enabled,
        contextual_order = enabled,
    })
    option_sync.init(restarted)
    assert_neural_state(restarted_ctx, enabled, "重新 require 后恢复磁盘开关状态")
    assert(restarted_ctx:get_option("contextual_order") == not enabled, "重启分别恢复两个开关")
    local switched, switched_ctx = make_env("mohu_zrm", { neural_rerank = not enabled })
    option_sync.init(switched)
    assert_neural_state(switched_ctx, enabled, "切换 schema 保留持久值")
    restarted_ctx:set_option("neural_rerank", not enabled)
    assert_neural_state(restarted_ctx, not enabled, "重启后继续切换并持久化")
    follow(switched)
    assert_neural_state(switched_ctx, not enabled, "重启后仍可跨环境同步")
    option_sync.fini(restarted)
    option_sync.fini(switched)
end

os.remove(state_dir .. "/lua/option_state_data.lua")
print("option_sync: ok")
