-- Local Qwen sentence scorer adapter.
--
-- The native sentence translator is deliberately the only caller of this
-- module.  A scorer may be exposed by the tigerengine Lua binding, by a
-- line-oriented Unix socket, or (only when explicitly configured) by a
-- direct HTTP score endpoint.  Every failure is fail-open.

local M = {}

local CACHE_LIMIT = 64
local MIN_CANDIDATES = 2
local MAX_CANDIDATES = 20
local MAX_SOCKET_RESPONSE_BYTES = 65536
local DEFAULT_TIMEOUT_MS = 20
-- A full adaptive request is materially more expensive than the normal
-- five-row request on Apple Silicon.  Keep the fast-path deadline separate so
-- an unavailable scorer never stalls ordinary keypresses for the full budget.
local DEFAULT_FULL_TIMEOUT_MS = 140
local OPTION_NAME = "mohu_tiger_sentence_neural_rerank"
local MAX_ALPHA = 16
local DEFAULT_SHORTLIST_MIN_K = 2
local DEFAULT_SHORTLIST_MARGIN = 0
local DEFAULT_FUSION_NORMALIZATION = "rank"
local FAST_BATCH_ROWS = 5

local state = {
  cache = {},
  cache_order = {},
  request_number = 0,
  transport = nil,
  profile = nil,
  profile_error = nil,
  profile_loaded = false,
  log_seen = {},
  socket_module = false,
  socket_clients = {},
  transport_strict = false,
  references = 0,
  clock_fn = false,
}

local close_socket
local close_all_sockets
local profile_signature

local function inject_lua_module_paths()
  if not package then return end
  local version = type(_VERSION) == "string" and
    (_VERSION:match("Lua (%d+%.%d+)") or "5.4") or "5.4"
  local home = ""
  if os and type(os.getenv) == "function" then
    local ok, value = pcall(os.getenv, "HOME")
    if ok and type(value) == "string" then home = value end
  end
  local user_dir = ""
  if rime_api and type(rime_api.get_user_data_dir) == "function" then
    local ok, value = pcall(rime_api.get_user_data_dir)
    if ok and type(value) == "string" then user_dir = value end
  end
  local lua_paths = {
    user_dir ~= "" and user_dir .. "/lua/?.lua" or nil,
    user_dir ~= "" and user_dir .. "/lua/rocks/share/lua/" .. version .. "/?.lua" or nil,
    user_dir ~= "" and user_dir .. "/lua/rocks/share/lua/" .. version .. "/?/init.lua" or nil,
    home ~= "" and home .. "/.luarocks/share/lua/" .. version .. "/?.lua" or nil,
    home ~= "" and home .. "/.luarocks/share/lua/" .. version .. "/?/init.lua" or nil,
    "/opt/homebrew/share/lua/" .. version .. "/?.lua",
    "/opt/homebrew/share/lua/" .. version .. "/?/init.lua",
  }
  local c_paths = {
    user_dir ~= "" and user_dir .. "/lua/rocks/lib/lua/" .. version .. "/?.so" or nil,
    home ~= "" and home .. "/.luarocks/lib/lua/" .. version .. "/?.so" or nil,
    "/opt/homebrew/lib/lua/" .. version .. "/?.so",
  }
  local function append(field, values)
    local current = package[field] or ""
    for _, value in ipairs(values) do
      if value and not current:find(value, 1, true) then
        current = current .. ";" .. value
      end
    end
    package[field] = current
  end
  append("path", lua_paths)
  append("cpath", c_paths)
end

inject_lua_module_paths()

local function finite(value)
  return type(value) == "number" and value == value and
    value ~= math.huge and value ~= -math.huge
end

local function integer(value)
  return type(value) == "number" and value == math.floor(value)
end

local function nonnegative_or_infinite(value)
  return (finite(value) and value >= 0) or value == math.huge
end

local function log_once(key, message)
  if state.log_seen[key] then return end
  state.log_seen[key] = true
  if log and type(log.error) == "function" then
    pcall(log.error, "mohu_tiger_reranker: " .. tostring(message))
  end
end

local function utf8_length(text)
  if type(text) ~= "string" then return 0 end
  local count = 0
  local index = 1
  while index <= #text do
    local byte = text:byte(index)
    local width
    if byte < 0x80 then
      width = 1
    elseif byte >= 0xC2 and byte <= 0xDF then
      width = 2
    elseif byte >= 0xE0 and byte <= 0xEF then
      width = 3
    elseif byte >= 0xF0 and byte <= 0xF4 then
      width = 4
    else
      return nil
    end
    if index + width - 1 > #text then return nil end
    for offset = 1, width - 1 do
      local continuation = text:byte(index + offset)
      if continuation < 0x80 or continuation > 0xBF then return nil end
    end
    -- Reject overlong and out-of-range sequences.  Candidate text is sent
    -- to another process, so accepting malformed UTF-8 here is undesirable.
    if width == 3 then
      local b2 = text:byte(index + 1)
      if byte == 0xE0 and b2 < 0xA0 then return nil end
      if byte == 0xED and b2 > 0x9F then return nil end
    elseif width == 4 then
      local b2 = text:byte(index + 1)
      if byte == 0xF0 and b2 < 0x90 then return nil end
      if byte == 0xF4 and b2 > 0x8F then return nil end
    end
    count = count + 1
    index = index + width
  end
  return count
end

-- -------------------------------------------------------------------------
-- Small JSON codec.  Rime distributions do not consistently ship cjson or
-- LuaSocket, so the wire format cannot depend on either package.

local function json_escape(text)
  local replacements = {
    ["\\"] = "\\\\",
    ["\""] = "\\\"",
    ["\b"] = "\\b",
    ["\f"] = "\\f",
    ["\n"] = "\\n",
    ["\r"] = "\\r",
    ["\t"] = "\\t",
  }
  return text:gsub('["\\%z\1-\31]', function(char)
    local replacement = replacements[char]
    if replacement then return replacement end
    return string.format("\\u%04x", char:byte())
  end)
end

local function json_array(value)
  if type(value) ~= "table" then return false end
  local count = 0
  for key in pairs(value) do
    if type(key) ~= "number" or key < 1 or key ~= math.floor(key) then
      return false
    end
    count = count + 1
  end
  return count == #value
end

local function json_encode(value)
  local function encode(item, depth)
    if depth > 20 then error("json nesting too deep") end
    local kind = type(item)
    if kind == "nil" then return "null" end
    if kind == "boolean" then return item and "true" or "false" end
    if kind == "number" then
      if not finite(item) then return "null" end
      return string.format("%.17g", item)
    end
    if kind == "string" then return '"' .. json_escape(item) .. '"' end
    if kind ~= "table" then error("unsupported json value") end
    local parts = {}
    if json_array(item) then
      for index = 1, #item do
        parts[#parts + 1] = encode(item[index], depth + 1)
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for key in pairs(item) do
      if type(key) == "string" then keys[#keys + 1] = key end
    end
    table.sort(keys)
    for _, key in ipairs(keys) do
      parts[#parts + 1] = encode(key, depth + 1) .. ":" ..
        encode(item[key], depth + 1)
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  local ok, result = pcall(encode, value, 0)
  return ok and result or nil
end

local function utf8_from_codepoint(codepoint)
  if codepoint <= 0x7f then
    return string.char(codepoint)
  elseif codepoint <= 0x7ff then
    return string.char(0xc0 + math.floor(codepoint / 0x40),
      0x80 + codepoint % 0x40)
  elseif codepoint <= 0xffff then
    return string.char(0xe0 + math.floor(codepoint / 0x1000),
      0x80 + math.floor(codepoint / 0x40) % 0x40,
      0x80 + codepoint % 0x40)
  elseif codepoint <= 0x10ffff then
    return string.char(0xf0 + math.floor(codepoint / 0x40000),
      0x80 + math.floor(codepoint / 0x1000) % 0x40,
      0x80 + math.floor(codepoint / 0x40) % 0x40,
      0x80 + codepoint % 0x40)
  end
  return nil
end

local function json_decode(text)
  if type(text) ~= "string" or #text > 65536 then return nil end
  local position = 1
  local length = #text

  local function skip_space()
    while position <= length and text:sub(position, position):match("%s") do
      position = position + 1
    end
  end

  local parse_value
  local function parse_string()
    if text:sub(position, position) ~= '"' then return nil end
    position = position + 1
    local parts = {}
    while position <= length do
      local char = text:sub(position, position)
      position = position + 1
      if char == '"' then return table.concat(parts) end
      if char == "\\" then
        if position > length then return nil end
        local escaped = text:sub(position, position)
        position = position + 1
        local simple = {
          ['"'] = '"', ['\\'] = "\\", ['/'] = "/",
          b = "\b", f = "\f", n = "\n", r = "\r", t = "\t",
        }
        if simple[escaped] then
          parts[#parts + 1] = simple[escaped]
        elseif escaped == "u" then
          local hex = text:sub(position, position + 3)
          if not hex:match("^%x%x%x%x$") then return nil end
          position = position + 4
          local codepoint = tonumber(hex, 16)
          -- Combine a UTF-16 surrogate pair when present.
          if codepoint >= 0xd800 and codepoint <= 0xdbff and
            text:sub(position, position + 1) == "\\u" then
            local low_hex = text:sub(position + 2, position + 5)
            local low = low_hex:match("^%x%x%x%x$") and tonumber(low_hex, 16)
            if low and low >= 0xdc00 and low <= 0xdfff then
              codepoint = 0x10000 + (codepoint - 0xd800) * 0x400 +
                low - 0xdc00
              position = position + 6
            end
          end
          local utf8 = utf8_from_codepoint(codepoint)
          if not utf8 then return nil end
          parts[#parts + 1] = utf8
        else
          return nil
        end
      elseif char:byte() < 0x20 then
        return nil
      else
        parts[#parts + 1] = char
      end
    end
    return nil
  end

  local function parse_number()
    local start = position
    local remainder = text:sub(position):match(
      "^-?%d+%.?%d*[eE]?[+-]?%d*")
    if not remainder or remainder == "" or not remainder:match(
      "^-?%d+%.?%d*[eE]?[+-]?%d*$") then
      return nil
    end
    position = position + #remainder
    local number = tonumber(remainder)
    if not finite(number) then return nil end
    if position == start then return nil end
    return number
  end

  local function parse_array()
    if text:sub(position, position) ~= "[" then return nil end
    position = position + 1
    skip_space()
    local result = {}
    if text:sub(position, position) == "]" then
      position = position + 1
      return result
    end
    while position <= length do
      local value = parse_value()
      if value == nil then return nil end
      result[#result + 1] = value
      skip_space()
      local delimiter = text:sub(position, position)
      position = position + 1
      if delimiter == "]" then return result end
      if delimiter ~= "," then return nil end
      skip_space()
    end
    return nil
  end

  local function parse_object()
    if text:sub(position, position) ~= "{" then return nil end
    position = position + 1
    skip_space()
    local result = {}
    if text:sub(position, position) == "}" then
      position = position + 1
      return result
    end
    while position <= length do
      local key = parse_string()
      if key == nil then return nil end
      skip_space()
      if text:sub(position, position) ~= ":" then return nil end
      position = position + 1
      skip_space()
      local value = parse_value()
      if value == nil then return nil end
      result[key] = value
      skip_space()
      local delimiter = text:sub(position, position)
      position = position + 1
      if delimiter == "}" then return result end
      if delimiter ~= "," then return nil end
      skip_space()
    end
    return nil
  end

  function parse_value()
    skip_space()
    local prefix = text:sub(position, position)
    if prefix == '"' then return parse_string() end
    if prefix == "{" then return parse_object() end
    if prefix == "[" then return parse_array() end
    if text:sub(position, position + 3) == "true" then
      position = position + 4
      return true
    end
    if text:sub(position, position + 4) == "false" then
      position = position + 5
      return false
    end
    if text:sub(position, position + 3) == "null" then
      position = position + 4
      return false, true
    end
    return parse_number()
  end

  local ok, value, is_null = pcall(parse_value)
  if not ok or is_null then value = nil end
  if value == nil then return nil end
  skip_space()
  if position <= length then return nil end
  return value
end

-- -------------------------------------------------------------------------
-- Profile and configuration

local function validate_profile(profile)
  if type(profile) ~= "table" then return nil, "profile is not a table" end
  local schema = profile.schema
  if schema ~= 1 then return nil, "profile schema must be 1" end
  local model_id = profile.model_id or profile.model_name or profile.model
  local model_path = profile.model_path or profile.path
  local model_sha256 = profile.model_sha256 or profile.sha256
  if type(model_id) ~= "string" or model_id == "" then
    return nil, "profile model_id is missing"
  end
  if type(model_path) ~= "string" or model_path == "" then
    return nil, "profile model_path is missing"
  end
  if type(model_sha256) ~= "string" or #model_sha256 ~= 64 or
    not model_sha256:match("^[0-9a-f]+$") then
    return nil, "profile model_sha256 must be 64 lowercase hex characters"
  end
  local normalization = profile.normalization
  local allowed_normalization = {
    sum_token_logp = true,
    mean_token_logp = true,
  }
  if type(normalization) ~= "string" or not allowed_normalization[normalization] then
    return nil, "profile normalization is invalid"
  end
  local alpha = profile.alpha
  if not finite(alpha) or alpha <= 0 or alpha > MAX_ALPHA then
    return nil, "profile alpha is invalid"
  end
  local top_k = profile.base_top_k or profile.top_k
  if not integer(top_k) or top_k < MIN_CANDIDATES or top_k > MAX_CANDIDATES then
    return nil, "profile top_k must be an integer from 2 to 20"
  end
  -- ``top_k`` is the normal request budget.  Ambiguous native output may be
  -- expanded once, up to ``max_top_k``; keeping the two values separate makes
  -- the latency/quality trade-off explicit in the profile.
  local min_top_k = profile.min_top_k or profile.shortlist_min_k or
    DEFAULT_SHORTLIST_MIN_K
  local max_top_k = profile.max_top_k or profile.shortlist_max_k or top_k
  if not integer(min_top_k) or min_top_k < MIN_CANDIDATES or
    min_top_k > top_k then
    return nil, "profile min_top_k must be an integer from 2 to top_k"
  end
  if not integer(max_top_k) or max_top_k < top_k or
    max_top_k > MAX_CANDIDATES then
    return nil, "profile max_top_k must be an integer from top_k to 20"
  end
  local adaptive_margin = profile.adaptive_margin
  if adaptive_margin ~= nil and not nonnegative_or_infinite(adaptive_margin) then
    return nil, "profile adaptive_margin is invalid"
  end
  local uncertainty_margin = profile.uncertainty_margin
  if uncertainty_margin ~= nil and not nonnegative_or_infinite(uncertainty_margin) then
    return nil, "profile uncertainty_margin is invalid"
  end
  local confidence_margin = profile.shortlist_confidence_margin
  if confidence_margin == nil then confidence_margin = profile.confidence_margin end
  if confidence_margin == nil then confidence_margin = adaptive_margin end
  if confidence_margin == nil then confidence_margin = uncertainty_margin end
  if confidence_margin == nil then confidence_margin = DEFAULT_SHORTLIST_MARGIN end
  local score_margin = profile.shortlist_score_margin
  if score_margin == nil then score_margin = profile.score_margin end
  if score_margin == nil then score_margin = adaptive_margin end
  if score_margin == nil then score_margin = uncertainty_margin end
  if score_margin == nil then score_margin = DEFAULT_SHORTLIST_MARGIN end
  if not nonnegative_or_infinite(confidence_margin) or
    not nonnegative_or_infinite(score_margin) then
    return nil, "profile shortlist margins are invalid"
  end
  local diversity_threshold = profile.diversity_threshold
  if diversity_threshold == nil then diversity_threshold = 0 end
  if not finite(diversity_threshold) or diversity_threshold < 0 or
    diversity_threshold > 1 then
    return nil, "profile diversity_threshold is invalid"
  end
  local adaptive = profile.adaptive
  if adaptive == nil then adaptive = max_top_k > top_k end
  if adaptive ~= true and adaptive ~= false then
    return nil, "profile adaptive must be boolean"
  end
  local fusion_normalization = profile.fusion_normalization or
    profile.fusion or DEFAULT_FUSION_NORMALIZATION
  if fusion_normalization == "z" then fusion_normalization = "zscore" end
  if fusion_normalization ~= "rank" and fusion_normalization ~= "zscore" and
    fusion_normalization ~= "rank_z" then
    return nil, "profile fusion_normalization is invalid"
  end
  local min_raw_len = profile.min_raw_len
  if not integer(min_raw_len) or min_raw_len < 0 then
    return nil, "profile min_raw_len is invalid"
  end
  local max_conf_gap = profile.max_conf_gap
  if not finite(max_conf_gap) and max_conf_gap ~= math.huge then
    return nil, "profile max_conf_gap is invalid"
  end
  if max_conf_gap < 0 then return nil, "profile max_conf_gap is invalid" end
  return {
    schema = 1,
    model_id = model_id,
    model_selection_id = profile.model_selection_id,
    model_path = model_path,
    model_sha256 = model_sha256,
    catalog_status = profile.catalog_status,
    model_available = profile.model_available,
    normalization = normalization,
    alpha = alpha,
    top_k = top_k,
    base_top_k = top_k,
    adaptive = adaptive,
    min_top_k = min_top_k,
    max_top_k = max_top_k,
    shortlist_confidence_margin = confidence_margin,
    shortlist_score_margin = score_margin,
    uncertainty_margin = uncertainty_margin or adaptive_margin or
      math.max(confidence_margin, score_margin),
    diversity_threshold = diversity_threshold,
    fusion_normalization = fusion_normalization,
    min_raw_len = min_raw_len,
    max_conf_gap = max_conf_gap,
  }
end

profile_signature = function(profile)
  if not profile then return "" end
  local values = {
    profile.schema, profile.model_id, profile.model_selection_id or "",
    profile.model_path,
    profile.model_sha256, profile.catalog_status or "",
    profile.model_available == nil and "" or profile.model_available,
    profile.normalization, profile.alpha,
    profile.top_k, profile.base_top_k, profile.adaptive,
    profile.min_top_k, profile.max_top_k,
    profile.shortlist_confidence_margin, profile.shortlist_score_margin,
    profile.uncertainty_margin, profile.diversity_threshold,
    profile.fusion_normalization, profile.min_raw_len, profile.max_conf_gap,
  }
  for index, value in ipairs(values) do values[index] = tostring(value) end
  return table.concat(values, "\30")
end

local function load_profile(force)
  if state.profile_loaded and not force then
    return state.profile, state.profile_error
  end
  local previous_signature = profile_signature and profile_signature(state.profile) or ""
  state.profile_loaded = true
  state.profile = nil
  state.profile_error = nil
  local ok, loaded = pcall(require, "mohu_tiger_reranker_profile")
  if not ok then
    state.profile_error = tostring(loaded)
    if previous_signature ~= "" then
      state.cache = {}
      state.cache_order = {}
    end
    -- A profile is optional.  Missing profiles are intentionally quiet; an
    -- installed but malformed profile gets one diagnostic for troubleshooting.
    if not tostring(loaded):match("module 'mohu_tiger_reranker_profile' not found") then
      log_once("profile", state.profile_error)
    end
    return nil, state.profile_error
  end
  if type(loaded) == "table" and type(loaded.profile) == "table" then
    loaded = loaded.profile
  end
  local profile, err = validate_profile(loaded)
  if not profile then
    state.profile_error = err
    if previous_signature ~= "" then
      state.cache = {}
      state.cache_order = {}
    end
    log_once("profile", err)
    return nil, err
  end
  state.profile = profile
  if previous_signature ~= profile_signature(profile) then
    state.cache = {}
    state.cache_order = {}
  end
  return profile
end

local function config_value(config, method, key, default)
  if not config or type(config[method]) ~= "function" then return default end
  local ok, value = pcall(config[method], config, key)
  if not ok or value == nil then return default end
  return value
end

local function config_string(config, key, default)
  local value = config_value(config, "get_string", key, nil)
  if value == nil then return default end
  value = tostring(value)
  return value == "" and default or value
end

local function config_number(config, key, default)
  local value = config_value(config, "get_int", key, nil)
  if value == nil then value = config_value(config, "get_double", key, nil) end
  value = tonumber(value)
  return finite(value) and value or default
end

local function user_data_dir()
  if rime_api and type(rime_api.get_user_data_dir) == "function" then
    local ok, value = pcall(rime_api.get_user_data_dir)
    if ok and type(value) == "string" and value ~= "" then return value end
  end
  return "."
end

local function resolve_user_path(path)
  if type(path) ~= "string" or path == "" then return path end
  if path:sub(1, 1) == "/" or path:match("^%a:[/\\]") then return path end
  if path:sub(1, 2) == "~/" then
    local home = os.getenv("HOME")
    if home and home ~= "" then return home .. path:sub(2) end
  end
  return user_data_dir() .. "/" .. path
end

local function env_config(env, profile)
  local config = env and env.engine and env.engine.schema and env.engine.schema.config
  local configured_socket = config_string(config, "tiger/rerank_socket", "")
  local socket_path = configured_socket
  if socket_path == "" and profile then
    socket_path = user_data_dir() .. "/tiger/qwen35-reranker.sock"
  else
    socket_path = resolve_user_path(socket_path)
  end
  local configured_service = config_string(config, "tiger/rerank_service", "")
  local service_path = resolve_user_path(configured_service)
  local http_endpoint = config_string(config, "tiger/rerank_http_endpoint", "")
  if http_endpoint == "" then
    http_endpoint = config_string(config, "tiger/rerank_endpoint", "")
  end
  local configured_model = config_string(config, "tiger/rerank_model", "")
  local model_path = configured_model ~= "" and resolve_user_path(configured_model) or
    (profile and profile.model_path or "")
  local timeout_ms = config_number(config, "tiger/rerank_timeout_ms",
    DEFAULT_TIMEOUT_MS)
  timeout_ms = math.max(1, math.min(1000, math.floor(timeout_ms)))
  local full_timeout_ms = config_number(config, "tiger/rerank_full_timeout_ms",
    math.max(timeout_ms, DEFAULT_FULL_TIMEOUT_MS))
  full_timeout_ms = math.max(timeout_ms,
    math.min(1000, math.floor(full_timeout_ms)))
  return {
    socket = socket_path,
    socket_explicit = configured_socket ~= "",
    service = service_path,
    http = http_endpoint,
    model = model_path,
    timeout_ms = timeout_ms,
    full_timeout_ms = full_timeout_ms,
  }
end

local function config_signature(config)
  if not config then return "" end
  return table.concat({
    config.socket or "", config.service or "", config.http or "",
    config.model or "", config.timeout_ms or "", config.full_timeout_ms or "",
  }, "\28")
end

local function env_state(env)
  if not env then
    return {
      profile = state.profile,
      config = {},
    }
  end
  if not env._tiger_reranker then env._tiger_reranker = {} end
  local current = env._tiger_reranker
  local profile = load_profile()
  local signature = profile_signature(profile)
  if current.profile_signature and current.profile_signature ~= signature then
    if close_all_sockets then close_all_sockets() end
    M.clear_cache()
  end
  current.profile = profile
  current.profile_signature = signature
  local config = env_config(env, profile)
  local cfg_signature = config_signature(config)
  if current.config_signature and current.config_signature ~= cfg_signature then
    if close_all_sockets then close_all_sockets() end
    M.clear_cache()
  end
  current.config = config
  current.config_signature = cfg_signature
  return current
end

function M.init(env)
  local current = env_state(env)
  if current then
    if not current.reference_active then
      current.reference_active = true
      state.references = state.references + 1
    end
    current.initialized = true
  end
  return current and current.profile
end

function M.fini(env)
  local current = env and env._tiger_reranker
  if current then
    if current.reference_active then
      current.reference_active = false
      state.references = math.max(0, state.references - 1)
    end
    current.initialized = false
  end
  if state.references > 0 then return end
  if close_all_sockets then close_all_sockets() end
  M.clear_cache()
  -- A Lua module can survive a schema reload in the same VM.  Drop all
  -- reload-sensitive state so a replacement profile or newly installed
  -- LuaSocket is discovered on the next init.
  state.profile_loaded = false
  state.profile = nil
  state.profile_error = nil
  state.socket_module = false
  state.clock_fn = false
  state.transport = nil
  state.transport_strict = false
  state.http_runner = nil
  state.log_seen = {}
  if package and package.loaded then
    package.loaded["mohu_tiger_reranker_profile"] = nil
  end
end

function M.neural_enabled(context)
  if not context or type(context.get_option) ~= "function" then return false end
  local ok, enabled = pcall(context.get_option, context, OPTION_NAME)
  return ok and (enabled == true or enabled == 1 or enabled == "true")
end

-- -------------------------------------------------------------------------
-- Cache and score validation

local function cache_key(raw, texts, profile, configured_model, context_text,
    shortlist_k)
  local parts = {}
  local function append(value)
    value = tostring(value or "")
    parts[#parts + 1] = tostring(#value)
    parts[#parts + 1] = ":"
    parts[#parts + 1] = value
    parts[#parts + 1] = ";"
  end
  append(profile.model_id)
  append(profile.model_path)
  append(profile.model_sha256)
  append(configured_model or profile.model_path)
  append(context_text or "")
  append(raw)
  append(shortlist_k or #texts)
  for _, text in ipairs(texts) do append(text) end
  return table.concat(parts, "\29")
end

local function remove_cache_key(key)
  for index, existing in ipairs(state.cache_order) do
    if existing == key then
      table.remove(state.cache_order, index)
      return
    end
  end
end

local function cache_get(key)
  local value = state.cache[key]
  if not value then return nil end
  remove_cache_key(key)
  state.cache_order[#state.cache_order + 1] = key
  local copy = {}
  for index, item in ipairs(value) do
    copy[index] = { sum_logp = item.sum_logp, predicted_tokens = item.predicted_tokens }
  end
  return copy
end

local function cache_put(key, scores)
  local copy = {}
  for index, item in ipairs(scores) do
    copy[index] = { sum_logp = item.sum_logp, predicted_tokens = item.predicted_tokens }
  end
  if state.cache[key] then remove_cache_key(key) end
  state.cache[key] = copy
  state.cache_order[#state.cache_order + 1] = key
  while #state.cache_order > CACHE_LIMIT do
    local oldest = table.remove(state.cache_order, 1)
    state.cache[oldest] = nil
  end
end

function M.clear_cache()
  state.cache = {}
  state.cache_order = {}
end

-- Public hook for model-selection changes.  The next rerank/init call loads a
-- fresh catalog-backed profile; no model weights or large files are touched.
function M.reload_profile()
  if close_all_sockets then close_all_sockets() end
  M.clear_cache()
  state.profile_loaded = false
  state.profile = nil
  state.profile_error = nil
  state.log_seen = {}
  if package and package.loaded then
    package.loaded["mohu_tiger_reranker_profile"] = nil
    package.loaded["mohu_tiger_model_catalog"] = nil
  end
  return nil
end

local function validate_scores(scores, count)
  if type(scores) ~= "table" or #scores ~= count then return nil end
  local result = {}
  for index = 1, count do
    local item = scores[index]
    if type(item) ~= "table" then return nil end
    local sum_logp = tonumber(item.sum_logp or item.sum_token_logp)
    local predicted_tokens = tonumber(item.predicted_tokens or item.tokens)
    if not finite(sum_logp) or not integer(predicted_tokens) or predicted_tokens <= 0 then
      return nil
    end
    result[index] = {
      sum_logp = sum_logp,
      predicted_tokens = predicted_tokens,
    }
  end
  return result
end

local function normalize_scores(scores, normalization)
  local result = {}
  for index, item in ipairs(scores or {}) do
    local value = item
    local tokens = 1
    if type(item) == "table" then
      value = item.sum_logp or item.sum_token_logp
      tokens = item.predicted_tokens or item.tokens or 0
    end
    value = tonumber(value)
    tokens = tonumber(tokens)
    if not finite(value) or not integer(tokens) or tokens <= 0 then return nil end
    if normalization == "mean_token_logp" then
      value = value / tokens
    end
    if not finite(value) then return nil end
    result[index] = value
  end
  return result
end

-- Normalize an aligned score vector without depending on the native score
-- scale.  Centered rank is bounded and deterministic; z-score preserves
-- relative distances while returning all-zero evidence for a flat vector.
local function rank_normalize(values)
  if type(values) ~= "table" or #values == 0 then return nil end
  local indexed = {}
  for index, value in ipairs(values) do
    value = tonumber(value)
    if not finite(value) then return nil end
    indexed[index] = { value = value, index = index }
  end
  table.sort(indexed, function(left, right)
    if left.value == right.value then return left.index < right.index end
    return left.value > right.value
  end)
  local result = {}
  local count = #indexed
  if count == 1 then
    result[1] = 0
    return result
  end
  local position = 1
  while position <= count do
    local finish = position
    while finish < count and indexed[finish + 1].value == indexed[position].value do
      finish = finish + 1
    end
    local average_rank = (position + finish) / 2
    local normalized = (count + 1 - 2 * average_rank) / (count - 1)
    for cursor = position, finish do
      result[indexed[cursor].index] = normalized
    end
    position = finish + 1
  end
  return result
end

local function zscore_normalize(values)
  if type(values) ~= "table" or #values == 0 then return nil end
  local total = 0
  for _, value in ipairs(values) do
    value = tonumber(value)
    if not finite(value) then return nil end
    total = total + value
  end
  local mean = total / #values
  local squared = 0
  for _, value in ipairs(values) do
    local delta = value - mean
    squared = squared + delta * delta
  end
  local deviation = math.sqrt(squared / #values)
  local result = {}
  if deviation <= 1e-12 then
    for index = 1, #values do result[index] = 0 end
    return result
  end
  for index, value in ipairs(values) do
    local normalized = (value - mean) / deviation
    if not finite(normalized) then return nil end
    result[index] = normalized
  end
  return result
end

local function rank_z_normalize(values, mode)
  mode = mode or DEFAULT_FUSION_NORMALIZATION
  if mode == "z" then mode = "zscore" end
  if mode == "rank" then return rank_normalize(values) end
  if mode == "zscore" then return zscore_normalize(values) end
  if mode ~= "rank_z" then return nil end
  local ranks = rank_normalize(values)
  local zscores = zscore_normalize(values)
  if not ranks or not zscores then return nil end
  local result = {}
  for index = 1, #values do
    -- Bound the z component so one outlier cannot dominate the rank signal.
    local bounded_z = zscores[index] / (1 + math.abs(zscores[index]))
    result[index] = (ranks[index] + bounded_z) / 2
  end
  return result
end

local function common_prefix_bytes(left, right)
  local limit = math.min(#left, #right)
  local index = 1
  while index <= limit and left:byte(index) == right:byte(index) do
    index = index + 1
  end
  -- Candidate text is valid UTF-8 at the transport boundary.  Back up to a
  -- codepoint boundary if the differing byte split a multibyte character.
  while index > 1 and index <= #left and left:byte(index) >= 0x80 and
    left:byte(index) <= 0xbf do
    index = index - 1
  end
  return index - 1
end

local function common_candidate_prefix(items, count)
  if type(items) ~= "table" or count == nil or count < 2 then return "" end
  local prefix = items[1] and items[1].text
  if type(prefix) ~= "string" or prefix == "" then return "" end
  for index = 2, math.min(count, #items) do
    local text = items[index] and items[index].text
    if type(text) ~= "string" or text == "" then return "" end
    local length = common_prefix_bytes(prefix, text)
    prefix = prefix:sub(1, length)
    if prefix == "" then return "" end
  end
  for index = 1, math.min(count, #items) do
    local text = items[index] and items[index].text
    if type(text) ~= "string" or #prefix >= #text then return "" end
  end
  return prefix
end

local function candidate_diversity(items, count)
  if type(items) ~= "table" or count < 2 then return 0 end
  local total = 0
  local pairs = 0
  for left_index = 1, count - 1 do
    local left = items[left_index] and items[left_index].text
    if type(left) ~= "string" or left == "" then return nil end
    for right_index = left_index + 1, count do
      local right = items[right_index] and items[right_index].text
      if type(right) ~= "string" or right == "" then return nil end
      local denominator = math.max(#left, #right)
      local distance = denominator == 0 and 0 or
        1 - common_prefix_bytes(left, right) / denominator
      total = total + distance
      pairs = pairs + 1
    end
  end
  return pairs == 0 and 0 or total / pairs
end

local function choose_shortlist_k(items, profile)
  if type(items) ~= "table" or type(profile) ~= "table" then return nil end
  -- Accept both the new names and the legacy aliases here.  ``rerank``
  -- normally passes a validated profile, but keeping this policy helper
  -- self-contained avoids a transient reload turning adaptive selection off.
  local function number_or(value, fallback)
    value = tonumber(value)
    return finite(value) and value or fallback
  end
  local function threshold_or(value, fallback)
    value = tonumber(value)
    return nonnegative_or_infinite(value) and value or fallback
  end
  local base_limit = number_or(profile.base_top_k, nil)
  if base_limit == nil then base_limit = number_or(profile.top_k, 0) end
  local maximum = number_or(profile.max_top_k, base_limit)
  local available = math.max(0, math.min(#items, maximum, MAX_CANDIDATES))
  local base_k = math.min(available, base_limit)
  local min_limit = number_or(profile.min_top_k,
    number_or(profile.shortlist_min_k, DEFAULT_SHORTLIST_MIN_K))
  local min_k = math.min(base_k, min_limit)
  if available < MIN_CANDIDATES or base_k < MIN_CANDIDATES or min_k < MIN_CANDIDATES then
    return available
  end
  local adaptive = profile.adaptive
  if adaptive == nil then
    adaptive = maximum > base_limit
  elseif adaptive == "true" or adaptive == 1 then
    adaptive = true
  else
    adaptive = adaptive == true
  end
  local scan_limit = adaptive and available or base_k
  for index = 1, scan_limit do
    local item = items[index]
    if type(item) ~= "table" or not finite(tonumber(item.score)) or
      not finite(tonumber(item.confidence)) then
      return nil
    end
  end
  if not adaptive or available <= base_k then return base_k end

  local confidence_margin = threshold_or(profile.shortlist_confidence_margin, nil)
  if confidence_margin == nil then
    confidence_margin = threshold_or(profile.confidence_margin, nil)
  end
  if confidence_margin == nil then
    confidence_margin = threshold_or(profile.adaptive_margin, nil)
  end
  if confidence_margin == nil then
    confidence_margin = threshold_or(profile.uncertainty_margin,
      DEFAULT_SHORTLIST_MARGIN)
  end
  local score_margin = threshold_or(profile.shortlist_score_margin, nil)
  if score_margin == nil then
    score_margin = threshold_or(profile.score_margin, nil)
  end
  if score_margin == nil then
    score_margin = threshold_or(profile.adaptive_margin, nil)
  end
  if score_margin == nil then
    score_margin = threshold_or(profile.uncertainty_margin,
      DEFAULT_SHORTLIST_MARGIN)
  end
  local leader = items[1]
  local leader_score = tonumber(leader.score)
  local leader_confidence = tonumber(leader.confidence)
  local confidence_gap = math.abs(leader_confidence - tonumber(items[2].confidence))
  local score_gap = math.abs(leader_score - tonumber(items[2].score))
  -- Expand only when the native leader is genuinely ambiguous.  Either
  -- signal may request expansion because score and confidence capture
  -- different aspects of the native beam.
  if confidence_gap > confidence_margin and score_gap > score_margin then
    return base_k
  end
  local diversity = candidate_diversity(items, available)
  local diversity_threshold = number_or(profile.diversity_threshold, 0)
  if diversity == nil or diversity < diversity_threshold then
    return base_k
  end

  -- A positive diversity threshold is an explicit request to inspect the
  -- whole available pool once an ambiguous leader is detected.  A zero
  -- threshold keeps the legacy contiguous-margin behavior and is useful for
  -- profiles that want only a small expansion.  This remains a single
  -- pre-transport decision; it never starts a follow-up request.
  if diversity_threshold > 0 then
    return available
  end

  local selected = math.max(min_k, base_k)
  -- This is deliberately one bounded scan.  The complete K is chosen before
  -- transport; no retry, looped request, or background candidate growth.
  for index = selected + 1, available do
    local item = items[index]
    local candidate_score_gap = math.abs(leader_score - tonumber(item.score))
    local candidate_confidence_gap = math.abs(
      leader_confidence - tonumber(item.confidence))
    if candidate_score_gap <= score_margin or
      candidate_confidence_gap <= confidence_margin then
      selected = index
    else
      break
    end
  end
  return selected
end

local function blend_and_stable_sort(items, scores, alpha, fusion_normalization)
  if type(items) ~= "table" or type(scores) ~= "table" or
    not finite(alpha) then return nil end
  local count = math.min(#scores, #items, MAX_CANDIDATES)
  if count < 1 then return nil end
  local native_values = {}
  local neural_values = {}
  for index = 1, count do
    local item = items[index]
    if not item then return nil end
    native_values[index] = tonumber(item.score)
    neural_values[index] = tonumber(scores[index])
    if not finite(native_values[index]) or not finite(neural_values[index]) then
      return nil
    end
  end
  local native_normalized = rank_z_normalize(native_values, fusion_normalization)
  local neural_normalized = rank_z_normalize(neural_values, fusion_normalization)
  if not native_normalized or not neural_normalized then return nil end
  local rows = {}
  for index = 1, count do
    local item = items[index]
    local blended = native_normalized[index] + alpha * neural_normalized[index]
    if not finite(blended) then return nil end
    rows[index] = {
      item = item,
      value = blended,
      index = index,
    }
  end
  table.sort(rows, function(left, right)
    if left.value == right.value then return left.index < right.index end
    return left.value > right.value
  end)
  local result = {}
  for index = 1, count do result[index] = rows[index].item end
  for index = count + 1, #items do result[#result + 1] = items[index] end
  return result
end

-- -------------------------------------------------------------------------
-- Transport implementations

local function next_request_id()
  state.request_number = state.request_number + 1
  return string.format("mohu-%d", state.request_number)
end

local function response_model_hash(response)
  if type(response) ~= "table" then return nil end
  local top_level = response.model_sha256 or response.sha256
  if top_level ~= nil then return top_level end
  local model = response.model
  if type(model) ~= "table" then return nil end
  return model.sha256 or model.model_sha256
end

local function response_scores(response, request_id, count, expected_hash,
    require_hash, require_request_id, expected_normalization)
  if type(response) ~= "table" then return nil end
  if response.ok == false then return nil end
  if require_hash and response.ok ~= true then return nil end
  if response.version ~= nil and response.version ~= 1 then return nil end
  if require_hash and response.version ~= 1 then return nil end
  if response.status and response.status ~= "ok" then return nil end
  if require_hash and response.status ~= "ok" then return nil end
  if response.request_id and tostring(response.request_id) ~= tostring(request_id) then
    return nil
  end
  if require_request_id and not response.request_id then return nil end
  local hash = response_model_hash(response)
  if response.model ~= nil and type(response.model) ~= "table" then return nil end
  if response.normalize ~= nil and response.normalize ~= "sum_logp" and
    response.normalize ~= "mean_logp" then return nil end
  if expected_normalization and response.normalize and
    response.normalize ~= expected_normalization then return nil end
  -- A production response is tied to the calibrated profile.  Requiring the
  -- normalization marker prevents an older scorer (with the same checkpoint
  -- hash but different scoring semantics) from being accepted silently.
  if require_hash and expected_normalization and
    response.normalize ~= expected_normalization then return nil end
  if hash and hash ~= expected_hash then return nil end
  if require_hash and not hash then return nil end
  return validate_scores(response.scores, count)
end

local function load_socket_module()
  if state.socket_module ~= false then return state.socket_module end
  local ok, module = pcall(require, "socket.unix")
  if ok then
    state.socket_module = module
    return module
  end
  ok, module = pcall(require, "socket")
  if ok and type(module.unix) == "function" then
    state.socket_module = module.unix
    return state.socket_module
  end
  state.socket_module = nil
  return nil
end

local function load_clock()
  if state.clock_fn ~= false then return state.clock_fn end
  local ok, socket = pcall(require, "socket")
  if ok and type(socket.gettime) == "function" then
    state.clock_fn = function()
      local clock_ok, value = pcall(socket.gettime)
      return clock_ok and finite(value) and value or nil
    end
  else
    state.clock_fn = nil
  end
  return state.clock_fn
end

local function monotonic_seconds()
  if rime_api and type(rime_api.get_time_ms) == "function" then
    local ok, value = pcall(rime_api.get_time_ms)
    if ok and finite(value) then return value / 1000 end
  end
  local clock = load_clock()
  if clock then
    local ok, value = pcall(clock)
    if ok and finite(value) then return value end
  end
  -- os.time is only a coarse fallback, but it is wall time rather than CPU
  -- time.  LuaSocket/Rime monotonic clocks are preferred above.
  if os and type(os.time) == "function" then
    local ok, value = pcall(os.time)
    if ok and finite(value) then return value end
  end
  return nil
end

local function deadline_remaining_seconds(deadline, fallback)
  if not deadline then return fallback end
  local now = monotonic_seconds()
  if not now then return fallback end
  return math.max(0, deadline - now)
end

local function deadline_remaining(start_ms, timeout_ms, now_ms)
  start_ms = tonumber(start_ms)
  timeout_ms = tonumber(timeout_ms)
  now_ms = tonumber(now_ms)
  if not finite(start_ms) or not finite(timeout_ms) or not finite(now_ms) then
    return 0
  end
  return math.max(0, start_ms + timeout_ms - now_ms)
end

local function socket_client(module)
  if type(module) == "function" then
    local ok, client = pcall(module)
    if ok then return client end
  elseif type(module) == "table" and type(module.new) == "function" then
    local ok, client = pcall(module.new)
    if ok then return client end
  elseif type(module) == "table" and type(module.stream) == "function" then
    local ok, client = pcall(module.stream)
    if ok then return client end
  end
  return nil
end

close_socket = function(path, client)
  if state.socket_clients[path] == client then state.socket_clients[path] = nil end
  if client and type(client.close) == "function" then pcall(client.close, client) end
end

close_all_sockets = function()
  for path, client in pairs(state.socket_clients) do
    close_socket(path, client)
  end
  state.socket_clients = {}
end

local function set_socket_timeout(client, seconds)
  if type(client.settimeout) ~= "function" then return false end
  local ok, result, detail = pcall(client.settimeout, client, seconds)
  -- LuaSocket returns a truthy status; lightweight test/embedded transports
  -- may return no value.  Treat an explicit error result as unavailable.
  return ok and result ~= false and not (result == nil and detail ~= nil)
end

local function connect_socket(path, timeout, deadline)
  local module = load_socket_module()
  if not module then return nil end
  local client = socket_client(module)
  if not client then return nil end
  if not set_socket_timeout(client, deadline_remaining_seconds(deadline, timeout)) then
    close_socket(path, client)
    return nil
  end
  local ok, connected = pcall(client.connect, client, path)
  if not ok or connected == false then
    close_socket(path, client)
    return nil
  end
  state.socket_clients[path] = client
  return client
end

local function receive_line_bounded(client, timeout, deadline)
  local parts = {}
  local total = 0
  while total < MAX_SOCKET_RESPONSE_BYTES do
    local remaining = deadline_remaining_seconds(deadline, timeout)
    if remaining <= 0 then return nil end
    if not set_socket_timeout(client, remaining) then return nil end
    -- LuaSocket's ``receive('*l')`` allocates until a newline arrives.  Read
    -- one byte at a time so an untrusted same-user endpoint cannot force an
    -- unbounded allocation before the frame-size check.
    local ok, chunk = pcall(client.receive, client, 1)
    if not ok or type(chunk) ~= "string" or #chunk == 0 then return nil end
    if chunk == "\n" then return table.concat(parts) end
    total = total + #chunk
    if total >= MAX_SOCKET_RESPONSE_BYTES then return nil end
    parts[#parts + 1] = chunk
  end
  return nil
end

local function socket_request(path, payload, timeout_ms)
  if type(path) ~= "string" or path == "" then return nil end
  local module = load_socket_module()
  if not module then return nil end
  local timeout = tonumber(timeout_ms) and tonumber(timeout_ms) / 1000 or 0
  if not finite(timeout) or timeout <= 0 then return nil end
  local started = monotonic_seconds()
  local deadline = started and started + timeout or nil
  local wire = json_encode(payload)
  if not wire then return nil end
  local message = wire .. "\n"
  -- A long-lived scorer may reap an idle connection between compositions.  A
  -- single bounded retry repairs that state without adding another deadline:
  -- every connect/send/read operation below uses the original monotonic
  -- deadline, and a failed attempt is closed before the replacement connects.
  for attempt = 1, 2 do
    local client = state.socket_clients[path] or
      connect_socket(path, timeout, deadline)
    local response
    if client then
      local failed = false
      local remaining = deadline_remaining_seconds(deadline, timeout)
      if remaining <= 0 or not set_socket_timeout(client, remaining) then
        failed = true
      end
      local offset = 1
      while not failed and offset <= #message do
        remaining = deadline_remaining_seconds(deadline, timeout)
        if remaining <= 0 or not set_socket_timeout(client, remaining) then
          failed = true
          break
        end
        -- LuaSocket returns the index of the last byte written (and may return
        -- a partial index on a short write).  A few test/embedded transports
        -- return true instead, which is also treated as a complete write.
        local send_ok, last, _, partial = pcall(
          client.send, client, message, offset)
        if not send_ok then
          failed = true
        elseif last == true then
          offset = #message + 1
        elseif type(last) == "number" or type(partial) == "number" then
          if type(last) ~= "number" then last = tonumber(partial) end
          if not last or last < offset then
            failed = true
          else
            offset = last + 1
          end
        else
          failed = true
        end
      end
      if not failed then
        remaining = deadline_remaining_seconds(deadline, timeout)
        if remaining <= 0 or not set_socket_timeout(client, remaining) then
          failed = true
        end
      end
      if not failed then
        local receive_ok, line = pcall(receive_line_bounded, client, timeout, deadline)
        if receive_ok and type(line) == "string" then
          response = json_decode(line)
          if response then return response end
        end
      end
      -- A stale, malformed, or half-closed connection must never remain in
      -- the persistent map.  The next loop iteration obtains a fresh socket.
      close_socket(path, client)
    end
    if attempt == 2 or deadline_remaining_seconds(deadline, timeout) <= 0 then
      return nil
    end
  end
  return nil
end

local function transport_score(current, payload, texts, timeout_ms)
  timeout_ms = tonumber(timeout_ms) or current.config.timeout_ms
  if state.transport then
    local ok, response = pcall(state.transport, payload, timeout_ms,
      current.config)
    if ok then
      if type(response) == "string" then response = json_decode(response) end
      return response_scores(response, payload.request_id, #texts,
        current.profile.model_sha256, state.transport_strict, true,
        payload.normalize)
    end
    return nil
  end
  local socket_available = current.config.socket ~= "" and
    load_socket_module() ~= nil
  if socket_available then
    local response = socket_request(current.config.socket, payload,
      timeout_ms)
    local scores = response_scores(response, payload.request_id, #texts,
      current.profile.model_sha256, true, true, payload.normalize)
    if scores then return scores end
    -- A syntactically valid but stale/mismatched frame is just as unsafe as a
    -- broken socket.  Drop the persistent client so the next composition
    -- cannot consume another response from the wrong protocol state.
    local stale_client = state.socket_clients[current.config.socket]
    if stale_client then close_socket(current.config.socket, stale_client) end
  end
  if current.config.http ~= "" then
    -- HTTP is intentionally test-injectable only.  The production transport
    -- is the authenticated, local JSONL Unix socket; external process or
    -- generated-text requests from the Rime thread break the latency bound.
    log_once("http_disabled", "HTTP rerank endpoint is disabled; use tiger/rerank_socket")
  end
  if state.http_runner then
    local ok, response = pcall(state.http_runner, current.config.http, payload,
      timeout_ms, current.config)
    if ok then
      if type(response) == "string" then response = json_decode(response) end
      return response_scores(response, payload.request_id, #texts,
        current.profile.model_sha256, true, true, payload.normalize)
    end
  end
  return nil
end

-- -------------------------------------------------------------------------
-- Public reranking operation

function M.rerank(items, raw, context, env, context_text)
  if type(items) ~= "table" or type(raw) ~= "string" then return nil end
  if not M.neural_enabled(context) then return nil end
  local current = env_state(env)
  local profile = current and current.profile
  if not profile then return nil end
  if profile.model_available == false or
    (profile.catalog_status and profile.catalog_status ~= "available") then
    return nil
  end
  local raw_count = utf8_length(raw)
  if not raw_count or raw_count < profile.min_raw_len then return nil end
  local count = choose_shortlist_k(items, profile)
  if not count then return nil end
  if count < 2 then return nil end
  for index = 1, count do
    if not finite(tonumber(items[index] and items[index].confidence)) then
      return nil
    end
  end
  local first_confidence = tonumber(items[1].confidence)
  local second_confidence = tonumber(items[2].confidence)
  if not finite(first_confidence) or not finite(second_confidence) then
    return nil
  end
  -- ``max_conf_gap`` is retained as a diagnostic/profile field, but a large
  -- native confidence gap must not suppress neural scoring: that is precisely
  -- where a mis-segmented native leader may need correction.  Adaptive K and
  -- the request deadline remain the budget controls.
  local texts = {}
  local seen = {}
  for index = 1, count do
    local text = items[index] and items[index].text
    if type(text) ~= "string" or text == "" or not utf8_length(text) or seen[text] then
      return nil
    end
    seen[text] = true
    texts[index] = text
  end

  -- When no committed Chinese context exists, condition all candidates on
  -- their common UTF-8 prefix.  This removes repeated prefix likelihood from
  -- the comparison and lets the model judge the actual point of divergence.
  -- A committed prefix remains authoritative; unrelated candidates are still
  -- scored independently by the scorer's complete-candidate fallback.
  local scoring_context = type(context_text) == "string" and context_text or ""
  if scoring_context == "" then
    local common = common_candidate_prefix(items, count)
    if common ~= "" and utf8_length(common) and utf8_length(common) > 0 then
      scoring_context = common
    end
  end

  local key = cache_key(raw, texts, profile, current.config.model, scoring_context,
    count)
  local scores = cache_get(key)
  if not scores then
    local payload = {
      op = "score",
      version = 1,
      request_id = next_request_id(),
      -- Candidates are complete decoded sentences.  The optional context is
      -- the already committed Chinese prefix, never the pinyin code.
      context = scoring_context,
      candidate_mode = "complete",
      candidates = texts,
      normalize = profile.normalization == "mean_token_logp" and
        "mean_logp" or "sum_logp",
    }
    -- The scorer uses the five-row fast kernel only for <=5 candidates; any
    -- larger request is padded to the full twenty-row shape regardless of how
    -- a custom profile names its base budget.
    local timeout_ms = count > FAST_BATCH_ROWS and current.config.full_timeout_ms or
      current.config.timeout_ms
    scores = transport_score(current, payload, texts, timeout_ms)
    if not scores then return nil end
    cache_put(key, scores)
  end
  local normalized = normalize_scores(scores, profile.normalization)
  if not normalized then return nil end
  return blend_and_stable_sort(items, normalized, profile.alpha,
    profile.fusion_normalization)
end

M._test = {
  validate_profile = validate_profile,
  normalize_scores = normalize_scores,
  rank_z_normalize = rank_z_normalize,
  rank_normalize_scores = rank_normalize,
  common_candidate_prefix = common_candidate_prefix,
  choose_shortlist_k = choose_shortlist_k,
  blend_and_stable_sort = blend_and_stable_sort,
  utf8_length = utf8_length,
  deadline_remaining = deadline_remaining,
  json_encode = json_encode,
  json_decode = json_decode,
  cache_key = cache_key,
  cache_put = cache_put,
  clear_cache = M.clear_cache,
  reload_profile = M.reload_profile,
  set_transport = function(fn, strict)
    state.transport = fn
    state.transport_strict = strict == true
  end,
  cache_size = function() return #state.cache_order end,
  load_profile = function(force) return load_profile(force) end,
  state = state,
  set_http_request = function(fn) state.http_runner = fn end,
  receive_line_bounded = receive_line_bounded,
}

return M
