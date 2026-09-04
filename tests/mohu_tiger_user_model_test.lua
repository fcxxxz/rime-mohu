package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

-- 用户调频层测试：上屏喂入 native 计数表、权重与快照导入、间隔快照
-- 原子写出、方案卸载兜底快照、旧 ABI dylib 静默降级、配置关闭。

local root = "/tmp/mohu-tiger-user-model-test"
os.execute("rm -rf " .. root)
os.execute("mkdir -p " .. root .. "/mohu/config")

rime_api = { get_user_data_dir = function() return root end }
log = { error = function() end }

local yielded = {}
Candidate = function(kind, start_pos, end_pos, text, comment)
  return { type = kind, start = start_pos, _end = end_pos, text = text, comment = comment }
end
yield = function(candidate) yielded[#yielded + 1] = candidate end

local decode_output = table.concat({
  "0 0 0 0 1 0",
  "原生句\tab cd ef\t0\t0\t1\t3:2,6:4",
  "",
}, "\n")

local function notifier()
  return {
    connections = {},
    connect = function(self, callback)
      local connection = { callback = callback, disconnected = false }
      function connection:disconnect() self.disconnected = true end
      self.connections[#self.connections + 1] = connection
      return connection
    end,
  }
end

local function make_env(config)
  local ctx = {
    input = "",
    properties = {},
    options = {},
    commit_notifier = notifier(),
    update_notifier = notifier(),
    _commit_text = "",
  }
  function ctx:get_commit_text() return self._commit_text end
  local engine = {
    context = ctx,
    schema = {
      config = {
        get_string = function(_, key) return config[key] end,
        get_int = function() return nil end,
      },
    },
  }
  return { engine = engine }, ctx
end

local function fresh(with_user_model)
  local calls = { update = {}, weights = {}, exports = 0, imports = {} }
  package.preload["mohu_tiger_reranker"] = function()
    return { init = function() end, fini = function() end, rerank = function() return nil end }
  end
  package.loadlib = function()
    return function()
      local module = {
        create = function() return 7 end,
        free = function() end,
        decode = function() return decode_output, 0.1 end,
      }
      if with_user_model then
        module.update_user_model = function(handle, text)
          assert(handle == 7, "update must target the live engine handle")
          calls.update[#calls.update + 1] = text
          return 1
        end
        module.set_user_model_weight = function(handle, weight)
          assert(handle == 7)
          calls.weights[#calls.weights + 1] = weight
          return true
        end
        module.user_model_export = function(handle)
          assert(handle == 7)
          calls.exports = calls.exports + 1
          return "SNAPSHOT-BLOB-" .. tostring(calls.exports)
        end
        module.user_model_import = function(handle, blob)
          assert(handle == 7)
          calls.imports[#calls.imports + 1] = blob
          return 1
        end
      end
      return module
    end
  end
  return dofile("tiger_sentence_native/mohu_tiger_sentence.lua"), calls
end

local function fire_commit(ctx, text)
  ctx._commit_text = text
  for _, connection in ipairs(ctx.commit_notifier.connections) do
    if not connection.disconnected then connection.callback(ctx) end
  end
end

local function read_file(path)
  local file = io.open(path, "rb")
  if not file then return nil end
  local content = file:read("*a")
  file:close()
  return content
end

-- 1. 全量 ABI：装载导入旧快照、按权重设置、上屏喂入、间隔快照、fini 兜底。
do
  -- paths.root 指向 <用户目录>/mohu，默认快照落在 mohu/config/ 下。
  local snapshot_path = root .. "/mohu/config/user-ngram.snapshot"
  local seed = io.open(snapshot_path, "wb")
  assert(seed, "test root must be writable")
  seed:write("PREVIOUS-BLOB")
  seed:close()

  local config = { ["tiger/user_model_snapshot_interval"] = "2" }
  local env, ctx = make_env(config)
  local native, calls = fresh(true)
  native.translator.init(env)

  assert(#calls.weights == 1 and calls.weights[1] == 0.85,
    "init must push the default static weight 0.85")
  assert(#calls.imports == 1 and calls.imports[1] == "PREVIOUS-BLOB",
    "init must import the persisted snapshot")

  fire_commit(ctx, "魔虎整句")
  assert(#calls.update == 1 and calls.update[1] == "魔虎整句",
    "a CJK commit must feed the user model")
  fire_commit(ctx, "hello world")
  assert(#calls.update == 1, "pure-ASCII commits must not feed the user model")
  fire_commit(ctx, "")
  assert(#calls.update == 1, "empty commits must not feed the user model")

  fire_commit(ctx, "第二次上屏")
  assert(calls.exports == 1, "the interval (2) must trigger a snapshot export")
  assert(read_file(snapshot_path) == "SNAPSHOT-BLOB-1",
    "the snapshot file must be replaced atomically")

  fire_commit(ctx, "第三次上屏")
  assert(calls.exports == 1, "non-boundary commits must not export")
  native.translator.fini(env)
  assert(calls.exports == 2, "fini must snapshot unsaved user counts")
  assert(read_file(snapshot_path) == "SNAPSHOT-BLOB-2",
    "fini snapshot must land on disk")
  local disconnected = 0
  for _, connection in ipairs(ctx.commit_notifier.connections) do
    if connection.disconnected then disconnected = disconnected + 1 end
  end
  assert(disconnected >= 1, "fini must disconnect the user model notifier")
end

-- 2. 旧 ABI dylib：无用户模型函数时静默降级，翻译不受影响。
do
  local env, ctx = make_env({})
  local native, calls = fresh(false)
  native.translator.init(env)
  assert(env._tiger_user_model_on == false,
    "a dylib without the user model ABI must disable the layer silently")
  assert(#calls.weights == 0 and #calls.imports == 0)
  local segment = { start = 0, _end = 6, has_tag = function(_, tag) return tag == "abc" end }
  ctx.input = "ufqyhfmimh"
  yielded = {}
  native.translator.func("ufqyhfmimh", segment, env)
  assert(#yielded == 1, "translation must keep working on the old ABI")
  native.translator.fini(env)
end

-- 3. 配置关闭：tiger/user_model: false 时不连接通知器、不设权重。
do
  local config = { ["tiger/user_model"] = "false" }
  local env, ctx = make_env(config)
  local native, calls = fresh(true)
  native.translator.init(env)
  assert(env._tiger_user_model_on == false, "user_model=false must disable the layer")
  assert(#calls.weights == 0 and #calls.imports == 0)
  assert(env._tiger_user_model_commit_notifier == nil,
    "the commit notifier must not connect when disabled")
  fire_commit(ctx, "魔虎整句")
  assert(#calls.update == 0)
  native.translator.fini(env)
end

-- 4. 权重与间隔的非法值回退默认；快照路径可覆盖。
do
  local config = {
    ["tiger/user_model_weight"] = "7",
    ["tiger/user_model_snapshot_interval"] = "0",
    ["tiger/user_model_snapshot"] = root .. "/custom.snapshot",
  }
  local env, _ = make_env(config)
  local native, calls = fresh(true)
  native.translator.init(env)
  assert(#calls.weights == 1 and calls.weights[1] == 0.85,
    "an out-of-range weight must fall back to 0.85")
  assert(env._tiger_user_model_interval == 64,
    "an invalid interval must fall back to 64")
  assert(env._tiger_user_model_path == root .. "/custom.snapshot",
    "the snapshot path override must be honored")
  native.translator.fini(env)
end

print("Mohu user model tests passed")
