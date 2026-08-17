DESTDIR ?= $(abspath ./dist)
ZRM_DESTDIR ?= $(abspath ./dist-zrm)
FLYPY_DESTDIR ?= $(abspath ./dist-flypy)

quick: tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen opencc
	uv run tools/build_flypy_assets.py
dict: tiger_aux chars fixed_tiger update-compact-dicts
	uv run tools/build_flypy_assets.py
all: quick dict

lint-python:
	uv run --with ruff ruff check tools

############
# 單字信息 #
############
chars_output := mohu_zrm.chars.dict.yaml opencc/mohu_chaifen.txt lua/zrmdb.txt
tiger_rank_output := lua/tiger_rank.txt
tiger_aux: tools/data/tiger_aux.txt
chars: mohu_zrm.chars.dict.yaml
pinyin_reverse: mohu_pinyin.dict.yaml
zrmdb: lua/zrmdb.txt
chaifen: opencc/mohu_chaifen.txt
	make -C opencc mohu_chaifen.ocd2

tools/data/tiger_aux.txt: tiger.dict.yaml tools/data/chars.txt tools/data/chars.dict.yaml tools/gen_tiger_aux.py tools/tiger_aux.py
	uv run tools/gen_tiger_aux.py > $@
mohu_zrm.chars.dict.yaml: tools/data/chars.txt tools/data/chars.dict.yaml tools/data/tiger_aux.txt tools/data/pinyin_simp.txt tools/gen_chars.py tools/modern_readings.py tools/utils.py
	uv run tools/gen_chars.py --simplified > $@
mohu_pinyin.dict.yaml: tools/data/pinyin_simp.txt tools/build_pinyin_reverse.py
	uv run tools/build_pinyin_reverse.py > $@
lua/zrmdb.txt: tools/data/tiger_aux.txt tools/gen_zrmdb.py tools/utils.py
	uv run tools/gen_zrmdb.py > $@
opencc/mohu_chaifen.txt: tools/data/tiger_chaifen.txt tools/data/chars.txt tiger.dict.yaml tools/tiger_aux.py tools/gen_chaifen_filter.py
	uv run tools/gen_chaifen_filter.py > $@

##########
# OpenCC #
##########
emoji: opencc/mohu_emoji.txt

opencc/mohu_emoji.txt: tools/data/mohu_emoji_base.txt tools/data/tiger_emoji.txt tools/merge_emoji.py
	uv run tools/merge_emoji.py

opencc: chaifen emoji
	make -C opencc

########
# 詞庫 #
########
update-compact-dicts:
	uv run ./tools/update_compact_dicts.sh

fixed_tiger: tiger_aux tools/data/pinyin_simp.txt tools/modern_readings.py
	uv run tools/rebuild_fixed_tiger.py

sync-essay:
	uv run tools/sync_essay.py

#########
# mdict #
#########

mdict: mohu.mdd mohu.mdx

mohu.mdd: tools/mdict/main.css
	mdict -a tools/mdict mohu.mdd

mohu.mdx: tools/data/chars.txt tools/data/mohu_chai.txt tools/gen_mdx.py 
	uv run tools/gen_mdx.py mohu.mdx

########
# 其他 #
########
dazhu:
	uv run tools/dazhu.py > dazhu-hant2s.txt
	uv run tools/dazhu.py -c='' > dazhu-hant.txt
	uv run tools/dazhu.py -c='' --dict mohu_zrm_fixed.dict.yaml > dazhu-hans.txt

clean:
	rm -rf mdict-out
	rm -f mohu.mdd mohu.mdx
	rm -rf dist
	rm -rf dist-zrm dist-flypy
	rm -f $(chars_output)
	rm -f $(tiger_rank_output)
	rm -f dazhu*.txt
	make -C opencc clean

# Installs the traditional version into DESTDIR
dist: quick
	if [ "$(abspath $(DESTDIR))" = "$(abspath ./dist)" ]; then rm -rf "$(DESTDIR)"; fi
	mkdir -p $(DESTDIR)
	cp -a README*.md LICENSE etc $(DESTDIR)
	cp -a mohu* $(DESTDIR)
	cp -a default.yaml key_bindings.yaml punctuation.yaml symbols.yaml $(DESTDIR)
	cp -a recipe.yaml recipes $(DESTDIR)
	cp -a squirrel.yaml $(DESTDIR)
	cp -a tiger.*.yaml $(DESTDIR)
	cp -a *.gram $(DESTDIR)
	cp -a Rime皮肤编辑器 $(DESTDIR)
	rm -rf "$(DESTDIR)/Rime皮肤编辑器/local/__pycache__"

	mkdir -p $(DESTDIR)/lua
	cp -a lua/* $(DESTDIR)/lua

	mkdir -p $(DESTDIR)/opencc
	cp -a opencc/*.ocd2 opencc/*.json $(DESTDIR)/opencc
	cp -a opencc/mohu_TSPhrases.txt $(DESTDIR)/opencc

	rm -rf dist/*.userdb  # Just in case

dist-zrm: quick
	uv run tools/build_split_dist.py zrm "$(ZRM_DESTDIR)"

dist-flypy: quick
	uv run tools/build_split_dist.py flypy "$(FLYPY_DESTDIR)"

test: dist
	uv run python -m unittest tests.test_tiger_aux -v
	uv run python -m unittest tests.test_mohu_config -v
	uv run python -m unittest tests.test_flypy_assets -v
	uv run python -m unittest tests.test_mohu_migration -v
	uv run python -m unittest tests.test_tiger_symbol_workflow -v
	uv run python -m unittest tests.test_merge_emoji -v
	PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.test_skin_editor_local_server -v
	bash tests/simp_dist_config_test.sh $(DESTDIR)
	lua tests/mohu_candidate_override_test.lua
	lua tests/mohu_candidate_weight_reset_test.lua
	lua tests/mohu_pin_store_test.lua
	lua tests/mohu_candidate_manager_test.lua
	lua tests/mohu_candidate_manager_config_test.lua
	lua tests/mohu_freestyle_config_test.lua
	lua tests/tiger_aux_config_test.lua
	lua tests/mohu_contextual_translator_test.lua
	lua tests/mohu_charset_filter_test.lua
	lua tests/mohu_hint_filter_runtime_test.lua
	lua tests/mohu_express_tiger_test.lua
	lua tests/mohu_pin_test.lua
	lua tests/mohu_symbol_commands_test.lua
	lua tests/mohu_skin_command_test.lua
	lua tests/rime_skin_editor_test.lua
	node tests/skin_editor_core_test.js
	node tests/schema_settings_test.js
	node tests/skin_editor_integration_test.js
	cp -a /usr/share/opencc/* dist/opencc       2>/dev/null || true
	cp -a /usr/local/share/opencc/* dist/opencc 2>/dev/null || true
	cp -a /opt/homebrew/share/opencc/* dist/opencc 2>/dev/null || true
	test -f dist/opencc/t2tw.json || (echo "Error: cannot find shared opencc data!" && exit 1)

	mira -C /tmp/mira-cache tests/mohu_zrm.test.yaml
	mira -C /tmp/mira-cache tests/mohu_flypy.test.yaml
	mira -C /tmp/mira-cache tests/tiger.test.yaml
	mira -C /tmp/mira-cache tests/mohu_tiger_priority.test.yaml
	mira -C /tmp/mira-cache tests/mohu_candidate_override.test.yaml
	mira -C /tmp/mira-cache tests/mohu.hint.test.yaml
	mira -C /tmp/mira-cache tests/mohu_aux.test.yaml
	mira -C /tmp/mira-cache tests/mohu_candidate_override_sentence.test.yaml
	mira -C /tmp/mira-cache tests/mohu_candidate_override_fixed.test.yaml
	rm -rf /tmp/mira-cache

.PHONY: quick all dict tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen emoji update-compact-dicts sync-essay dazhu opencc mdict dist dist-zrm dist-flypy test lint-python
