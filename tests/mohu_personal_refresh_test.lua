package.path = './tiger_sentence_native/?.lua;./lua/?.lua;' .. package.path
rime_api = { get_user_data_dir = function() return '/tmp/mohu-personal-refresh-test' end }
log = { error = function(message) error(message) end }
local function notifier()
  return { callbacks = {}, connect = function(self, callback)
    self.callbacks[#self.callbacks+1] = callback
    return { disconnect = function() end }
  end }
end
local ctx = { input = '', commit_notifier = notifier(), update_notifier = notifier() }
function ctx:get_commit_text() return '教授' end
local rows = {
  { text = '教授', custom_code = 'qy;oa gf;pg', commit_count = 2 },
  { text = '教授', custom_code = 'qy;oa gf;pi', commit_count = 3 },
}
local memory = { user_lookup = function() return true end, disconnect = function() end }
function memory:iter_user()
  local i = 0
  return function() i=i+1; return rows[i] end
end
Memory = function() return memory end
local applied, appended, commits, learned = {}, {}, 0, {}
package.loadlib = function()
  return function() return {
    create = function() return 7 end,
    free = function() end,
    adjust_personal = function(_, code, text, count)
      learned[#learned+1]={code,text,count};return 1
    end,
    decode = function() return "0 0 0 0 0 0\n", 0 end,
    set_personal_lexicon = function(_, payload) applied[#applied+1] = payload end,
    personal_begin = function() appended={};return true end,
    personal_append = function(_, payload) appended[#appended+1]=payload;return true end,
    personal_commit = function() commits=commits+1;return true end,
    personal_abort = function() end,
  } end
end
local env = { engine = { context = ctx, schema = { config = {
  get_string = function(_,key)
    if key == 'tiger/personal_refresh_interval' then return '0' end
    if key == 'tiger/user_model' then return 'false' end
  end,
  get_int = function() return nil end,
} } } }
local native = dofile('tiger_sentence_native/mohu_tiger_sentence.lua')
native.translator.init(env)
assert(applied[1] == 'qygf\t教授\t5\n', 'startup snapshot merges spelling variants')
rows[2].commit_count = 4
for _,f in ipairs(ctx.commit_notifier.callbacks) do f(ctx) end
for _=1,10 do for _,f in ipairs(ctx.update_notifier.callbacks) do f(ctx) end end
assert(commits == 1, 'idle scan must finish native transaction')
assert(table.concat(appended) == 'qygf\t教授\t6\n', 'transaction consumes finalized merged snapshot')
assert(env._mohu_personal_feed == nil and not env._mohu_personal_dirty,
  'completed refresh clears transaction and dirty state')
local pinned_phrase={type="pinned",text="教授",preedit="qy gf",
 get_dynamic_type=function() return "Phrase" end}
function pinned_phrase:get_genuine() return self end
ctx.composition={toSegmentation=function() return {
 get_segments=function() return {{get_selected_candidate=function() return pinned_phrase end}} end,
} end}
for _,f in ipairs(ctx.commit_notifier.callbacks) do f(ctx) end
assert(#learned==1 and learned[1][1]=="qygf" and learned[1][2]=="教授",
 "pinned lexical Phrase retains immediate native word learning")
native.translator.fini(env)
print('personal refresh transaction: ok')
