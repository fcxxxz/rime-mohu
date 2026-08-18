package.path = "./lua/?.lua;" .. package.path

local executed

rime_api = {
  get_user_data_dir = function()
    return "C:\\用户\\Rime配置"
  end,
}

local editor = require("rime_skin_editor")

local function assert_contains(text, expected, label)
  if not tostring(text):find(expected, 1, true) then
    error(label .. ": expected to find " .. expected .. " in " .. tostring(text), 2)
  end
end

local function assert_not_contains(text, expected, label)
  if tostring(text):find(expected, 1, true) then
    error(label .. ": did not expect to find " .. expected .. " in " .. tostring(text), 2)
  end
end

local function assert_ascii(text, label)
  local value = tostring(text)
  for index = 1, #value do
    if value:byte(index) > 127 then
      error(label .. ": expected ASCII-only command, got " .. value, 2)
    end
  end
end

local function test_windows_launcher_command_does_not_expose_unicode_path_to_cmd()
  executed = nil
  editor._test_reset({
    platform = function()
      return "windows"
    end,
    execute = function(command)
      executed = command
      return true
    end,
    now_ms = function()
      return 10000
    end,
  })

  editor.open_from_command({ engine = { context = { clear = function() end } } })

  assert_contains(executed, "-EncodedCommand", "windows command should use encoded PowerShell")
  assert_not_contains(executed, "用户", "windows command should not expose Unicode user path to cmd")
  assert_not_contains(executed, "Rime皮肤编辑器", "windows command should not expose Unicode editor path to cmd")
  assert_not_contains(executed, "启动.bat", "windows command should not expose Chinese launcher name to cmd")
  assert_not_contains(executed, ".bat", "windows command should not launch through any bat file")
  assert_ascii(executed, "windows command")
end

local function test_windows_outer_launcher_script_does_not_expose_unicode_paths_to_start_process()
  editor._test_reset({
    platform = function()
      return "windows"
    end,
  })

  local script = editor._test_windows_launcher_script()
  assert_contains(script, "Start-Process", "windows script should spawn a child process")
  assert_contains(script, "powershell.exe", "windows script should start PowerShell directly")
  assert_contains(script, "-EncodedCommand", "windows script should pass child script as encoded command")
  assert_not_contains(script, "用户", "outer script should not expose Unicode user path")
  assert_not_contains(script, "Rime皮肤编辑器", "outer script should not expose Unicode editor path")
  assert_not_contains(script, "local_server.ps1", "outer script should not expose server path")
  assert_ascii(script, "outer windows script")
  assert_not_contains(script, ".bat", "windows script should not depend on bat files")
end

local function test_windows_child_launcher_script_starts_local_server_directly()
  editor._test_reset({
    platform = function()
      return "windows"
    end,
  })

  local script = editor._test_windows_child_launcher_script()
  assert_contains(script, "local_server.ps1", "child script should start local server script directly")
  assert_contains(script, "-Root", "child script should pass Rime user data dir as root")
  assert_not_contains(script, ".bat", "windows script should not depend on bat files")
end

local function test_windows_child_launcher_script_declares_utf8_runtime_encoding()
  editor._test_reset({
    platform = function()
      return "windows"
    end,
  })

  local script = editor._test_windows_child_launcher_script()
  assert_contains(script, "$OutputEncoding", "child script should set PowerShell output encoding")
  assert_contains(script, "[Console]::OutputEncoding", "child script should set console output encoding")
  assert_contains(script, "UTF8Encoding", "child script should use UTF-8 encoding")
  assert_contains(script, "-ArgumentList $false", "child script should use Windows PowerShell compatible New-Object syntax")
end

local tests = {
  test_windows_launcher_command_does_not_expose_unicode_path_to_cmd,
  test_windows_outer_launcher_script_does_not_expose_unicode_paths_to_start_process,
  test_windows_child_launcher_script_starts_local_server_directly,
  test_windows_child_launcher_script_declares_utf8_runtime_encoding,
}

for _, test in ipairs(tests) do
  test()
end

print("rime_skin_editor tests passed")
