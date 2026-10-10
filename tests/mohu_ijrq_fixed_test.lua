package.path = './lua/?.lua;' .. package.path
local actual_mohu = require('mohu')
actual_mohu.is_reverse_lookup = function(env) return env.engine.context.reverse == true end
local filter = require('mohu_ijrq_filter')
local codes = { ['咦'] = 'yid yidm', ['邑'] = 'yidm', ['甲'] = 'j', ['暮'] = 'mulo', ['进'] = 'jn jnqu' }
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

local function secondary(text, finish)
 local original=cand(text,finish)
 local c=cand(text,finish,'mohu_secondary_word')
 function c:get_genuine() return original end
 return c
end
local function texts(items)
 local result={}
 for _,c in ipairs(items) do result[#result+1]=c.text end
 return table.concat(result,'|')
end
local jnqu={cand('进'),secondary('机难轻失'),cand('进去'),cand('禁区'),cand('进取'),cand('进区'),cand('金曲')}
assert(texts(run('jnqu',jnqu))=='进去|机难轻失|禁区|进取|进区|进|金曲',
 'a secondary abbreviation must follow the final first candidate, not inherit a deferred character slot')
assert(texts(run('jnqu',{cand('进'),secondary('机难轻失'),secondary('另一简词'),cand('进去'),cand('禁区'),cand('进取'),cand('进区')}))
 =='进去|机难轻失|另一简词|禁区|进取|进|进区','multiple abbreviations retain order and count toward the defer slot')
assert(texts(run('jnqu',{secondary('机难轻失'),cand('进去'),cand('禁区')}))=='机难轻失|进去|禁区',
 'without a deferred character, existing injection priority stays unchanged')
assert(texts(run('jnqu',{cand('进'),secondary('机难轻失'),cand('进去'),cand('禁区'),cand('进取'),cand('进区'),cand('金曲')},{['mohu/ijrq/enable']=false}))=='进|机难轻失|进去|禁区|进取|进区|金曲',
 'disabled IJRQ keeps the original order')
assert(texts(run('jnqu',{cand('进'),secondary('机难轻失'),cand('进去'),cand('禁区'),cand('进取'),cand('进区'),cand('金曲')},nil,{inflexible=true}))=='机难轻失|进去|禁区|进取|进区|进|金曲',
 'fixed-word mode retains its explicit fixed-word priority')
assert(texts(run('jnqu',{cand('进'),cand('正常长词'),cand('进去')}))=='正常长词|进去|进',
 'a non-injected long candidate must not be treated as a secondary abbreviation')
assert(texts(run('jnqu',{cand('暮'),secondary('机难轻失'),cand('进去')}))=='暮|机难轻失|进去',
 'a non-yielding full-code character keeps its first slot')
local leading_pin=cand('机难轻失',4,'pinned')
assert(run('jnqu',{leading_pin,cand('进'),cand('进去')})[1]==leading_pin,'explicit abbreviation pin stays first')
local wrapped_pin=secondary('机难轻失');wrapped_pin.type='pinned'
assert(run('jnqu',{wrapped_pin,cand('进'),cand('进去')})[1]==wrapped_pin,'fixed provenance must not override a pin')
local partial_secondary=secondary('机难轻失',2)
assert(run('jnqu',{partial_secondary,cand('进去')})[1].text=='机难轻失','partial selection is not an injected full-span abbreviation')
assert(texts(run('jnquo',{secondary('机难轻失',5),cand('进去',5)}))=='机难轻失|进去',
 'full-code suffix input must not apply four-code word injection rules')
assert(texts(run('jnqu',{cand('进'),secondary('机难轻失')}))=='机难轻失|进',
 'no ordinary alternative must not discard either the abbreviation or the character')
local duplicates=run('jnqu',{cand('进'),secondary('机难轻失'),cand('进去'),cand('进去'),cand('禁区'),cand('进取'),cand('进区')})
local unique,seen_words={},{}
for _,c in ipairs(duplicates) do if not seen_words[c.text] then seen_words[c.text]=true;unique[#unique+1]=c end end
assert(texts(unique)=='进去|机难轻失|禁区|进取|进区|进','duplicate streams cannot steal the final first slot or defer position')
assert(run('jnqu',{cand('进'),secondary('机难轻失'),cand('进去')})[2].type=='table',
 'internal injection marker must not leak to final candidates')
assert(run('jnqu',{secondary('机难轻失')},{['mohu/ijrq/enable']=false})[1].type=='table',
 'disabled IJRQ must also remove the internal marker')
assert(run('jnqu',{secondary('机难轻失')},nil,{reverse=true})[1].type=='table',
 'reverse lookup must also remove the internal marker')
local single_slot=run('jnqu',{cand('进'),secondary('机难轻失'),cand('进去'),cand('禁区')},
 {['mohu/ijrq/defer']=1})
assert(texts(single_slot)=='进去|机难轻失|进|禁区','secondary block follows first even with a one-candidate defer')
assert(texts(run('jnqu',{cand('进'),secondary('简词一'),secondary('简词二'),secondary('简词三'),
 secondary('简词四'),secondary('简词五'),secondary('简词六'),cand('进去'),cand('禁区')}))
 =='进去|简词一|简词二|简词三|简词四|简词五|简词六|进|禁区',
 'a long secondary block stays intact and can defer the character beyond the minimum')
print('secondary abbreviation IJRQ: ok')

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

-- Real ShadowCandidate comments are independent of genuine comments. The
-- reordered display annotation must survive unwrapping before hint filters.
local display_original=cand('机难轻失');display_original.comment='`F'
local display_shadow=cand('机难轻失',4,'mohu_secondary_word');display_shadow.comment='⚡️'
function display_shadow:get_genuine() return display_original end
local display_output=run('jnqu',{cand('进'),display_shadow,cand('进去')})
assert(display_output[2]==display_original and display_output[2].comment=='⚡️',
 'secondary cleanup must remove the wrapper and retain the converted display comment')
assert(display_original.type=='table','cleanup must preserve the original lexical type')
local stale=secondary('机难轻失');stale.comment='`F';stale:get_genuine().comment='⚡️'
assert(run('jnqu',{cand('进'),stale,cand('进去')})[2].comment=='⚡️',
 'an immutable stale Shadow comment must not overwrite the converted quick-code indicator')
print('secondary display comment: ok')
