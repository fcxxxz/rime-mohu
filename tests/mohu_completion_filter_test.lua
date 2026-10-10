package.path='./lua/?.lua;'..package.path
local filter=require('mohu_completion_filter')
local function candidate(text,kind,comment,genuine)
 local c={text=text,type=kind or 'phrase',comment=comment or ''}
 function c:get_genuine() return genuine or self end
 return c
end
local function run(list,limit)
 local env={engine={schema={config={
  get_int=function(_,key) if key=='mohu/word_completion_limit' then return limit end end,
  get_string=function(_,key) if key=='mohu/pin/indicator' then return '📌' end end,
 }}}}
 filter.init(env)
 local out={};yield=function(c) out[#out+1]=c end
 filter.func({iter=function() local i=0;return function() i=i+1;return list[i] end end},env)
 return out
end
local list={candidate('国际贸易','mohu_zrm'),candidate('国际贸易结算','completion'),
 candidate('国际贸易体系','completion'),candidate('国际贸易有限公司','completion'),
 candidate('国际贸易法','completion'),candidate('国际','phrase'),candidate('国籍','phrase')}
local out=run(list)
assert(#out==6 and out[1]==list[1] and out[4]==list[4] and out[5]==list[6],
 'default keeps three long completions while preserving typed first candidate and partial words')
assert(#run(list,0)==3,'zero hides long completions only')
assert(#run(list,-1)==#list,'minus one restores unbounded completion')
assert(#run(list,1)==4,'configured limit is applied')
assert(#run({list[2],list[2],list[3],list[4],list[5],list[6]})==5,
 'duplicate text must not consume extra completion slots before uniquifier')
local pinned=candidate('国际贸易组织','pinned','',candidate('国际贸易组织','completion'))
assert(run({list[2],list[3],list[4],pinned,list[5]},1)[2]==pinned,
 'a pin survives even with a genuine completion and a hidden icon')
local managed=candidate('国际贸易组织','mohu_reordered','',candidate('国际贸易组织','completion'))
assert(run({list[2],list[3],managed,list[4]},1)[2]==managed,'saved explicit order survives the cap')
assert(#run({candidate('机器难轻失','table'),candidate('机难轻失','mohu_secondary_word'),
 candidate('普通长整句','sentence'),candidate('完整长词语','phrase'),candidate('短词','completion')},0)==5,
 'fixed abbreviations, exact phrases, native sentences and short completions are independent')
print('long word completion limit: ok')
