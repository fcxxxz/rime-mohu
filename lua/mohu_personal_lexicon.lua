-- Snapshot of user-created phrases for the native sentence decoder.
-- The full user dictionary is read only at refresh boundaries, never per key.

local M = {}

local MAX_TEXT_BYTES = 192
local MAX_CODE_BYTES = 128

local function valid_utf8(text)
    if type(text) ~= "string" then return false end
    local ok = pcall(function()
        for _ in utf8.codes(text) do end
    end)
    for index = 1, #text do
        local byte = text:byte(index)
        if byte < 32 or byte == 127 then
            return false
        end
    end
    return ok and text ~= ""
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
        local ok, iterator = pcall(memory.dictiter_lookup, memory, candidate_code, false, 0)
        if not ok or iterator == nil or type(iterator.iter) ~= "function" then
            return false
        end
        local iter_ok, found = pcall(function()
            for entry in iterator:iter() do
                if entry and entry.text == text then return true end
            end
            return false
        end)
        return iter_ok and found or false
    end
    if lookup(code) then return true end
    local normalized = pure_double_pinyin(code)
    return normalized ~= nil and normalized ~= code and lookup(normalized) or false
end

local function entry_code(entry)
    if type(entry) ~= "table" and type(entry) ~= "userdata" then return nil end
    local ok, code = pcall(function() return entry.custom_code end)
    return ok and type(code) == "string" and code or nil
end

local function entry_text(entry)
    local ok, text = pcall(function() return entry.text end)
    return ok and type(text) == "string" and text or nil
end

local function entry_commits(entry)
    local ok, commits = pcall(function() return entry.commit_count end)
    commits = ok and tonumber(commits) or 0
    if commits == nil or commits <= 0 then return 0 end
    return math.min(math.floor(commits), 1000000)
end

function M.normalize_code(code)
    return pure_double_pinyin(code)
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
            local text = entry_text(entry)
            local raw_code = entry_code(entry)
            local commits = entry_commits(entry)
            local code = pure_double_pinyin(raw_code)
            local text_length = text and #text or 0
            if code and text and commits > 0 and #code >= 4 and #code <= MAX_CODE_BYTES and
                text_length > 0 and text_length <= MAX_TEXT_BYTES and valid_utf8(text) and
                (utf8.len(text) or 0) > 1 and not is_builtin(memory, raw_code, text) then
                local key = code .. "\t" .. text
                if not seen[key] then
                    seen[key] = true
                    result[#result + 1] = { code = code, text = text, commits = commits }
                end
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

M._test = {
    pure_double_pinyin = pure_double_pinyin,
    valid_utf8 = valid_utf8,
    is_builtin = is_builtin,
}

return M
