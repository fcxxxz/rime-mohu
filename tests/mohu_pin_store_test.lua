package.path = "./lua/?.lua;" .. package.path

local db = {
    data = {
        ["aa \tManual"] = "c=168 d=0 t=1 s=pin",
        ["aa \tMade"] = "c=169 d=0 t=1 s=panacea",
        ["bb \tOld"] = "c=200 d=0 t=1",
    },
    is_loaded = false,
}

function db:loaded()
    return self.is_loaded
end
function db:open()
    self.is_loaded = true
end
function db:close()
    self.is_loaded = false
end
function db:query(prefix)
    local entries = {}
    for key, value in pairs(self.data) do
        if key:sub(1, #prefix) == prefix then
            table.insert(entries, { key, value })
        end
    end
    table.sort(entries, function(a, b) return a[1] < b[1] end)
    return {
        iter = function()
            local index = 0
            return function()
                index = index + 1
                local entry = entries[index]
                if entry == nil then return nil end
                return entry[1], entry[2]
            end
        end,
    }
end
function db:update(key, value)
    self.data[key] = value
    return true
end

local original_level_db = LevelDb
LevelDb = function()
    return db
end

local pin = require("mohu_pin")
local store = pin._test.user_db
assert(store.acquire() == true)

local entries = store.list_all()
assert(#entries == 3, #entries)
local sources = {}
for _, entry in ipairs(entries) do
    sources[entry.phrase] = entry.source
end
assert(sources.Manual == "pin")
assert(sources.Made == "panacea")
assert(sources.Old == "legacy")

store.move_pin_down("aa", "Made")
assert(db.data["aa \tMade"]:match("s=panacea"))
assert(db.data["aa \tManual"]:match("s=pin"))

assert(store.remove("aa", "Manual") == true)
assert(store.unpack_entry("aa \tManual", db.data["aa \tManual"]) == nil)
assert(db.data["aa \tManual"]:match("s=pin"))

store.release()
LevelDb = original_level_db

print("pin store logic: ok")
