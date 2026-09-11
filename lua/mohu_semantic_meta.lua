-- Mohu Semantic Metadata ABI（受控语义导出前置件）
-- Copyright (c) 2026 ksqsf
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- docs/knowledge/qwen-semantic-reranker-training.md「Controlled metadata-bridge
-- acceptance boundary」的可执行前置件：为 qwen 语义候选重排导出提供
-- 生产者侧元数据绑定 ABI。默认不接入任何 schema；仅供隔离 staging 导出
-- 与配套测试使用。
--
-- 契约（违反即导出整单拒绝，绝不推断补值）：
--   bind(cand, meta)    生产者在创建候选时绑定一次；重复绑定、非法输入
--                       均失败。绑定做浅拷贝，生产者事后改表不影响已绑值。
--   resolve(cand)       从最外层沿 get_genuine() 向内解析（外层优先，
--                       深度受界，自引用/环失败返回 nil）。没有绑定的
--                       候选返回 nil，由观察侧整单拒绝，而不是跳过。
--   resolve_menu(list)  仅供未来最终菜单观察器使用：逐项遍历完整 genuine
--                       链，拒绝链内多重绑定、跨菜单复用、缺失绑定或异常链；
--                       成功时返回与最终菜单位置一一对应的元数据数组。
--   codepoint_span(raw, start0, end_incl)
--                       Rime 候选的 0 基 start 与 1 基包含式 _end 是
--                       Context.input 的 UTF-8 字节边界；本函数受检转换
--                       为协议要求的 0 基码点偏移 (start, exclusive_end)。
--                       边界落在码点中间、非法/截断 UTF-8 一律失败。
--   exportable_raw_input(raw)
--                       协议要求 NFC 字符串；Lua 侧只能对纯 ASCII 证明
--                       NFC 不变性，非 ASCII 原始输入整体拒绝导出。

local M = {}

-- 弱键注册表：候选 userdata 由流水线持有，候选被回收时绑定随之释放。
local bindings = setmetatable({}, { __mode = "k" })

local MAX_GENUINE_DEPTH = 8

local function is_candidate_like(value)
  return type(value) == "table" or type(value) == "userdata"
end

-- 绑定一次不可变（浅拷贝）元数据。成功返回 true；失败返回 false, reason。
function M.bind(candidate, meta)
  if not is_candidate_like(candidate) then
    return false, "candidate must be a table or userdata"
  end
  if type(meta) ~= "table" then
    return false, "meta must be a table"
  end
  if bindings[candidate] ~= nil then
    return false, "candidate already bound"
  end
  local frozen = {}
  for key, value in pairs(meta) do frozen[key] = value end
  bindings[candidate] = frozen
  return true
end

-- 沿 get_genuine() 链解析绑定；找不到返回 nil（观察侧必须整单拒绝）。
function M.resolve(candidate)
  local current = candidate
  local depth = 0
  while current ~= nil do
    if not is_candidate_like(current) then
      return nil
    end
    local meta = bindings[current]
    if meta ~= nil then
      return meta
    end
    local getter = current.get_genuine
    if type(getter) ~= "function" then
      return nil
    end
    local ok, genuine = pcall(getter, current)
    if not ok then
      return nil
    end
    if genuine == current then
      return nil
    end
    depth = depth + 1
    if depth > MAX_GENUINE_DEPTH then
      return nil
    end
    current = genuine
  end
  return nil
end

-- 逐个收集最终菜单候选的唯一 producer binding。与 resolve() 的外层优先
-- 查询不同，这个入口会检查整条 genuine 链：多层绑定无法表明哪一个 producer
-- 对最终展示负责，因而整单拒绝。最终菜单中同一 binding 出现两次也无从分辨
-- 两个菜单项是否独立，必须拒绝而非按 text 或 rank 猜测。
function M.resolve_menu(candidates)
  if type(candidates) ~= "table" then
    return nil, "menu must be an array table"
  end

  local count = #candidates
  if count == 0 then
    return nil, "menu must not be empty"
  end
  for key in pairs(candidates) do
    if type(key) ~= "number" or key < 1 or key ~= math.floor(key) or key > count then
      return nil, "menu must be a contiguous array"
    end
  end

  local resolved = {}
  local seen_meta = {}
  for index = 1, count do
    local candidate = candidates[index]
    local current = candidate
    local depth = 0
    local chain_seen = {}
    local found = nil

    while current ~= nil do
      if not is_candidate_like(current) or chain_seen[current] then
        return nil, "candidate " .. index .. " has an invalid genuine chain"
      end
      chain_seen[current] = true

      local bound = bindings[current]
      if bound ~= nil then
        if found ~= nil then
          return nil, "candidate " .. index .. " has ambiguous provenance"
        end
        found = bound
      end

      local getter = current.get_genuine
      if type(getter) ~= "function" then
        break
      end
      local ok, genuine = pcall(getter, current)
      if not ok then
        return nil, "candidate " .. index .. " has an invalid genuine chain"
      end
      -- Native candidates commonly expose themselves as the genuine terminal.
      -- This is a valid end state; a missing binding still fails below.
      if genuine == current then
        break
      end
      depth = depth + 1
      if depth > MAX_GENUINE_DEPTH then
        return nil, "candidate " .. index .. " exceeds genuine depth limit"
      end
      current = genuine
    end

    if found == nil then
      return nil, "candidate " .. index .. " has no provenance"
    end
    if seen_meta[found] then
      return nil, "candidate " .. index .. " reuses provenance"
    end
    seen_meta[found] = true
    resolved[index] = found
  end
  return resolved
end

-- 字节边界 → 码点偏移映射表：map[1 基字节位置] = 该位置前的完整码点数。
-- 非法/截断/续字节错位 UTF-8 返回 nil, reason。
local function boundary_map(raw)
  local byte_length = #raw
  local map = { [1] = 0 }
  local byte = 1
  local codepoints = 0
  while byte <= byte_length do
    local lead = string.byte(raw, byte)
    local width
    if lead < 0x80 then
      width = 1
    elseif lead >= 0xC0 and lead < 0xE0 then
      width = 2
    elseif lead >= 0xE0 and lead < 0xF0 then
      width = 3
    elseif lead >= 0xF0 and lead < 0xF8 then
      width = 4
    else
      return nil, "invalid UTF-8 lead byte"
    end
    if byte + width - 1 > byte_length then
      return nil, "truncated UTF-8 sequence"
    end
    for offset = 2, width do
      local continuation = string.byte(raw, byte + offset - 1)
      if continuation < 0x80 or continuation >= 0xC0 then
        return nil, "invalid UTF-8 continuation byte"
      end
    end
    codepoints = codepoints + 1
    byte = byte + width
    map[byte] = codepoints
  end
  return map
end

-- Rime 字节跨度（candidate.start 0 基、candidate._end 1 基包含）→
-- 协议码点跨度（0 基 start、exclusive end）。失败返回 nil, reason。
function M.codepoint_span(raw, start0, end_inclusive)
  if type(raw) ~= "string" then
    return nil, "raw must be a string"
  end
  local start_byte = tonumber(start0)
  local end_byte = tonumber(end_inclusive)
  if type(start_byte) ~= "number" or start_byte ~= math.floor(start_byte) or
      start_byte < 0 or start_byte > #raw then
    return nil, "invalid start byte offset"
  end
  if type(end_byte) ~= "number" or end_byte ~= math.floor(end_byte) or
      end_byte < 0 or end_byte > #raw then
    return nil, "invalid end byte offset"
  end
  if end_byte < start_byte then
    return nil, "end before start"
  end
  local map, map_error = boundary_map(raw)
  if map == nil then
    return nil, map_error
  end
  local cp_start = map[start_byte + 1]
  local cp_end = map[end_byte + 1]
  if cp_start == nil then
    return nil, "start is not a UTF-8 boundary"
  end
  if cp_end == nil then
    return nil, "end is not a UTF-8 boundary"
  end
  return cp_start, cp_end
end

-- 协议要求 NFC；仅纯 ASCII 可在 Lua 侧证明 NFC 不变性，其余整体拒绝。
function M.exportable_raw_input(raw)
  if type(raw) ~= "string" then
    return false
  end
  for index = 1, #raw do
    if string.byte(raw, index) >= 0x80 then
      return false
    end
  end
  return true
end

return M
