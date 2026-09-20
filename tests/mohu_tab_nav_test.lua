-- mohu_tab_nav 单元测试：候选切分站点导航与 Shift+字母补辅码。
package.path = "./lua/?.lua;" .. package.path

local nav = dofile("lua/mohu_tab_nav.lua")

local failures = 0
local function check(name, ok)
    if ok then
        print("ok - " .. name)
    else
        failures = failures + 1
        print("FAIL - " .. name)
    end
end

-- stops_from_candidate：mock 一个带选中候选音节串的上下文。
local function make_ctx(input, segmented)
    local segment = {
        get_selected_candidate = function()
            return { preedit = segmented, type = "mohu_zrm" }
        end,
    }
    local comp = { back = function() return segment end }
    return { input = input, composition = comp }
end

-- 站点 = 音节串空格单元起点；辅码归属单元内部，永不出站。
check("stops respect aux code unit",
    table.concat(nav._test.stops_from_candidate(make_ctx("myjdly", "myjd ly"), "myjdly"), ",") == "4,6")
check("stops full code then pinyin",
    table.concat(nav._test.stops_from_candidate(make_ctx("myjdlyjk", "myjd lyjk"), "myjdlyjk"), ",") == "4,8")
check("stops three code unit",
    table.concat(nav._test.stops_from_candidate(make_ctx("myjly", "myj ly"), "myjly"), ",") == "3,5")
check("stops word merged",
    table.concat(nav._test.stops_from_candidate(make_ctx("sssswoma", "ss ss woma"), "sssswoma"), ",") == "2,4,8")
check("stops multi char word one unit",
    table.concat(nav._test.stops_from_candidate(make_ctx("vegeuurufaviii", "vege uuru fa viii"), "vegeuurufaviii"), ",") == "4,8,10,14")
-- 候选不覆盖全输入 → 不可信，返回 nil。
check("stops nil when partial", nav._test.stops_from_candidate(make_ctx("myjdly", "myjd"), "myjdly") == nil)
check("stops nil on empty", nav._test.stops_from_candidate(make_ctx("myly", ""), "myly") == nil)

-- 降级站点：每 2 字母。
check("fallback stops", table.concat(nav._test.fallback_stops("sssswoma"), ",") == "2,4,6,8")

-- previous_syllable_end：倒数第二个站点。
check("prev syllable full code", nav._test.previous_syllable_end({4, 6}) == 4)
check("prev syllable word", nav._test.previous_syllable_end({2, 4, 8}) == 4)
check("prev syllable none single", nav._test.previous_syllable_end({6}) == nil)

-- stop_move / smart_move（站点跳、回绕、站点间逐字符）。
local stops = {2, 4, 8}
check("stop fwd wrap to 0", nav._test.stop_move(8, "sssswoma", stops, false) == 0)
check("stop fwd from 0", nav._test.stop_move(0, "sssswoma", stops, false) == 2)
check("stop back to 4", nav._test.stop_move(8, "sssswoma", stops, true) == 4)
check("stop back wrap to end", nav._test.stop_move(0, "sssswoma", stops, true) == 8)
check("smart stop jump", nav._test.smart_move(4, "sssswoma", stops, true) == 2)
check("smart mid one char back", nav._test.smart_move(5, "sssswoma", stops, true) == 4)
check("smart mid one char fwd", nav._test.smart_move(5, "sssswoma", stops, false) == 6)
check("smart start wrap", nav._test.smart_move(0, "sssswoma", stops, true) == 8)

-- func：mock 上下文验证按键分发与写入。
local function make_event(keycode, opts)
    opts = opts or {}
    return {
        keycode = keycode,
        release = function() return false end,
        shift = function() return opts.shift == true end,
        ctrl = function() return opts.ctrl == true end,
        alt = function() return false end,
        super = function() return false end,
    }
end

-- 集成 mock：composition 提供选中候选音节串，caret/input 可写。
local function make_env(input, caret, segmented)
    local segment = {
        get_selected_candidate = function()
            return { preedit = segmented, type = "mohu_zrm" }
        end,
    }
    local comp = { back = function() return segment end }
    local ctx = { input = input, _caret = caret, composition = comp }
    local proxy = setmetatable({}, {
        __index = function(_, key)
            if key == "caret_pos" then return ctx._caret end
            return ctx[key]
        end,
        __newindex = function(_, key, value)
            if key == "caret_pos" then ctx._caret = value
            elseif key == "input" then ctx.input = value
            else ctx[key] = value end
        end,
    })
    return { engine = { context = proxy } }, ctx
end

local TAB, ISO_LT, LEFT, RIGHT, BS = 0xff09, 0xfe20, 0xff51, 0xff53, 0xff08

nav._test.reset_cache()

-- myjdly（命全码 + 令）：站点 {4,6}，Tab 在 4 停（不劈辅码）。
local env, ctx = make_env("myjdly", 6, "myjd ly")
check("tab wrap to 0", nav.func(make_event(TAB), env) == 1 and ctx._caret == 0)
check("tab to 4 (aux intact)", nav.func(make_event(TAB), env) == 1 and ctx._caret == 4)
check("tab to 6", nav.func(make_event(TAB), env) == 1 and ctx._caret == 6)
check("shift tab to 4", nav.func(make_event(ISO_LT), env) == 1 and ctx._caret == 4)
check("shift tab to 0", nav.func(make_event(ISO_LT), env) == 1 and ctx._caret == 0)
check("shift tab wrap to end", nav.func(make_event(ISO_LT), env) == 1 and ctx._caret == 6)

-- 普通方向键：站点跳、辅码单元内逐字符。
local env2, ctx2 = make_env("myjdly", 3, "myjd ly")
nav._test.reset_cache()
check("plain left mid unit one char", nav.func(make_event(LEFT), env2) == 1 and ctx2._caret == 2)
check("plain right mid unit one char", nav.func(make_event(RIGHT), env2) == 1 and ctx2._caret == 3)
local env3, ctx3 = make_env("myjdly", 6, "myjd ly")
check("plain left stop jump over unit", nav.func(make_event(LEFT), env3) == 1 and ctx3._caret == 4)

-- Shift+字母：插到上一音节末（站点 4），myjdly → myjddly？不——
-- 插入点 4：myjd|ly → myjd + d + ly。
nav._test.reset_cache()
local env4, ctx4 = make_env("myjdly", 6, "myjd ly")
check("shift D inserts at aux boundary",
    nav.func(make_event(0x44, {shift=true}), env4) == 1
    and ctx4.input == "myjddly" and ctx4._caret == 7)
-- Shift+BackSpace：删上一音节末位（4 处字母 d）→ myjly。
nav._test.reset_cache()
local env5, ctx5 = make_env("myjdly", 6, "myjd ly")
check("shift backspace at aux boundary",
    nav.func(make_event(BS, {shift=true}), env5) == 1 and ctx5.input == "myjly")

-- 缓存：同一 input 第二次调用不再读候选（换成无候选上下文仍得站点）。
nav._test.reset_cache()
local env6 = make_env("sssswoma", 8, "ss ss woma")
check("cache first hit", nav.func(make_event(TAB), env6) == 1)
local env7, ctx7 = make_env("sssswoma", 0, nil)
ctx7.composition = nil
check("cache survives candidate loss",
    nav.func(make_event(TAB), env7) == 1 and ctx7._caret == 2)

-- 放行：非纯小写、单字母、非导航键、release、无站点插字母。
nav._test.reset_cache()
check("pass through with quote", nav.func(make_event(TAB), make_env("my'ly", 5)) == 2)
check("pass through single letter", nav.func(make_event(TAB), make_env("m", 1)) == 2)
check("pass through other key", nav.func(make_event(0x61), make_env("ssss", 4)) == 2)
local rel = make_event(TAB)
rel.release = function() return true end
check("pass through release", nav.func(rel, make_env("ssss", 4)) == 2)
local env_short, ctx_short = make_env("my", 2, nil)
ctx_short.composition = nil
check("shift letter no prev unit", nav.func(make_event(0x41, {shift=true}), env_short) == 2)

if failures > 0 then
    error(failures .. " test(s) failed")
end
print("mohu_tab_nav tests passed")
