-- 方向键 / Tab 音节导航（librime 1.16 兼容层）。
--
-- 背景：librime 1.16 navigator 的音节顶点（spans）只认原生 Phrase
-- 候选自带的切分表；native 句图候选（Lua 构造）不提供 spans，且
-- caret 落到句中后整句候选被 sentence_visibility 押后，navigator
-- 拿不到任何顶点（1.17.0 重写了步长计算，Squirrel 1.16 无此修复）。
--
-- 站点来源：选中候选的音节串（native segmented / smart 渲染，空格
-- 分隔）。每个空格单元是一个「字码」——纯双拼字 2 键、三码 3 键、
-- 全码 4 键（双拼+辅码）、或引擎切出的多字词——**辅码归属正确**，
-- 站点永远落在单元边界，不会把「命 myjd」的辅码劈开。多字词整词
-- 一站（如 vege=「这个」），词内定位用左右方向键逐字符微调。
--
-- caret 落在句中时选中候选只覆盖活动段，站点会丢：导航过程中输入
-- 不变，故在光标处于句尾、候选完整时计算一次站点并按 input 缓存，
-- 导航键全程命中缓存。
--
-- 接管的键（仅在输入非空且只含小写字母时；候选不可用时降级为
-- 每 2 字母一站）：
--   Tab / Shift+Tab          —— 下一站 / 上一站（含句尾↔句首回绕）
--   Control+Left/Right       —— 同上（key_binder 的 mohu_tab 绑定兜底
--                                非小写输入场景，故保留）
--   Left / Right             —— 智能移动：光标在站点上跳一站（回绕），
--                                站点之间逐字符（保留音节内微调能力）
--   Shift+字母 / Shift+BackSpace
--                             —— 小写字母插入 / 删除「上一音节末尾」
--                                （倒数第二个站点处）。取代 key_binder 的
--                                send_sequence 版本：该版本在 1.16 长句
--                                上 Control+Left 一步跳句首，字母被插进
--                                句首导致整句候选清空。主方案 schema 已
--                                移除 mohu_capital_for_last_syllable 引用，
--                                字词/裁剪方案仍用旧绑定。
--
-- librime-lua 的 ctx.caret_pos / ctx.input 均可写（2026-09-20 探针
-- 实测），这是本兼容层可行的前提。与方向键 preedit 修复同批引入。

local kAccepted = 1
local kNoop = 2

local KEY_TAB = 0xff09
local KEY_ISO_LEFT_TAB = 0xfe20
local KEY_LEFT = 0xff51
local KEY_RIGHT = 0xff53
local KEY_BACKSPACE = 0xff08

local M = {}

-- 站点缓存：按 input 键控。导航过程输入不变，句尾算一次全程有效；
-- 输入变化（新字母/删除）自然失效重算。
local stops_cache = { input = nil, stops = nil }

-- 从选中候选音节串计算站点。返回 nil 表示不可用（调用方降级）。
local function stops_from_candidate(ctx, input)
    local ok, comp = pcall(function() return ctx.composition end)
    if not ok or comp == nil then return nil end
    local seg_ok, segment = pcall(function() return comp:back() end)
    if not seg_ok or segment == nil then return nil end
    local cand_ok, cand = pcall(function()
        return segment:get_selected_candidate()
    end)
    if not cand_ok or cand == nil then return nil end
    local preedit_ok, preedit = pcall(function() return cand.preedit end)
    if not preedit_ok or type(preedit) ~= "string" or preedit == "" then
        return nil
    end
    if not preedit:match("^[a-z ]+$") then return nil end
    local stops = {}
    local raw_pos = 0
    local unit_letters = 0
    for index = 1, #preedit do
        local ch = preedit:sub(index, index)
        if ch == " " then
            if unit_letters > 0 then
                raw_pos = raw_pos + unit_letters
                unit_letters = 0
                if raw_pos > 0 and raw_pos < #input then
                    stops[#stops + 1] = raw_pos
                end
            end
        else
            unit_letters = unit_letters + 1
        end
    end
    -- 候选必须覆盖整个输入，否则坐标系不可信（caret 在句中、候选只
    -- 覆盖活动段的情况由缓存层挡住，走到这里说明缓存未建立）。
    if raw_pos + unit_letters ~= #input then return nil end
    stops[#stops + 1] = #input
    if #stops < 2 then return nil end
    return stops
end

-- 降级站点：每 2 字母（纯双拼近似；含辅码时仅供兜底，正常路径不会
-- 走到这里——选中候选缺失的输入通常也无法整句解码）。
local function fallback_stops(input)
    local stops = {}
    local pos = 2
    while pos < #input do
        stops[#stops + 1] = pos
        pos = pos + 2
    end
    stops[#stops + 1] = #input
    return stops
end

local function compute_stops(ctx, input)
    if stops_cache.input == input and stops_cache.stops ~= nil then
        return stops_cache.stops
    end
    local stops = stops_from_candidate(ctx, input) or fallback_stops(input)
    stops_cache.input = input
    stops_cache.stops = stops
    return stops
end

local function is_stop(caret, stops)
    if caret == 0 then return true end
    for _, stop in ipairs(stops) do
        if stop == caret then return true end
    end
    return false
end

-- 站点跳转（带回绕）。返回目标位置，无可移动时返回 nil。
local function stop_move(caret, input, stops, backward)
    local target = nil
    if backward then
        for index = #stops, 1, -1 do
            if stops[index] < caret then
                target = stops[index]
                break
            end
        end
        if caret == 0 then
            target = #input
        elseif target == nil then
            target = 0
        end
    else
        for index = 1, #stops do
            if stops[index] > caret then
                target = stops[index]
                break
            end
        end
        if target == nil then target = 0 end
    end
    if target == caret then return nil end
    return target
end

-- 智能移动（1.17 Rewind/Forward 语义）：站点上跳站（回绕），站点间逐字符。
local function smart_move(caret, input, stops, backward)
    if not is_stop(caret, stops) then
        local target = backward and caret - 1 or caret + 1
        if target < 0 or target > #input then return nil end
        return target
    end
    return stop_move(caret, input, stops, backward)
end

-- 「上一音节末尾」位置：倒数第二个站点（最后一个站点恒为句尾）。
local function previous_syllable_end(stops)
    if #stops < 2 then return nil end
    return stops[#stops - 1]
end

local function write_caret(ctx, target)
    return pcall(function() ctx.caret_pos = target end)
end

local function write_input(ctx, value, caret)
    local ok = pcall(function() ctx.input = value end)
    if ok and caret then
        pcall(function() ctx.caret_pos = caret end)
    end
    return ok
end

function M.func(key_event, env)
    if key_event:release() then
        return kNoop
    end
    local keycode = key_event.keycode
    local shifted = key_event:shift()
    local backward = keycode == KEY_ISO_LEFT_TAB

    local ctx = env.engine.context
    local input = ctx.input
    if type(input) ~= "string" or #input < 2 or not input:match("^[a-z]+$") then
        return kNoop
    end
    local stops = compute_stops(ctx, input)

    -- Shift+大写字母：插入小写字母到上一音节末尾；Shift+BackSpace 删除
    -- 上一音节末位字母。
    if shifted and keycode >= 0x41 and keycode <= 0x5A then
        local pos = previous_syllable_end(stops)
        if pos == nil or pos < 2 then
            return kNoop
        end
        local letter = string.char(keycode + 0x20)
        local new_input = input:sub(1, pos) .. letter .. input:sub(pos + 1)
        if write_input(ctx, new_input, #new_input) then
            return kAccepted
        end
        return kNoop
    end
    if shifted and keycode == KEY_BACKSPACE then
        local pos = previous_syllable_end(stops)
        if pos == nil or pos < 2 then
            return kNoop
        end
        local new_input = input:sub(1, pos - 1) .. input:sub(pos + 1)
        if write_input(ctx, new_input, #new_input) then
            return kAccepted
        end
        return kNoop
    end

    -- 方向键族：Control+方向 / Tab（及 Shift 反向）纯站点跳；
    -- 普通方向键智能移动。
    local caret_ok, caret = pcall(function() return ctx.caret_pos end)
    if not caret_ok or type(caret) ~= "number" then
        return kNoop
    end

    local target = nil
    if keycode == KEY_TAB or keycode == KEY_ISO_LEFT_TAB or
        (key_event:ctrl() and (keycode == KEY_LEFT or keycode == KEY_RIGHT)) then
        if keycode == KEY_LEFT then backward = true end
        if keycode == KEY_TAB and shifted then backward = true end
        target = stop_move(caret, input, stops, backward)
    elseif keycode == KEY_LEFT then
        target = smart_move(caret, input, stops, true)
    elseif keycode == KEY_RIGHT then
        target = smart_move(caret, input, stops, false)
    else
        return kNoop
    end

    if target == nil then
        return kNoop
    end
    if write_caret(ctx, target) then
        return kAccepted
    end
    return kNoop
end

M._test = {
    stops_from_candidate = stops_from_candidate,
    fallback_stops = fallback_stops,
    compute_stops = compute_stops,
    is_stop = is_stop,
    stop_move = stop_move,
    smart_move = smart_move,
    previous_syllable_end = previous_syllable_end,
    reset_cache = function()
        stops_cache.input = nil
        stops_cache.stops = nil
    end,
}

return M
