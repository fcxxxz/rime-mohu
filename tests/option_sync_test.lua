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

local function make_env(schema_id)
    local ctx = make_context({})
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
local env_a, ctx_a = make_env("mohu_llm_zrm")
option_sync.init(env_a)
assert(ctx_a:get_option("contextual_order") == true, "contextual_order 默认开")
assert(ctx_a:get_option("mohu_llm_model_rerank") == false, "model_rerank 默认关")
assert(ctx_a:get_option("quick_code_hint") == false, "quick_code_hint 默认关")

-- 2. 菜单里切换开关 → 写入共享状态文件
ctx_a:set_option("mohu_llm_model_rerank", true)
assert(option_state.get("mohu_llm_model_rerank") == true, "切换后写入状态文件")

-- 3. 新会话恢复：另一个应用的会话初始化时读到文件值
local env_b, ctx_b = make_env("mohu_llm_flypy")
option_sync.init(env_b)
assert(ctx_b:get_option("mohu_llm_model_rerank") == true, "跨方案恢复 model_rerank")

-- 4. 已打开会话的实时跟随：A 切换后，B 打字时同步
ctx_a:set_option("quick_code_hint", true)
fake_ms = fake_ms + 1000 -- 越过 250ms 节流
option_sync.func(key_press, env_b)
assert(ctx_b:get_option("quick_code_hint") == true, "B 会话按键后跟随 A 的修改")

-- 5. 其他方案只同步通用名单，不带方案私有开关
local env_d, ctx_d = make_env("mohu_zrm")
option_sync.init(env_d)
fake_ms = fake_ms + 1000
ctx_a:set_option("multi_short_code", true)
option_sync.func(key_press, env_d)
assert(ctx_d:get_option("multi_short_code") == true, "通用开关跨方案同步")
assert(ctx_d:get_option("mohu_llm_model_rerank") == false, "方案私有开关不进入通用方案")

os.remove(state_dir .. "/lua/option_state_data.lua")
print("option_sync: ok")
