DESTDIR ?= $(abspath ./dist)
ZRM_DESTDIR ?= $(abspath ./dist-zrm)
FLYPY_DESTDIR ?= $(abspath ./dist-flypy)
LLM_DESTDIR ?= $(abspath ./dist-llm)
TIGER_NGRAM ?= tiger_sentence_native/sentence-ngram-mobile.bin

quick: classics tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen opencc
	uv run tools/build_flypy_assets.py
dict: classics tiger_aux chars fixed_tiger update-compact-dicts
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
mohu_zrm.chars.dict.yaml: tools/data/tiger_compatibility_chars.txt tiger.dict.yaml tools/data/chars.txt tools/data/chars.dict.yaml tools/data/tiger_aux.txt tools/data/pinyin_simp.txt tools/gen_chars.py tools/modern_readings.py tools/tiger_aux.py tools/tiger_compatibility.py tools/utils.py
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
classics:
	uv run tools/import_classics.py build

check-classics:
	uv run tools/import_classics.py check
	uv run python -m unittest tests.test_classics_import -v

update-compact-dicts:
	uv run ./tools/update_compact_dicts.sh

fixed_tiger: tiger_aux tiger.dict.yaml tools/data/pinyin_simp.txt tools/data/simp_chars.txt tools/data/tiger_race_profile.tsv tools/modern_readings.py tools/tiger_compatibility.py
	uv run tools/rebuild_fixed_tiger.py

tools/data/tiger_compatibility_chars.txt: fixed_tiger

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

# Native Tiger sentence assets are kept separate from the portable scheme
# bundle.  The Qwen checkpoint and LuaSocket runtime are installed locally by
# the native deployment instructions and are intentionally not copied here.
tigerengine-native: tiger_sentence_native/tigerengine.cc tiger_sentence_native/tigerengine_lua.cc tiger_sentence_native/tigerengine.h
	@test -f tiger_sentence_native/lua-5.4.6/src/lua.hpp || \
		(echo "Lua 5.4 headers are required; see tiger_sentence_native/README.md" >&2; exit 1)
	zsh tiger_sentence_native/build.sh

native-dist: dist tigerengine-native
	@test -f "$(TIGER_NGRAM)" || (echo "Error: TIGER_NGRAM not found at $(TIGER_NGRAM); set TIGER_NGRAM=/path/to/sentence-ngram-mobile.bin" >&2; exit 1)
	mkdir -p "$(DESTDIR)/tiger" "$(DESTDIR)/lua"
	cp tiger_sentence_native/mohu_tiger_sentence.schema.yaml "$(DESTDIR)/"
	cp tiger_sentence_native/mohu_tiger_sentence.lua tiger_sentence_native/mohu_tiger_reranker.lua tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_model_catalog.lua tiger_sentence_native/mohu_tiger_model_menu.lua "$(DESTDIR)/lua/"
	cp tiger_sentence_native/qwen35_scorer.py tiger_sentence_native/run_qwen35_scorer.command tiger_sentence_native/install_qwen35_launch_agent.command tiger_sentence_native/scorer_models.zsh tiger_sentence_native/switch_qwen_model.command tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_reranker_profile_qwen3_06b.lua "$(DESTDIR)/tiger/"
	if [ ! -f tiger_sentence_native/libtigerengine.dylib ]; then :; else \
		dylib_tmp="$(DESTDIR)/tiger/.libtigerengine.dylib.$$$$"; \
		trap 'rm -f "$$dylib_tmp"' EXIT INT TERM; \
		cp tiger_sentence_native/libtigerengine.dylib "$$dylib_tmp"; \
		codesign --verify --strict "$$dylib_tmp"; \
		mv -f "$$dylib_tmp" "$(DESTDIR)/tiger/libtigerengine.dylib"; \
		trap - EXIT INT TERM; \
	fi
	test ! -f tiger_sentence_native/mohu_tiger.lexicon.txt || cp tiger_sentence_native/mohu_tiger.lexicon.txt "$(DESTDIR)/tiger/"
	cp "$(TIGER_NGRAM)" "$(DESTDIR)/tiger/sentence-ngram-mobile.bin"
	test -f "$(DESTDIR)/tiger/scorer_models.zsh"
	test -f "$(DESTDIR)/tiger/mohu_tiger_reranker_profile.lua"
	test -f "$(DESTDIR)/tiger/mohu_tiger_reranker_profile_qwen3_06b.lua"
	test -x "$(DESTDIR)/tiger/run_qwen35_scorer.command"
	test -x "$(DESTDIR)/tiger/install_qwen35_launch_agent.command"
	test -x "$(DESTDIR)/tiger/switch_qwen_model.command"
	test -f "$(DESTDIR)/lua/option_sync.lua"
	test -f "$(DESTDIR)/lua/option_state.lua"
	test -f "$(DESTDIR)/lua/mohu_tiger_model_catalog.lua"
	test -f "$(DESTDIR)/lua/mohu_tiger_model_menu.lua"

# Independent overlay containing the optional 魔虎大模型 runtime.  Model
# weights are deliberately excluded; install them separately from the
# registry manifests under tiger/models/.
llm-dist: tigerengine-native
	@test -f "$(TIGER_NGRAM)" || (echo "Error: TIGER_NGRAM not found at $(TIGER_NGRAM); set TIGER_NGRAM=/path/to/sentence-ngram-mobile.bin" >&2; exit 1)
	if [ "$(abspath $(LLM_DESTDIR))" = "$(abspath ./dist-llm)" ]; then rm -rf "$(LLM_DESTDIR)"; fi
	mkdir -p "$(LLM_DESTDIR)/tiger/models" "$(LLM_DESTDIR)/lua"
	cp tiger_sentence_native/mohu_tiger_sentence.schema.yaml "$(LLM_DESTDIR)/"
	cp tiger_sentence_native/mohu_tiger_sentence.lua tiger_sentence_native/mohu_tiger_reranker.lua tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_model_catalog.lua tiger_sentence_native/mohu_tiger_model_menu.lua "$(LLM_DESTDIR)/lua/"
	cp tiger_sentence_native/qwen35_scorer.py tiger_sentence_native/run_qwen35_scorer.command tiger_sentence_native/install_qwen35_launch_agent.command tiger_sentence_native/scorer_models.zsh tiger_sentence_native/switch_qwen_model.command tiger_sentence_native/mohu_tiger_reranker_profile_qwen3_06b.lua "$(LLM_DESTDIR)/tiger/"
	cp tiger_sentence_native/README.md tiger_sentence_native/QWEN35_SCORER.md "$(LLM_DESTDIR)/tiger/"
	cp tiger_sentence_native/models/README.md tiger_sentence_native/models/*.manifest "$(LLM_DESTDIR)/tiger/models/"
	if [ ! -f tiger_sentence_native/libtigerengine.dylib ]; then :; else \
		dylib_tmp="$(LLM_DESTDIR)/tiger/.libtigerengine.dylib.$$$$"; \
		trap 'rm -f "$$dylib_tmp"' EXIT INT TERM; \
		cp tiger_sentence_native/libtigerengine.dylib "$$dylib_tmp"; \
		codesign --verify --strict "$$dylib_tmp"; \
		mv -f "$$dylib_tmp" "$(LLM_DESTDIR)/tiger/libtigerengine.dylib"; \
		trap - EXIT INT TERM; \
	fi
	test -f tiger_sentence_native/mohu_tiger.lexicon.txt || (echo "Error: mohu_tiger.lexicon.txt is required" >&2; exit 1)
	cp tiger_sentence_native/mohu_tiger.lexicon.txt "$(LLM_DESTDIR)/tiger/"
	cp "$(TIGER_NGRAM)" "$(LLM_DESTDIR)/tiger/sentence-ngram-mobile.bin"
	test -f "$(LLM_DESTDIR)/tiger/libtigerengine.dylib"
	test -f "$(LLM_DESTDIR)/tiger/mohu_tiger.lexicon.txt"
	test -f "$(LLM_DESTDIR)/tiger/sentence-ngram-mobile.bin"
	test -f "$(LLM_DESTDIR)/tiger/scorer_models.zsh"
	test -x "$(LLM_DESTDIR)/tiger/run_qwen35_scorer.command"
	test -x "$(LLM_DESTDIR)/tiger/install_qwen35_launch_agent.command"
	test -x "$(LLM_DESTDIR)/tiger/switch_qwen_model.command"
	test -f "$(LLM_DESTDIR)/tiger/models/README.md"
	test -f "$(LLM_DESTDIR)/lua/mohu_tiger_model_catalog.lua"
	test -f "$(LLM_DESTDIR)/lua/mohu_tiger_model_menu.lua"

tigerengine-safety:
	clang++ -std=c++17 -O2 -I tiger_sentence_native tests/tigerengine_safety_test.cc tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_safety_test
	/tmp/tigerengine_safety_test

tigerengine-lua-safety:
	@if [ -f tiger_sentence_native/lua-5.4.6/src/liblua.a ]; then \
		clang++ -std=c++17 -O2 -I tiger_sentence_native -I tiger_sentence_native/lua-5.4.6/src \
			tests/tigerengine_lua_safety_test.cc tiger_sentence_native/tigerengine.cc \
			tiger_sentence_native/tigerengine_lua.cc tiger_sentence_native/lua-5.4.6/src/liblua.a \
			-lm -ldl -o /tmp/tigerengine_lua_safety_test; \
		/tmp/tigerengine_lua_safety_test; \
	else \
		echo "tigerengine Lua safety tests skipped (Lua 5.4 static library not present)"; \
	fi

dist-zrm: quick
	uv run tools/build_split_dist.py zrm "$(ZRM_DESTDIR)"

dist-flypy: quick
	uv run tools/build_split_dist.py flypy "$(FLYPY_DESTDIR)"

test: dist
	$(MAKE) tigerengine-safety
	$(MAKE) tigerengine-lua-safety
	uv run tools/import_classics.py check
	uv run python -m unittest tests.test_classics_import -v
	uv run python -m unittest tests.test_tiger_aux -v
	uv run python -m unittest tests.test_mohu_config -v
	uv run python -m unittest tests.test_mohu_tiger_sentence_native -v
	uv run python -m unittest tests.test_tiger_lexicon_fly -v
	uv run python -m unittest tests.test_qwen35_scorer -v
	uv run python -m unittest tests.test_tiger_reranker_eval -v
	uv run python -m unittest tests.test_flypy_assets -v
	uv run python -m unittest tests.test_mohu_migration -v
	uv run python -m unittest tests.test_tiger_symbol_workflow -v
	uv run python -m unittest tests.test_merge_emoji -v
	PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.test_skin_editor_local_server -v
	bash tests/rime_sync_conf_test.sh
	bash tests/simp_dist_config_test.sh $(DESTDIR)
	lua tests/mohu_candidate_override_test.lua
	lua tests/mohu_candidate_weight_reset_test.lua
	lua tests/mohu_pin_store_test.lua
	lua tests/option_sync_test.lua
	lua tests/mohu_candidate_manager_test.lua
	lua tests/mohu_candidate_manager_config_test.lua
	lua tests/mohu_tiger_sentence_native_test.lua
	lua tests/mohu_tiger_no_early_commit_test.lua
	lua tests/mohu_tiger_selected_segment_test.lua
	lua tests/mohu_tiger_translator_rerank_test.lua
	lua tests/mohu_tiger_reranker_test.lua
	lua tests/mohu_tiger_reranker_socket_recovery_test.lua
	lua tests/mohu_reorder_filter_lexicon_test.lua
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
	mira -C /tmp/mira-cache tests/mohu.ijrq.test.yaml
	rm -rf /tmp/mira-cache

.PHONY: quick all dict tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen emoji update-compact-dicts sync-essay dazhu opencc mdict dist tigerengine-native native-dist llm-dist tigerengine-safety tigerengine-lua-safety dist-zrm dist-flypy test lint-python
