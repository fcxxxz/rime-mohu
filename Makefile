ZRM_DESTDIR ?= $(abspath ./dist-zrm)
FLYPY_DESTDIR ?= $(abspath ./dist-flypy)
TIGER_NGRAM ?= tiger_sentence_native/mohu-sentence-ngram-v5.bin
# Optional, complete Windows DLL closure staged by CI. Local macOS-only builds
# may leave it unset.
TIGER_WINDOWS_RUNTIME ?=
WINDOWS_RUNTIME_ARG = $(if $(strip $(TIGER_WINDOWS_RUNTIME)),--windows-runtime "$(TIGER_WINDOWS_RUNTIME)")

quick: classics tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen opencc
	uv run tools/build_flypy_assets.py
dict: classics tiger_aux chars fixed_tiger update-compact-dicts
	uv run tools/build_flypy_assets.py
all: quick dict

mohu_flypy_custom_phrases.txt: mohu_zrm_custom_phrases.txt tools/build_flypy_assets.py
	uv run tools/build_flypy_assets.py --custom-phrases-only

mohu_lexicons: tiger_sentence_native/mohu_tiger.lexicon.txt tools/build_mohu_lexicons.py tools/flypyify.py tools/zrmify.py
	test -f mohu_zrm.chars.dict.yaml
	uv run tools/build_mohu_lexicons.py
	test -f tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt
	test -f tiger_sentence_native/data/flypy/mohu_flypy.lexicon.txt

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
mohu_zrm.chars.dict.yaml: tools/data/tiger_compatibility_chars.txt tiger.dict.yaml tools/data/chars.txt tools/data/chars.dict.yaml tools/data/tiger_aux.txt tools/data/pinyin_simp.txt tools/gen_chars.py tools/modern_readings.py tools/tiger_aux.py tools/tiger_compatibility.py tools/utils.py tools/write_if_changed.py
	uv run tools/gen_chars.py --simplified | uv run tools/write_if_changed.py $@ --ignore-version
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
	rm -rf dist-mohu-llm-zrm dist-mohu-llm-flypy
	rm -f $(chars_output)
	rm -f $(tiger_rank_output)
	rm -f dazhu*.txt
	make -C opencc clean

# Native Tiger sentence assets are kept separate from the generated source
# dictionaries and are copied into the flat scheme package by its builder.
tigerengine-native: tiger_sentence_native/tigerengine.cc tiger_sentence_native/tigerengine_lua.cc tiger_sentence_native/tigerengine.h
	@test -f tiger_sentence_native/lua-5.4.6/src/lua.hpp || \
		(echo "Lua 5.4 headers are required; see tiger_sentence_native/README.md" >&2; exit 1)
	zsh tiger_sentence_native/build.sh

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

# 用户调频层引擎测试：真实模型上的翻转/快照回环/权重开关；
# 模型缺失（未安装或未设 TIGER_NGRAM）时自动跳过。
tigerengine-user-model:
	clang++ -std=c++17 -O2 -I tiger_sentence_native tests/tigerengine_user_model_test.cc \
		tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_user_model_test
	/tmp/tigerengine_user_model_test

# 读音先验引擎测试：第 5 列（读音条件简频）压制多音字罕用读音拼字
# （mohuz→万虎）；模型缺失或旧 4 列码表时自动跳过。
tigerengine-reading-prior:
	clang++ -std=c++17 -O2 -I tiger_sentence_native tests/tigerengine_reading_prior_test.cc \
		tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_reading_prior_test
	/tmp/tigerengine_reading_prior_test

tigerengine-context:
	clang++ -std=c++17 -O2 -I tiger_sentence_native tests/tigerengine_context_test.cc \
		tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_context_test
	/tmp/tigerengine_context_test

# 词级上下文候选评分引擎测试：load_word_scorer/context_word_scores 的
# 可用性语义、方向性、OOV、确定性与 MHCTN01 容器词层等价；模型缺失
# （未安装或未设 TIGER_NGRAM/TIGER_WORD_NGRAM）时自动跳过。
tigerengine-word-score:
	clang++ -std=c++17 -O2 -I tiger_sentence_native tests/tigerengine_word_score_test.cc \
		tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_word_score_test
	/tmp/tigerengine_word_score_test

# Decode latency benchmark; pass the installed model explicitly, e.g.
#   make tigerengine-bench TIGER_NGRAM=~/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin
tigerengine-bench:
	@test -n "$(TIGER_NGRAM)" || (echo "Error: set TIGER_NGRAM to mohu-sentence-ngram-v5.bin" >&2; exit 2)
	clang++ -std=c++17 -O2 -I tiger_sentence_native tiger_sentence_native/bench_decode.cc \
		tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_bench
	/tmp/tigerengine_bench "$(TIGER_NGRAM)" tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt \
		$(TIGER_BENCH_ARGS)

dist-zrm: quick mohu_lexicons tigerengine-native
	uv run tools/build_flat_dist.py zrm "$(ZRM_DESTDIR)" $(WINDOWS_RUNTIME_ARG)

dist-flypy: quick mohu_lexicons tigerengine-native
	uv run tools/build_flat_dist.py flypy "$(FLYPY_DESTDIR)" $(WINDOWS_RUNTIME_ARG)

model-dist:
	@test -f "$(TIGER_NGRAM)" || (echo "Error: set TIGER_NGRAM to mohu-sentence-ngram-v5.bin" >&2; exit 1)
	rm -rf model-dist
	mkdir -p model-dist/mohu/model
	install -m 0644 "$(TIGER_NGRAM)" model-dist/mohu/model/mohu-sentence-ngram-v5.bin

test: dist-zrm dist-flypy mohu_lexicons
	$(MAKE) tigerengine-safety
	$(MAKE) tigerengine-lua-safety
	$(MAKE) tigerengine-user-model
	$(MAKE) tigerengine-reading-prior
	$(MAKE) tigerengine-context
	uv run tools/import_classics.py check
	uv run python -m unittest tests.test_classics_import -v
	uv run python -m unittest tests.test_tiger_aux -v
	uv run python -m unittest tests.test_tiger_lexicon_fly -v
	uv run python -m unittest tests.test_mohu_lexicons -v
	uv run python -m unittest tests.test_flat_distribution -v
	uv run python -m unittest tests.test_collect_windows_runtime -v
	uv run python -m unittest tests.test_split_release_workflow -v
	uv run python -m unittest tests.test_flypy_assets -v
	uv run python -m unittest tests.test_mohu_migration -v
	uv run python -m unittest tests.test_tiger_symbol_workflow -v
	uv run python -m unittest tests.test_merge_emoji -v
	PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.test_skin_editor_local_server -v
	bash tests/rime_sync_conf_test.sh
	lua tests/mohu_candidate_override_test.lua
	lua tests/mohu_candidate_weight_reset_test.lua
	lua tests/mohu_pin_store_test.lua
	lua tests/option_sync_test.lua
	lua tests/mohu_candidate_manager_test.lua
	lua tests/mohu_candidate_manager_config_test.lua
	lua tests/mohu_tiger_sentence_native_test.lua
	lua tests/mohu_tiger_log_compat_test.lua
	lua tests/mohu_tiger_user_model_test.lua
	lua tests/mohu_tiger_context_test.lua
	lua tests/mohu_tiger_two_char_test.lua
	lua tests/mohu_personal_lexicon_test.lua
	lua tests/mohu_path_test.lua
	lua tests/mohu_model_version_test.lua
	lua tests/mohu_tiger_no_early_commit_test.lua
	lua tests/mohu_tiger_selected_segment_test.lua
	lua tests/mohu_reorder_filter_lexicon_test.lua
	lua tests/mohu_word_order_filter_test.lua
	lua tests/mohu_freestyle_config_test.lua
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

.PHONY: quick all dict mohu_lexicons tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen emoji update-compact-dicts sync-essay dazhu opencc mdict model-dist tigerengine-native tigerengine-safety tigerengine-lua-safety tigerengine-user-model tigerengine-context tigerengine-word-score tigerengine-bench dist-zrm dist-flypy test lint-python
