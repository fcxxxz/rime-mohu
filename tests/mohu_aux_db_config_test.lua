-- per-schema aux_db（辅码表路径）与原生候选类型谓词的配置测试。
-- 覆盖 mohu.lua 的 load_zrmdb(path) 缓存/回退、is_native_candidate_type，
-- 以及 hint_filter/aux_translator 对 mohu/aux_db 的读取接线。

local function read(path)
    local f = assert(io.open(path, "r")); local s = f:read("*a"); f:close(); return s
end

-- 桩：rime_api 用户目录指向临时目录，mohu.lua 从用户目录解析模块。
local tmp = os.tmpname()
os.remove(tmp)
local userdir = tmp .. "-auxdb-test"
os.execute("mkdir -p " .. userdir .. "/lua")

local function write(rel, content)
    local f = assert(io.open(userdir .. "/" .. rel, "w"))
    f:write(content); f:close()
end

write("lua/zrmdb.txt", "的\tun ui\n")
write("lua/zrmdb_testscheme.txt", "的\tdr\n")

-- mohu.lua 通过全局 rime_api.get_user_data_dir 定位文件。
_G.rime_api = {
    get_user_data_dir = function() return userdir end,
}
local log = { error = function() end, info = function() end }
_G.log = log

package.path = "lua/?.lua;" .. package.path
local mohu = require("mohu")

-- 1) 默认路径：共享 zrmdb.txt。
local shared = mohu.load_zrmdb()
assert(shared, "shared zrmdb must load")
assert(shared[utf8.codepoint("的")] == " un ui", "shared table content")

-- 2) 指定路径：方案专属表。
local own = mohu.load_zrmdb("lua/zrmdb_testscheme.txt")
assert(own, "per-scheme zrmdb must load")
assert(own[utf8.codepoint("的")] == " dr", "per-scheme table content")

-- 3) 缓存：同路径两次返回同表；不同路径互不影响。
assert(mohu.load_zrmdb("lua/zrmdb_testscheme.txt") == own, "per-path cache")
assert(mohu.load_zrmdb() == shared, "default still cached separately")

-- 4) 原生候选类型谓词。
assert(mohu.is_native_candidate_type("mohu_zrm"))
assert(mohu.is_native_candidate_type("mohu_flypy"))
assert(mohu.is_native_candidate_type("mohu_daniu"))
assert(not mohu.is_native_candidate_type("mohu_table_full_word"))
assert(not mohu.is_native_candidate_type("mohu_semantic_meta"))
assert(not mohu.is_native_candidate_type("pinned"))
assert(not mohu.is_native_candidate_type(nil))
assert(not mohu.is_native_candidate_type(42))

-- 5) 消费方接线：hint_filter 读取 mohu/aux_db 配置。
local hint = require("mohu_hint_filter")
local env = {
    name_space = "",
    engine = {
        schema = {
            config = {
                get_string = function(self, key)
                    if key == "mohu/aux_db" then return "lua/zrmdb_testscheme.txt" end
                    return nil
                end,
                get_bool = function() return nil end,
            },
        },
        context = { get_option = function() return false end },
    },
}
hint.init(env)
assert(env.aux_db_path == "lua/zrmdb_testscheme.txt", "hint_filter reads aux_db")

os.execute("rm -rf " .. userdir)
print("aux_db per-schema config tests passed")
