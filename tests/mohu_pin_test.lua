package.path = "./lua/?.lua;" .. package.path

package.loaded.mohu = {
    str_is_chinese = function(text)
        return text ~= nil and text ~= ""
    end,
}

local pin = require("mohu_pin")
local subject = assert(pin._test, "mohu_pin._test is missing")

local capture = subject.new_capture("tnfb")
subject.append_query(capture, "t")
assert(capture.query == "t")
subject.append_text(capture, "头脑")
assert(capture.code == "tnfb")
assert(capture.query == "")
assert(capture.text == "头脑")
subject.backspace(capture)
assert(capture.text == "头")
subject.append_query(capture, "f")
subject.backspace(capture)
assert(capture.query == "")
assert(capture.text == "头")

local function engine_wrappers()
    local properties = {}
    local function wrapper()
        local context = {}
        function context:get_property(name)
            return properties[name] or ""
        end
        function context:set_property(name, value)
            properties[name] = value
        end
        return { context = context }
    end
    return wrapper(), wrapper()
end

local engine_a_processor, engine_a_filter = engine_wrappers()
local engine_b_processor, engine_b_filter = engine_wrappers()
subject.set_capture(engine_a_processor, subject.new_capture("aaaa"))
subject.set_capture(engine_b_processor, subject.new_capture("bbbb"))
assert(subject.get_capture(engine_a_filter).code == "aaaa")
assert(subject.get_capture(engine_b_filter).code == "bbbb")
subject.clear_capture(engine_a_filter)
assert(subject.get_capture(engine_a_processor) == nil)
assert(subject.get_capture(engine_b_filter).code == "bbbb")
subject.clear_capture(engine_b_processor)

assert(subject.selection_index(0, 0x31, 5, "12345") == 0)
assert(subject.selection_index(0, 0x32, 5, "12345") == 1)
assert(subject.selection_index(0, 0x3b, 5, "12345") == 1)
assert(subject.selection_index(0, 0x27, 5, "12345") == 2)
assert(subject.selection_index(5, 0x31, 5, "12345") == 5)
assert(subject.selection_index(0, 0x36, 5, "67890") == 0)

assert(subject.keycode_to_char(0x31) == "1")
assert(subject.keycode_to_char(0xffb4) == "4")
assert(subject.keycode_to_char(0xffae) == ".")
assert(subject.keycode_to_char(0xffab) == "+")
assert(subject.keycode_to_char(0xffad) == "-")
assert(subject.keycode_to_char(0xffaa) == "*")
assert(subject.keycode_to_char(0xffaf) == "/")
assert(subject.keycode_to_char(0xffac) == ",")
assert(subject.keycode_to_char(0xffbd) == "=")
assert(subject.keycode_to_char(0xff51) == nil)

assert(subject.is_printable_keysym(0x00e9))
assert(subject.is_printable_keysym(0x07e1))
assert(subject.is_printable_keysym(0x20ac))
assert(subject.is_printable_keysym(0x0101f600))
assert(not subject.is_printable_keysym(0xff51))

local empty_capture = subject.new_capture("a4vi")
assert(empty_capture.mode == "lookup")
assert(subject.capture_shows_status_candidate(empty_capture))
local lookup_comment = subject.capture_status_comment(empty_capture)
assert(lookup_comment == "[选词]")
empty_capture.mode = "literal"
assert(subject.capture_status_comment(empty_capture) == "[文本]")
empty_capture.mode = "lookup"
local empty_actions = {
    { 0x61, "query", "a" },
    { 0x41, "literal", "A" },
    { 0x34, "literal", "4" },
    { 0x20, "literal", " " },
    { 0x2d, "literal", "-" },
    { 0x3b, "quick_query", ";" },
    { 0x27, "literal", "'" },
    { 0xffb4, "literal", "4" },
    { 0xffae, "literal", "." },
    { 0xffab, "literal", "+" },
    { 0xffad, "literal", "-" },
    { 0xffaa, "literal", "*" },
    { 0xffaf, "literal", "/" },
    { 0xffac, "literal", "," },
    { 0xffbd, "literal", "=" },
}
for _, test in ipairs(empty_actions) do
    local action, char = subject.capture_key_action(empty_capture, test[1], "1234567890")
    assert(action == test[2] and char == test[3])
end

empty_capture.query = "vi"
assert(not subject.capture_shows_status_candidate(empty_capture))
local active_actions = {
    { 0x61, "query", "a" },
    { 0x31, "select", "1" },
    { 0xffb4, "select", "4" },
    { 0x20, "select", " " },
    { 0x3b, "select", ";" },
    { 0x27, "select", "'" },
    { 0x2d, "consume", "-" },
    { 0x41, "consume", "A" },
    { 0x00e9, "consume", nil },
    { 0x07e1, "consume", nil },
    { 0x20ac, "consume", nil },
    { 0x0101f600, "consume", nil },
}
for _, test in ipairs(active_actions) do
    local action, char = subject.capture_key_action(empty_capture, test[1], "1234567890")
    assert(action == test[2] and char == test[3])
end

local literal_capture = subject.new_capture("potter")
literal_capture.mode = "literal"
for _, test in ipairs({
    { 0x61, "literal", "a" },
    { 0x7a, "literal", "z" },
    { 0x20, "literal", " " },
    { 0x3b, "quick_query", ";" },
}) do
    local action, char = subject.capture_key_action(literal_capture, test[1], "1234567890")
    assert(action == test[2] and char == test[3])
end

assert(subject.selection_index(0, 0x34, 5, "12345") == 3)
assert(subject.selection_index(0, 0x35, 5, "12345") == 4)
assert(subject.selection_index(0, 0xffb4, 5, "12345") == 3)
assert(subject.selection_index(5, 0x31, 5, "12345") == 5)

local function key_event(keycode, modifiers)
    modifiers = modifiers or {}
    return {
        keycode = keycode,
        shift = function() return modifiers.shift or false end,
        ctrl = function() return modifiers.ctrl or false end,
        alt = function() return modifiers.alt or false end,
        caps = function() return modifiers.caps or false end,
        super = function() return modifiers.super or false end,
        release = function() return modifiers.release or false end,
    }
end

local function capture_env(ascii_punct, full_shape)
    local properties = {}
    local candidate_texts = { "一", "二", "三", "四", "五", "六" }
    local context

    local menu = {}
    function menu:prepare(count)
        return count
    end
    function menu:get_candidate_at(index)
        local text = context.input == ";w" and index == 0 and "？" or candidate_texts[index + 1]
        if text ~= nil then
            local candidate = { text = text, type = "phrase" }
            function candidate:get_genuine()
                return self
            end
            return candidate
        end
        return nil
    end

    local segment = { selected_index = 0, menu = menu }
    local composition = {}
    function composition:empty()
        return false
    end
    function composition:back()
        return segment
    end

    context = { input = "" , composition = composition }
    function context:get_property(name)
        return properties[name] or ""
    end
    function context:set_property(name, value)
        properties[name] = value
    end
    function context:clear()
        self.input = ""
    end
    function context:refresh_non_confirmed_composition()
    end
    function context:get_option(name)
        if name == "ascii_punct" then return ascii_punct or false end
        if name == "full_shape" then return full_shape or false end
        return false
    end

    local env = {
        pin_enable = true,
        select_keys = "12345",
        engine = {
            context = context,
            schema = { page_size = 5 },
        },
    }
    return env
end

local processor_env = capture_env()
local processor_capture = subject.new_capture("a4vi")
subject.set_capture(processor_env.engine, processor_capture)

for _, test in ipairs({
    { 0x41, "A" },
    { 0x34, "4" },
    { 0x20, " " },
    { 0x2d, "-" },
    { 0xffb4, "4" },
    { 0xffae, "。" },
    { 0xffac, "，" },
    { 0xffbd, "=" },
}) do
    assert(pin.pin_processor.func(key_event(test[1]), processor_env) == 1)
    assert(processor_capture.text:sub(-#test[2]) == test[2])
    assert(processor_capture.query == "")
end

local shifted_text = processor_capture.text
assert(pin.pin_processor.func(key_event(0xffe1, { shift = true }), processor_env) == 1)
assert(pin.pin_processor.func(key_event(0x41, { shift = true }), processor_env) == 1)
assert(pin.pin_processor.func(key_event(0x2b, { shift = true }), processor_env) == 1)
assert(pin.pin_processor.func(key_event(0x41, { shift = true, release = true }), processor_env) == 1)
assert(pin.pin_processor.func(key_event(0xffe1, { release = true }), processor_env) == 1)
assert(processor_capture.text == shifted_text .. "A+")
assert(processor_capture.mode == "lookup")

-- Shift+D only records one literal character. The following lowercase key must
-- immediately return to Chinese lookup so mixed words such as 3D打印机 work.
local mixed_env = capture_env(false)
local mixed_capture = subject.new_capture("sdd")
subject.set_capture(mixed_env.engine, mixed_capture)
assert(pin.pin_processor.func(key_event(0x33), mixed_env) == 1)
assert(pin.pin_processor.func(key_event(0x64, { shift = true }), mixed_env) == 1)
assert(mixed_capture.text == "3D" and mixed_capture.mode == "lookup")
assert(pin.pin_processor.func(key_event(0x61), mixed_env) == 1)
assert(mixed_capture.query == "a" and mixed_capture.text == "3D")
assert(pin.pin_processor.func(key_event(0x20), mixed_env) == 1)
assert(mixed_capture.text == "3D一" and mixed_capture.query == "")
subject.clear_capture(mixed_env.engine)

local shift_env = capture_env(false)
local shift_capture = subject.new_capture("shift")
subject.set_capture(shift_env.engine, shift_capture)
assert(pin.pin_processor.func(key_event(0xffe1, { shift = true }), shift_env) == 1)
assert(pin.pin_processor.func(key_event(0xffe1, { release = true }), shift_env) == 1)
assert(shift_capture.mode == "literal")
assert(pin.pin_processor.func(key_event(0xffe2, { shift = true }), shift_env) == 1)
assert(pin.pin_processor.func(key_event(0xffe2, { release = true }), shift_env) == 1)
assert(shift_capture.mode == "lookup")

assert(pin.pin_processor.func(key_event(0xffe1, { shift = true }), shift_env) == 1)
assert(pin.pin_processor.func(key_event(0xffe2, { shift = true }), shift_env) == 1)
assert(pin.pin_processor.func(key_event(0xffe2, { release = true }), shift_env) == 1)
assert(pin.pin_processor.func(key_event(0xffe1, { release = true }), shift_env) == 1)
assert(shift_capture.mode == "lookup")
subject.clear_capture(shift_env.engine)

local english_env = capture_env(false)
local english_capture = subject.new_capture("potter")
subject.set_capture(english_env.engine, english_capture)
assert(pin.pin_processor.func(key_event(0x3b, { ctrl = true }), english_env) == 1)
for _, char in ipairs({ "Y", "o", "u", " ", "d", "a", "r", "e" }) do
    assert(pin.pin_processor.func(key_event(string.byte(char)), english_env) == 1)
end
assert(english_capture.mode == "literal")
assert(english_capture.query == "")
assert(english_capture.text == "You dare")

for char in (" use my own spells agai me"):gmatch(".") do
    assert(pin.pin_processor.func(key_event(string.byte(char)), english_env) == 1)
end
assert(pin.pin_processor.func(key_event(0x2c), english_env) == 1)
assert(pin.pin_processor.func(key_event(0x50, { shift = true }), english_env) == 1)
for char in ("otter"):gmatch(".") do
    assert(pin.pin_processor.func(key_event(string.byte(char)), english_env) == 1)
end
assert(english_capture.text == "You dare use my own spells agai me，Potter")

assert(pin.pin_processor.func(key_event(0x3b, { ctrl = true }), english_env) == 1)
assert(english_capture.mode == "lookup")
assert(pin.pin_processor.func(key_event(0x3b, { ctrl = true }), english_env) == 1)
assert(english_capture.mode == "literal")

assert(pin.pin_processor.func(key_event(0x3b), english_env) == 1)
assert(english_capture.query == ";")
assert(pin.pin_processor.func(key_event(0x77), english_env) == 1)
assert(english_capture.query == "")
assert(english_capture.text == "You dare use my own spells agai me，Potter？")
subject.clear_capture(english_env.engine)

local ascii_punct_env = capture_env(true)
local ascii_punct_capture = subject.new_capture("ascii")
ascii_punct_capture.mode = "literal"
subject.set_capture(ascii_punct_env.engine, ascii_punct_capture)
assert(pin.pin_processor.func(key_event(0x2c), ascii_punct_env) == 1)
assert(pin.pin_processor.func(key_event(0x3f, { shift = true }), ascii_punct_env) == 1)
assert(ascii_punct_capture.text == ",?")
subject.clear_capture(ascii_punct_env.engine)

local chinese_punct_env = capture_env(false)
local chinese_punct_capture = subject.new_capture("punct")
chinese_punct_capture.mode = "literal"
subject.set_capture(chinese_punct_env.engine, chinese_punct_capture)
for _, keycode in ipairs({ 0x3c, 0x3e, 0x5b, 0x5d, 0x5f, 0x22, 0x22, 0x27, 0x27 }) do
    assert(pin.pin_processor.func(key_event(keycode), chinese_punct_env) == 1)
end
assert(chinese_punct_capture.text == "《》「」——“”‘’")
subject.clear_capture(chinese_punct_env.engine)

local full_shape_env = capture_env(false, true)
local full_shape_capture = subject.new_capture("full")
full_shape_capture.mode = "literal"
subject.set_capture(full_shape_env.engine, full_shape_capture)
for _, keycode in ipairs({ 0x40, 0x23, 0x26, 0x2d, 0x2b }) do
    assert(pin.pin_processor.func(key_event(keycode), full_shape_env) == 1)
end
assert(full_shape_capture.text == "＠＃＆－＋")
subject.clear_capture(full_shape_env.engine)

processor_capture.mode = "lookup"
assert(pin.pin_processor.func(key_event(0x61), processor_env) == 1)
assert(processor_capture.query == "a")
local active_text = processor_capture.text
for _, keycode in ipairs({ 0x2d, 0x41, 0xffbd, 0x00e9, 0x07e1, 0x20ac, 0x0101f600 }) do
    assert(pin.pin_processor.func(key_event(keycode), processor_env) == 1)
    assert(processor_capture.text == active_text and processor_capture.query == "a")
end

for _, modifiers in ipairs({
    { ctrl = true },
    { alt = true },
    { super = true },
    { release = true },
}) do
    assert(pin.pin_processor.func(key_event(0x62, modifiers), processor_env) == 1)
    assert(processor_capture.query == "a")
end

assert(pin.pin_processor.func(key_event(0xff0d, { shift = true }), processor_env) == 1)
assert(pin.pin_processor.func(key_event(0xff08, { ctrl = true }), processor_env) == 1)
assert(subject.get_capture(processor_env.engine) == processor_capture)
assert(processor_capture.query == "a")

processor_capture.query = "vi"
assert(pin.pin_processor.func(key_event(0x31), processor_env) == 1)
assert(processor_capture.text:sub(-#"一") == "一" and processor_capture.query == "")
processor_capture.query = "vi"
assert(pin.pin_processor.func(key_event(0xffb4), processor_env) == 1)
assert(processor_capture.text:sub(-#"四") == "四" and processor_capture.query == "")
for _, selection in ipairs({
    { 0x20, "一" },
    { 0x3b, "二" },
    { 0x27, "三" },
}) do
    processor_capture.query = "vi"
    assert(pin.pin_processor.func(key_event(selection[1]), processor_env) == 1)
    assert(processor_capture.text:sub(-#selection[2]) == selection[2])
end

processor_capture.query = "vi"
assert(pin.pin_processor.func(key_event(0xff08), processor_env) == 1)
assert(processor_capture.query == "v")

processor_capture.query = ""
processor_capture.text = ""
assert(pin.pin_processor.func(key_event(0xff0d), processor_env) == 1)
assert(subject.get_capture(processor_env.engine) == processor_capture)
assert(processor_capture.message == "请先选取要加入的词")

processor_capture.text = "取消"
assert(pin.pin_processor.func(key_event(0xff1b), processor_env) == 1)
assert(subject.get_capture(processor_env.engine) == nil)
subject.clear_capture(processor_env.engine)

local function make_db(update_result)
    local db = {
        data = {},
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
        table.sort(entries, function(left, right)
            return left[1] < right[1]
        end)
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
        if update_result == false then
            return false
        end
        self.data[key] = value
        return true
    end

    return db
end

local original_level_db = LevelDb
local db = make_db(true)
LevelDb = function()
    return db
end

assert(subject.acquire())
local ok, inserted = subject.ensure_pin("tnfb", "头脑风暴")
assert(ok and inserted)
local duplicate_ok, duplicate_inserted = subject.ensure_pin("tnfb", "头脑风暴")
assert(duplicate_ok and not duplicate_inserted)
assert(subject.query("tnfb")[1].phrase == "头脑风暴")

local original_candidate = Candidate
local original_yield = yield
local yielded = {}
Candidate = function(cand_type, start_pos, end_pos, text, comment)
    return {
        type = cand_type,
        start = start_pos,
        _end = end_pos,
        text = text,
        comment = comment,
    }
end
yield = function(candidate)
    table.insert(yielded, candidate)
end
pin.panacea_translator.func("tnfb", { start = 0, _end = 4 }, {
    pin_enable = true,
    escaped_infix = "\\\\",
    indicator = "📌",
})
assert(#yielded == 1)
assert(yielded[1].type == "pinned")
assert(yielded[1].text == "头脑风暴")
assert(yielded[1].comment == "📌")
Candidate = original_candidate
yield = original_yield

for _, return_key in ipairs({ 0xff0d, 0xff8d }) do
    local save_env = capture_env()
    local save_capture = subject.new_capture("save" .. return_key)
    save_capture.text = "成功"
    subject.set_capture(save_env.engine, save_capture)
    assert(pin.pin_processor.func(key_event(return_key), save_env) == 1)
    assert(subject.get_capture(save_env.engine) == nil)
    assert(save_env.engine.context.input == "save" .. return_key)
end
subject.release()

local failing_db = make_db(false)
LevelDb = function()
    return failing_db
end
assert(subject.acquire())
local failed_ok, failed_inserted = subject.ensure_pin("tnfb", "写入失败")
assert(not failed_ok and not failed_inserted)
assert(next(failing_db.data) == nil)

local failed_env = capture_env()
local failed_capture = subject.new_capture("fail")
failed_capture.text = "写入失败"
subject.set_capture(failed_env.engine, failed_capture)
assert(pin.pin_processor.func(key_event(0xff0d), failed_env) == 1)
assert(subject.get_capture(failed_env.engine) == failed_capture)
assert(failed_capture.message == "保存失败，请重试")
subject.release()

local original_ensure_pin = subject.user_db.ensure_pin
subject.user_db.ensure_pin = function()
    error("simulated storage failure")
end
local throwing_env = capture_env()
local throwing_capture = subject.new_capture("throw")
throwing_capture.text = "异常"
subject.set_capture(throwing_env.engine, throwing_capture)
assert(pin.pin_processor.func(key_event(0xff0d), throwing_env) == 1)
assert(subject.get_capture(throwing_env.engine) == throwing_capture)
assert(throwing_capture.message == "保存失败，请重试")
subject.user_db.ensure_pin = original_ensure_pin

local unavailable_db = make_db(true)
function unavailable_db:open()
    self.is_loaded = false
end
LevelDb = function()
    return unavailable_db
end
assert(not subject.acquire())
subject.release()

local recovered_db = make_db(true)
LevelDb = function()
    return recovered_db
end
assert(subject.acquire())
assert(subject.ensure_pin("tnfb", "恢复写入"))
subject.release()

local rearrange_db = make_db(true)
local original_values = {}
for index = 0, 7 do
    local key = "full \t词" .. index
    local value = "c=" .. (index + 8) .. " d=0 t=1"
    rearrange_db.data[key] = value
    original_values[key] = value
end
rearrange_db.update_count = 0
function rearrange_db:update(key, value)
    self.update_count = self.update_count + 1
    if self.update_count == 2 then
        return false
    end
    self.data[key] = value
    return true
end
LevelDb = function()
    return rearrange_db
end
assert(subject.acquire())
local rearrange_ok, rearrange_inserted = subject.ensure_pin("full", "新词")
assert(not rearrange_ok and not rearrange_inserted)
for key, value in pairs(original_values) do
    assert(rearrange_db.data[key] == value)
end
assert(rearrange_db.data["full \t新词"] == nil)
subject.release()

LevelDb = original_level_db

print("mohu pin logic: ok")
