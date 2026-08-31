-- Public Mohu sentence translator entry point.
-- Keep the implementation in the compatibility module while deployed packages migrate.
return dofile((debug.getinfo(1, "S").source:sub(2):gsub("mohu_sentence%.lua$", "mohu_tiger_sentence.lua")))
