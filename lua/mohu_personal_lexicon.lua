-- Snapshot of user-created phrases for the native sentence decoder.
-- The full user dictionary is read only at refresh boundaries, never per key.

local M = {}

local MAX_TEXT_BYTES = 192
local MAX_CODE_BYTES = 128

local function valid_utf8(text)
    if type(text) ~= "string" then return false end
    -- utf8.len 不抛错：非法 UTF-8 返回 nil，空串返回 0。
    -- %c 覆盖 \0-\31，\127（DEL）单独列出。
    local count = utf8.len(text)
    if not count or count < 1 then return false end
    return text:find("[%c\127]") == nil
end

local function pure_double_pinyin(code)
    if type(code) ~= "string" then return nil end
    local result = {}
    for token in code:gmatch("%S+") do
        local bare = token:match("^([A-Za-z]+)")
        if not bare or #bare == 0 or #bare % 2 ~= 0 then return nil end
        for index = 1, #bare, 2 do
            result[#result + 1] = bare:sub(index, index + 1):lower()
        end
    end
    if #result == 0 then return nil end
    return table.concat(result)
end

local function is_builtin(memory, code, text)
    if memory == nil or type(memory.dictiter_lookup) ~= "function" then
        return false
    end
    local function lookup(candidate_code)
        -- 单次 pcall 包住词典查询与迭代；rime userdata 的任何访问异常
        -- 都按“查不到”处理。
        local ok, found = pcall(function()
            local iterator = memory:dictiter_lookup(candidate_code, false, 0)
            if iterator == nil or type(iterator.iter) ~= "function" then
                return false
            end
            for entry in iterator:iter() do
                if entry and entry.text == text then return true end
            end
            return false
        end)
        return ok and found or false
    end
    if lookup(code) then return true end
    local normalized = pure_double_pinyin(code)
    return normalized ~= nil and normalized ~= code and lookup(normalized) or false
end

-- 一次性读取条目的三个字段；任何字段缺失或访问异常时 text 为 nil。
local function entry_fields(entry)
    if type(entry) ~= "table" and type(entry) ~= "userdata" then return nil end
    local ok, text, raw_code, commits = pcall(function()
        return entry.text, entry.custom_code, entry.commit_count
    end)
    if not ok then return nil end
    return text, raw_code, commits
end

function M.normalize_code(code)
    return pure_double_pinyin(code)
end

-- 条目验收谓词链：整体扫描与分片扫描共用，保证两条路径结果一致。
-- 返回 code, text, commits（已归一），不满足条件返回 nil。
local function accept_entry(memory, seen, text, raw_code, commits)
    if type(text) ~= "string" or type(raw_code) ~= "string" then return nil end
    commits = tonumber(commits)
    if not commits or commits <= 0 then return nil end
    commits = math.min(math.floor(commits), 1000000)
    local text_length = #text
    if text_length <= 0 or text_length > MAX_TEXT_BYTES or
        not valid_utf8(text) or (utf8.len(text) or 0) <= 1 then
        return nil
    end
    local code = pure_double_pinyin(raw_code)
    if not code or #code < 4 or #code > MAX_CODE_BYTES then return nil end
    if is_builtin(memory, raw_code, text) then return nil end
    local key = code .. "\t" .. text
    if seen[key] then return nil end
    seen[key] = true
    return code, text, commits
end

function M.collect(memory, options)
    options = options or {}
    local limit = tonumber(options.limit)
    if limit ~= nil then
        limit = math.max(0, math.floor(limit))
    end
    local result, seen = {}, {}
    if memory == nil or type(memory.user_lookup) ~= "function" then return result end

    local ok, found = pcall(memory.user_lookup, memory, "", true)
    if not ok or not found or type(memory.iter_user) ~= "function" then return result end
    pcall(function()
        for entry in memory:iter_user() do
            local code, text, commits = accept_entry(memory, seen, entry_fields(entry))
            if code then
                result[#result + 1] = { code = code, text = text, commits = commits }
            end
        end
    end)
    table.sort(result, function(a, b)
        if a.commits ~= b.commits then return a.commits > b.commits end
        if a.code ~= b.code then return a.code < b.code end
        return a.text < b.text
    end)
    if limit ~= nil then
        while #result > limit do table.remove(result) end
    end
    return result
end

function M.serialize(rows, options)
    options = options or {}
    local limit = tonumber(options.limit)
    if limit ~= nil then
        limit = math.max(0, math.floor(limit))
    end
    local payload, count = {}, 0
    for _, row in ipairs(rows or {}) do
        if limit ~= nil and count >= limit then break end
        if type(row.code) == "string" and type(row.text) == "string" and
            #row.code >= 4 and row.code:match("^[a-z]+$") and #row.code <= MAX_CODE_BYTES and
            valid_utf8(row.text) and #row.text <= MAX_TEXT_BYTES and
            not row.code:find("[\r\n\t]") and not row.text:find("[\r\n\t]") then
            local line = row.code .. "\t" .. row.text .. "\t" .. tostring(math.max(1, math.floor(tonumber(row.commits) or 1))) .. "\n"
            payload[#payload + 1] = line
            count = count + 1
        end
    end
    return table.concat(payload), count
end

function M.snapshot(memory, options)
    local rows = M.collect(memory, options)
    local payload, count = M.serialize(rows, options)
    return rows, payload, count
end

-- ---------------------------------------------------------------- 分片扫描
-- 把 userdb 全量扫描切成 CPU 预算受限的片段，供 translator 在输入组合
-- 为空的间隙逐片推进：任何时刻按键最坏只等待一个切片预算。
-- 与整体扫描共用 accept_entry，行集合一致；顺序按库键序（确定性足够，
-- 负载相等性检查只需同路径自身可复现）。

local DEFAULT_SLICE_BUDGET = 0.005  -- 秒；单片 CPU 预算

-- 开始一次分片扫描。返回 state；不可用返回 nil,"unavailable"，
-- userdb 为空返回 nil,"empty"（调用方应据此应用空负载）。
function M.scan_begin(memory)
    if memory == nil or type(memory.user_lookup) ~= "function" or
        type(memory.iter_user) ~= "function" then
        return nil, "unavailable"
    end
    local ok, found = pcall(memory.user_lookup, memory, "", true)
    if not ok then return nil, "unavailable" end
    if not found then return nil, "empty" end
    local it_ok, iterator = pcall(function() return memory:iter_user() end)
    if not it_ok or type(iterator) ~= "function" then return nil, "unavailable" end
    return {
        memory = memory,
        iterator = iterator,
        seen = {},
        parts = {},
        count = 0,
    }
end

-- 推进一个切片；budget 为秒。返回 true 表示扫描完成（迭代耗尽或出错，
-- 出错时保留已收集部分，与整体扫描的容错语义一致）。
-- 每片另有 512 条的硬上限：即使 os.clock 粒度粗于预算，单片耗时也有
-- 与时钟无关的上界（约几毫秒），保证按键最坏等待可控。
local SLICE_ENTRY_CAP = 512

function M.scan_step(state, budget)
    local finished = false
    local ok = pcall(function()
        local deadline = os.clock() + (budget or DEFAULT_SLICE_BUDGET)
        local checks = 0
        local processed = 0
        while true do
            local entry = state.iterator()
            if entry == nil then
                finished = true
                return
            end
            local code, text, commits =
                accept_entry(state.memory, state.seen, entry_fields(entry))
            if code then
                state.count = state.count + 1
                state.parts[#state.parts + 1] = code .. "\t" .. text .. "\t" .. commits .. "\n"
            end
            processed = processed + 1
            if processed >= SLICE_ENTRY_CAP then return end
            checks = checks + 1
            if checks >= 32 then
                checks = 0
                if os.clock() >= deadline then return end
            end
        end
    end)
    if not ok then finished = true end
    return finished
end

-- 结束扫描并产出负载。payload 与整体路径格式一致；count 为行数。
function M.scan_finish(state)
    return table.concat(state.parts), state.count
end

M._test = {
    pure_double_pinyin = pure_double_pinyin,
    valid_utf8 = valid_utf8,
    is_builtin = is_builtin,
}

return M
