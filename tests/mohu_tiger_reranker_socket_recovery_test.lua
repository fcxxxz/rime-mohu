package.path = "./tiger_sentence_native/?.lua;" .. package.path

local hash = string.rep("a", 64)
package.preload["mohu_tiger_reranker_profile"] = function()
  return {
    schema = 1,
    model_id = "socket-recovery-test",
    model_path = "/tmp/socket-recovery-model",
    model_sha256 = hash,
    normalization = "sum_token_logp",
    alpha = 1,
    top_k = 5,
    min_raw_len = 1,
    max_conf_gap = math.huge,
  }
end

rime_api = { get_user_data_dir = function() return "/tmp/socket-recovery" end }
local reranker = require("mohu_tiger_reranker")

local context = { options = { mohu_llm_model_rerank = true } }
function context:get_option(name) return self.options[name] or false end
local values = {
  ["tiger/rerank_socket"] = "/tmp/socket-recovery.sock",
  ["tiger/rerank_timeout_ms"] = 100,
}
local config = {
  get_string = function(_, key) return values[key] end,
  get_int = function(_, key) return tonumber(values[key]) end,
  get_double = function(_, key) return tonumber(values[key]) end,
}
local env = { engine = { context = context, schema = { config = config } } }

local connection_count = 0
local idle_reap = false
local function response_for(request, valid)
  local request_id = request:match('"request_id":"([^"]+)"') or ""
  if not valid then request_id = "wrong-request" end
  return string.format(
    '{"ok":true,"version":1,"status":"ok","request_id":"%s",' ..
      '"normalize":"sum_logp","model":{"sha256":"%s"},' ..
      '"scores":[{"sum_logp":-1,"predicted_tokens":1},' ..
      '{"sum_logp":-2,"predicted_tokens":1},' ..
      '{"sum_logp":-3,"predicted_tokens":1},' ..
      '{"sum_logp":-4,"predicted_tokens":1},' ..
      '{"sum_logp":-5,"predicted_tokens":1}]}\n',
    request_id, hash)
end

reranker._test.state.socket_module = function()
  connection_count = connection_count + 1
  local client = { request = "", response = nil, position = 1, closed = false }
  function client:settimeout(_) return 1 end
  function client:connect(_) return 1 end
  function client:send(message, offset)
    if idle_reap and connection_count == 2 then
      return nil, "closed"
    end
    offset = offset or 1
    self.request = self.request .. message:sub(offset)
    self.response = response_for(self.request, connection_count > 1)
    return #message
  end
  function client:receive(size)
    assert(size == 1)
    if self.position > #self.response then return nil, "closed" end
    local value = self.response:sub(self.position, self.position)
    self.position = self.position + 1
    return value
  end
  function client:close() self.closed = true end
  return client
end
reranker._test.set_transport(nil)
reranker._test.clear_cache()
reranker.init(env)

local items = {}
for index = 1, 5 do
  items[index] = { text = "候选" .. index, score = 10 - index, confidence = 10 - index }
end
assert(reranker.rerank(items, "abcdefgh", context, env) == nil,
  "a mismatched response must fail open")
assert(reranker._test.state.socket_clients[values["tiger/rerank_socket"]] == nil,
  "an invalid response must close the persistent socket")

reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefgh", context, env) ~= nil,
  "the next request must reconnect and accept a valid response")
assert(connection_count == 2, "socket recovery must create a fresh connection")

-- The scorer may reap an otherwise healthy persistent connection while Rime
-- is idle.  The first request after that reap must reconnect within its
-- existing deadline instead of silently losing the neural pass.
idle_reap = true
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefgh-idle", context, env) ~= nil,
  "an idle-reaped socket must reconnect during the same request")
assert(connection_count == 3, "idle recovery must create one replacement connection")

reranker.fini(env)
print("Mohu tiger reranker socket recovery tests passed")
