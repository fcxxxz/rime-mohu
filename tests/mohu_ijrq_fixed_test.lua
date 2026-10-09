package.path = './lua/?.lua;' .. package.path
local actual_mohu = require('mohu')
actual_mohu.is_reverse_lookup = function(env) return env.engine.context.reverse == true end
local filter = require('mohu_ijrq_filter')
local codes = { ['咦'] = 'yid yidm', ['邑'] = 'yidm', ['甲'] = 'j', ['暮'] = 'mulo' }
ReverseLookup = function() return { lookup = function(_,text) return codes[text] or '' end } end
rime_api = { get_time_ms = function() return 1000 end }
log = { error = function(message) error(message) end }
local function cand(text, finish, kind)
 local c={text=text,type=kind or 'table',comment='',preedit='yidm',start=0,_end=finish or 4}
 function c:get_genuine() return self end
 return c
end
local function run(raw, list, conf, opts)
 conf=conf or {};opts=opts or {}
 local ctx={input=raw,reverse=opts.reverse}
 function ctx:get_option(name) return opts[name] or false end
 local env={engine={context=ctx,schema={config={
  get_bool=function(_,key)
   if conf[key]~=nil then return conf[key] end
   if key=='mohu/ijrq/enable' then return true end
   if key=='mohu/ijrq/show_hint' then return true end
   return false
  end,
  get_int=function(_,key) if key=='menu/page_size' then return 5 end return conf[key] end,
  get_string=function(_,key) if key=='translator/dictionary' then return 'mohu_zrm' end
   if key=='mohu/ijrq/suffix' then return 'o' end
   if key=='mohu/pin/indicator' then return '📌' end
   return nil
  end,
 }}}}
 filter.init(env)
 local emitted={};yield=function(c) emitted[#emitted+1]=c end
 local t={iter=function() local i=0;return function() i=i+1;return list[i] end end}
 filter.func(t,env)
 if not opts.keep_env then filter.fini(env) end
 return emitted,env
end
local rows={cand('咦'),cand('一点'),cand('邑'),cand('已点'),cand('疑点'),cand('伊甸'),cand('尾词')}
local out=run('yidm',rows)
assert(out[1].text=='一点','a fixed full-code 咦 must not bypass 出简让全')
assert(out[6].text=='咦' and out[6].comment=='yid','deferred character keeps short-code hint')
assert(#out==#rows,'defer must retain every candidate')
assert(run('yid',{cand('咦',3),cand('邑',3)})[1].text=='咦','short input retains source order')
assert(run('yidm',rows,{['mohu/ijrq/enable']=false})[1].text=='咦','disabled IJRQ retains fixed order')
local pin=cand('咦',4,'pinned')
assert(run('yidm',{pin,cand('一点')})[1]==pin,'explicit pin stays first')
local partial=cand('咦',2)
assert(run('yidm',{partial,cand('一点')})[1]==partial,'partial selection is not full-code IJRQ')
assert(run('yidm',{cand('咦')})[1].text=='咦','only deferred result remains available')
local suffix=run('yidmo',{cand('咦',5),cand('一点',5)})
assert(suffix[1].text=='一点' and suffix[2].text=='咦','configured full-code suffix is handled')
local words={cand('噗嗤',5),cand('噗哧',5),cand('扑哧',5)}
local same=run('puiid',words)
for i,c in ipairs(words) do assert(same[i]==c,'word auxiliary order stays independent') end
assert(run('yidm',rows,nil,{reverse=true})[1].text=='咦','reverse lookup remains unchanged')
print('fixed-character IJRQ: ok')
-- Deduplication runs later. Five distinct alternatives must precede 咦.
local repeated=run('yidm',{cand('咦'),cand('一点'),cand('一点'),cand('已点'),cand('疑点'),cand('伊甸'),cand('邑')})
local distinct,seen={},{}
for _,c in ipairs(repeated) do if not seen[c.text] then seen[c.text]=true;distinct[#distinct+1]=c.text end end
assert(distinct[6]=='咦','duplicate alternatives cannot put the full-code character back on page one')

-- Reorder replaces a pin with its smart phrase for learning. Keep pin identity
-- even when the user hides its display indicator.
local reorder=require('mohu_reorder_filter')
local saved_shadow=ShadowCandidate
ShadowCandidate=function(original,kind,text,comment)
 local shadow={type=kind,text=text,comment=comment,preedit=original.preedit,start=original.start,_end=original._end}
 function shadow:get_genuine() return original:get_genuine() end
 return shadow
end
local captured={};yield=function(c) captured[#captured+1]=c end
local learning_phrase=cand('咦',4,'phrase')
learning_phrase.get_dynamic_type=function() return 'Phrase' end
reorder.yield_smart_in_place_of_fixed({pin_indicator=''},learning_phrase,cand('咦',4,'pinned'))
assert(captured[1]==learning_phrase and captured[1]:get_dynamic_type()=='Phrase',
  'pin replacement must keep the same learning Phrase without another Shadow')
local invisible_pin=run('yidm',{captured[1],cand('一点'),cand('已点'),cand('疑点'),cand('伊甸'),cand('邑')},
 {['mohu/pin/indicator']=''})
assert(invisible_pin[1].text=='咦' and invisible_pin[1].type=='pinned','pin identity does not depend on an icon')
ShadowCandidate=saved_shadow
print('duplicate and invisible-pin IJRQ: ok')

-- Word-level IJRQ must also retain explicit pins when that independent
-- feature is enabled.
local _,word_env=run('lmjx',{cand('链接',4,'pinned'),cand('连接',4,'phrase')},
 {['mohu/ijrq/enable_word']=true}, {keep_env=true})
word_env.engine.context.input='lmjxf'
local word_items={cand('链接',5,'pinned'),cand('连接',5,'phrase')}
local word_yielded={};yield=function(c) word_yielded[#word_yielded+1]=c end
filter.func({iter=function() local i=0;return function() i=i+1;return word_items[i] end end},word_env)
assert(word_yielded[1]==word_items[1],'word-level IJRQ must preserve a pinned first candidate')
filter.fini(word_env)
print('word-level pinned IJRQ: ok')
