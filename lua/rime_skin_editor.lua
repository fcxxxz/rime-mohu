local M = {}

local OPTION_NAME = "rime_skin_editor"
local kNoop = 2
local throttle_ms = 2000
local last_launch_ms = -throttle_ms

local now_ms_fn = nil
local execute_fn = nil
local platform_fn = nil

local function now_ms()
  if now_ms_fn then
    return now_ms_fn()
  end
  if rime_api and rime_api.get_time_ms then
    return rime_api.get_time_ms()
  end
  return os.time() * 1000
end

local function path_separator()
  return package.config:sub(1, 1)
end

local function user_data_dir()
  if rime_api and rime_api.get_user_data_dir then
    return rime_api.get_user_data_dir()
  end
  return "."
end

local function shell_quote(value)
  local text = tostring(value or "")
  if path_separator() == "\\" then
    return '"' .. text:gsub('"', '\\"') .. '"'
  end
  return "'" .. text:gsub("'", "'\\''") .. "'"
end

local base64_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

local function base64_encode(bytes)
  local output = {}
  for index = 1, #bytes, 3 do
    local a = bytes:byte(index) or 0
    local b = bytes:byte(index + 1) or 0
    local c = bytes:byte(index + 2) or 0
    local value = a * 65536 + b * 256 + c
    local remain = #bytes - index + 1
    output[#output + 1] = base64_chars:sub(math.floor(value / 262144) % 64 + 1, math.floor(value / 262144) % 64 + 1)
    output[#output + 1] = base64_chars:sub(math.floor(value / 4096) % 64 + 1, math.floor(value / 4096) % 64 + 1)
    output[#output + 1] = remain >= 2 and base64_chars:sub(math.floor(value / 64) % 64 + 1, math.floor(value / 64) % 64 + 1) or "="
    output[#output + 1] = remain >= 3 and base64_chars:sub(value % 64 + 1, value % 64 + 1) or "="
  end
  return table.concat(output)
end

local function utf8_codepoints(text)
  local points = {}
  local index = 1
  while index <= #text do
    local byte = text:byte(index)
    local codepoint
    local width
    if byte < 0x80 then
      codepoint = byte
      width = 1
    elseif byte < 0xE0 then
      codepoint = (byte % 0x20) * 0x40 + (text:byte(index + 1) % 0x40)
      width = 2
    elseif byte < 0xF0 then
      codepoint = (byte % 0x10) * 0x1000 + (text:byte(index + 1) % 0x40) * 0x40 + (text:byte(index + 2) % 0x40)
      width = 3
    else
      codepoint = (byte % 0x08) * 0x40000 + (text:byte(index + 1) % 0x40) * 0x1000 + (text:byte(index + 2) % 0x40) * 0x40 + (text:byte(index + 3) % 0x40)
      width = 4
    end
    points[#points + 1] = codepoint
    index = index + width
  end
  return points
end

local function append_utf16le_unit(bytes, value)
  bytes[#bytes + 1] = string.char(value % 0x100)
  bytes[#bytes + 1] = string.char(math.floor(value / 0x100) % 0x100)
end

local function utf8_to_utf16le(text)
  local bytes = {}
  for _, codepoint in ipairs(utf8_codepoints(tostring(text or ""))) do
    if codepoint <= 0xFFFF then
      append_utf16le_unit(bytes, codepoint)
    else
      local value = codepoint - 0x10000
      append_utf16le_unit(bytes, 0xD800 + math.floor(value / 0x400))
      append_utf16le_unit(bytes, 0xDC00 + (value % 0x400))
    end
  end
  return table.concat(bytes)
end

local function powershell_single_quote(value)
  return "'" .. tostring(value or ""):gsub("'", "''") .. "'"
end

local function powershell_encoded_command(script)
  return base64_encode(utf8_to_utf16le(script))
end

local function detected_platform()
  if platform_fn then
    return platform_fn()
  end
  if path_separator() == "\\" then
    return "windows"
  end
  local uname = io.popen and io.popen("uname -s 2>/dev/null")
  if uname then
    local value = uname:read("*l") or ""
    uname:close()
    if value == "Darwin" then
      return "mac"
    end
  end
  return "unix"
end

local function launcher_path(platform)
  local sep = platform == "windows" and "\\" or path_separator()
  local suffix = platform == "windows" and "local\\local_server.ps1" or "启动.command"
  return table.concat({ user_data_dir(), "Rime皮肤编辑器", suffix }, sep)
end

local function windows_child_launcher_script()
  local script_path = launcher_path("windows")
  local root = user_data_dir()
  return table.concat({
    "$Utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false;",
    "$OutputEncoding = $Utf8NoBom;",
    "[Console]::OutputEncoding = $Utf8NoBom;",
    "&",
    powershell_single_quote(script_path),
    "-Root",
    powershell_single_quote(root),
  }, " ")
end

local function windows_launcher_script()
  local child_command = powershell_encoded_command(windows_child_launcher_script())
  return table.concat({
    "Start-Process",
    "-FilePath 'powershell.exe'",
    "-ArgumentList @(",
    "'-NoProfile',",
    "'-WindowStyle', 'Normal',",
    "'-ExecutionPolicy', 'Bypass',",
    "'-EncodedCommand', " .. powershell_single_quote(child_command),
    ")",
  }, " ")
end

local function launcher_command(platform)
  local path = launcher_path(platform)
  if platform == "windows" then
    return "powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand " .. powershell_encoded_command(windows_launcher_script())
  end
  if platform == "mac" then
    return "open " .. shell_quote(path)
  end
  return "sh " .. shell_quote(path) .. " >/dev/null 2>&1 &"
end

local function launch()
  local platform = detected_platform()
  local command = launcher_command(platform)
  local execute = execute_fn or os.execute
  return execute(command)
end

local function launch_throttled()
  local current = now_ms()
  if current - last_launch_ms < throttle_ms then
    return false
  end
  last_launch_ms = current
  launch()
  return true
end

local function get_context(env)
  return env and env.engine and env.engine.context or nil
end

local function maybe_launch(env)
  local ctx = get_context(env)
  if not ctx or not ctx.get_option or not ctx:get_option(OPTION_NAME) then
    return
  end
  if ctx.set_option then
    ctx:set_option(OPTION_NAME, false)
  end

  launch_throttled()
end

local processor = {}

function processor.init(env)
  local ctx = get_context(env)
  if ctx and ctx.option_update_notifier and ctx.option_update_notifier.connect then
    env.rime_skin_editor_notifier = ctx.option_update_notifier:connect(function()
      maybe_launch(env)
    end)
  end
  maybe_launch(env)
end

function processor.fini(env)
  if env and env.rime_skin_editor_notifier and env.rime_skin_editor_notifier.disconnect then
    env.rime_skin_editor_notifier:disconnect()
  end
end

function processor.func(_, env)
  maybe_launch(env)
  return kNoop
end

function M._test_reset(opts)
  opts = opts or {}
  now_ms_fn = opts.now_ms
  execute_fn = opts.execute
  platform_fn = opts.platform
  last_launch_ms = -throttle_ms
end

function M._test_windows_launcher_script()
  return windows_launcher_script()
end

function M._test_windows_child_launcher_script()
  return windows_child_launcher_script()
end

function M.open_from_command(env)
  local ctx = get_context(env)
  if ctx and ctx.clear then
    ctx:clear()
  end
  return launch_throttled()
end

M.processor = processor

return M
