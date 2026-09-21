ZRM_DESTDIR ?= $(abspath ./dist-zrm)
FLYPY_DESTDIR ?= $(abspath ./dist-flypy)
MOBILE_ZRM_DESTDIR ?= $(abspath ./dist-mobile-zrm)
MOBILE_FLYPY_DESTDIR ?= $(abspath ./dist-mobile-flypy)
TIGER_NGRAM ?= tiger_sentence_native/mohu-sentence-ngram-v5.bin
MOHU_LUA_BIN ?= lua
TIGER_MEMORY_OUTPUT ?= .tmp/native-tests/tigerengine-windows-mapping.json
# Optional, complete Windows DLL closure staged by CI. Local macOS-only builds
# may leave it unset.
TIGER_WINDOWS_RUNTIME ?=
WINDOWS_RUNTIME_ARG = $(if $(strip $(TIGER_WINDOWS_RUNTIME)),--windows-runtime "$(TIGER_WINDOWS_RUNTIME)")
# 魔虎语义进程内 ONNX 推理：编译期需要 onnxruntime 头文件，链接期使用
# tiger_sentence_native/ 内随包分发的 libonnxruntime.1.dylib（@loader_path）。
# ONNXRUNTIME_HOME 可覆盖（Windows CI 指向官方 win-x64 zip 解压目录；
# 同时给出 include 与 include/onnxruntime 两种布局的搜索路径）。
ONNXRUNTIME_HOME ?= /opt/homebrew/opt/onnxruntime
ORT_INCLUDES = -I$(ONNXRUNTIME_HOME)/include/onnxruntime -I$(ONNXRUNTIME_HOME)/include -I tiger_sentence_native
ifeq ($(OS),Windows_NT)
ORT_TEST_LIBS = -L$(ONNXRUNTIME_HOME)/lib -lonnxruntime
# MinGW 不识别 onnxruntime 头文件在 _WIN32 分支使用的 MSVC 写法
# `_stdcall`（GCC 关键字是双下划线 __stdcall；x64 上该调用约定本就被
# 忽略），用预处理器映射绕过。
ORT_DEFINES = -D_stdcall=__stdcall
TIGER_EXTRA_LDFLAGS =
else
ORT_TEST_LIBS = -L$(ONNXRUNTIME_HOME)/lib -lonnxruntime -Wl,-rpath,$(ONNXRUNTIME_HOME)/lib
ORT_DEFINES =
TIGER_EXTRA_LDFLAGS = -framework Accelerate
endif

quick: classics tiger_aux fixed_tiger sync_flykey chars pinyin_reverse zrmdb chaifen opencc
	uv run tools/build_flypy_assets.py
	$(MAKE) mohu_lexicons

dict: classics tiger_aux chars fixed_tiger sync_flykey update-compact-dicts
	uv run tools/build_flypy_assets.py
	$(MAKE) mohu_lexicons

all: quick dict

sync_flykey: tools/data/mohu_fly_keys.tsv tools/fly_keys.py tools/sync_flykey_config.py tools/sync_flykey_quickcodes.py fixed_tiger
	uv run python tools/sync_flykey_config.py --apply
	uv run python tools/sync_flykey_quickcodes.py --apply

flykey-check: tools/data/mohu_fly_keys.tsv tools/fly_keys.py tools/sync_flykey_config.py tools/sync_flykey_quickcodes.py
	uv run python tools/sync_flykey_config.py --check
	uv run python tools/sync_flykey_quickcodes.py --check
	uv run python tools/sync_flykey_quickcodes.py --check --scheme flypy mohu_flypy_fixed.dict.yaml mohu_flypy_fixed_legacy.dict.yaml

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

tools/data/tiger_aux.txt: tiger.dict.yaml tools/data/chars.txt tools/data/chars.dict.yaml tools/data/tiger_chaifen.txt tools/gen_tiger_aux.py tools/tiger_aux.py
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

fixed_tiger: tiger_aux tiger.dict.yaml tools/data/pinyin_simp.txt tools/data/simp_chars.txt tools/data/tiger_race_profile.tsv tools/data/mohu_fixed_code_claims.tsv tools/data/mohu_fixed_secondary_codes.tsv tools/data/mohu_fixed_simp_legacy_chars.txt tools/data/mohu_fixed_char_code_overrides.tsv tools/data/mohu_fly_keys.tsv tools/fly_keys.py tools/modern_readings.py tools/tiger_compatibility.py
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
	rm -rf dist-mobile-zrm dist-mobile-flypy
	rm -f rime-mohu-mobile-zrm-lite.zip rime-mohu-mobile-flypy-lite.zip
	rm -rf dist-mohu-llm-zrm dist-mohu-llm-flypy
	rm -f $(chars_output)
	rm -f $(tiger_rank_output)
	rm -f dazhu*.txt
	make -C opencc clean

# Native Tiger sentence assets are kept separate from the generated source
# dictionaries and are copied into the flat scheme package by its builder.
# 重编引擎依赖 macOS 工具链（zsh + codesign + Accelerate）；Linux CI（万象
# 同步的 dist 校验）只消费仓库里已提交的 dylib，不重编。dylib 缺失时在
# 非 Darwin 上直接报错而不是调用不存在的工具链（2026-09-04 起的每夜
# wanxiang-sync 失败即源于此）。
UNAME_S := $(shell uname -s)

tigerengine-native: tiger_sentence_native/tigerengine.cc tiger_sentence_native/tigerengine_lua.cc tiger_sentence_native/tigerengine.h tiger_sentence_native/semantic_infer.h tiger_sentence_native/libonnxruntime.1.dylib
ifeq ($(UNAME_S),Darwin)
	@test -f tiger_sentence_native/lua-5.4.6/src/lua.hpp || \
		(echo "Lua 5.4 headers are required; see tiger_sentence_native/README.md" >&2; exit 1)
	zsh tiger_sentence_native/build.sh
else
	@test -f tiger_sentence_native/libtigerengine.dylib || \
		(echo "Error: engine rebuild is macOS-only; commit libtigerengine.dylib or build on macOS" >&2; exit 1)
	@echo "tigerengine-native: non-macOS host, using committed libtigerengine.dylib"
endif

tigerengine-safety:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_safety_test.cc tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_safety_test
	/tmp/tigerengine_safety_test

tigerengine-lua-safety:
	@if [ -f tiger_sentence_native/lua-5.4.6/src/liblua.a ]; then \
		clang++ -std=c++17 -O2 $(ORT_INCLUDES) -I tiger_sentence_native/lua-5.4.6/src \
			tests/tigerengine_lua_safety_test.cc tiger_sentence_native/tigerengine.cc \
			tiger_sentence_native/tigerengine_lua.cc tiger_sentence_native/lua-5.4.6/src/liblua.a \
			-lm -ldl $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_lua_safety_test; \
		/tmp/tigerengine_lua_safety_test; \
	else \
		echo "tigerengine Lua safety tests skipped (Lua 5.4 static library not present)"; \
	fi

# 用户调频层引擎测试：真实模型上的翻转/快照回环/权重开关；
# 模型缺失（未安装或未设 TIGER_NGRAM）时自动跳过。
tigerengine-user-model:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_user_model_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_user_model_test
	/tmp/tigerengine_user_model_test

tigerengine-snapshot-io:
	@mkdir -p .tmp/native-tests
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -O2 $(ORT_DEFINES) $(ORT_INCLUDES) \
		tests/tigerengine_snapshot_io_test.cc tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) \
		-o .tmp/native-tests/tigerengine_snapshot_io_test
	.tmp/native-tests/tigerengine_snapshot_io_test

# 读音先验引擎测试：第 5 列（读音条件简频）压制多音字罕用读音拼字
# （mohuz→万虎）；模型缺失或旧 4 列码表时自动跳过。
tigerengine-reading-prior:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_reading_prior_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_reading_prior_test
	/tmp/tigerengine_reading_prior_test

tigerengine-context:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_context_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_context_test
	/tmp/tigerengine_context_test

# 魔虎语义进程内 C2 scorer 测试：真实 ONNX 模型上的方向性/边界/释放；
# MOHU_SEMANTIC_MODEL/MOHU_SEMANTIC_VOCAB 未设置时自动跳过。
tigerengine-semantic:
	@if [ -n "$${MOHU_SEMANTIC_MODEL:-}" ] && [ -n "$${MOHU_SEMANTIC_VOCAB:-}" ]; then \
		clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_semantic_test.cc \
			tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) \
			-o /tmp/tigerengine_semantic_test; \
		/tmp/tigerengine_semantic_test; \
	else \
		echo "tigerengine semantic tests skipped (MOHU_SEMANTIC_MODEL/MOHU_SEMANTIC_VOCAB not set)"; \
	fi
# Cross-platform ownership seam: borrowed container views must never release
# the primary mapping. The test-only ABI is compiled into this binary only.
tigerengine-mapping:
	@mkdir -p .tmp/native-tests
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -O2 $(ORT_DEFINES) -DTIGERENGINE_MAPPING_TEST \
		$(ORT_INCLUDES) tests/tigerengine_mapping_ownership_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) -o .tmp/native-tests/tigerengine_mapping_ownership_test
	.tmp/native-tests/tigerengine_mapping_ownership_test

tigerengine-mobile:
	@mkdir -p .tmp/native-tests
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -O2 $(ORT_DEFINES) $(ORT_INCLUDES) \
		tests/tigerengine_mobile_test.cc tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) \
		-o .tmp/native-tests/tigerengine_mobile_test
	python tests/tigerengine_mobile_cases.py .tmp/native-tests/tigerengine_mobile_test

tigerengine-windows-memory:
	@test "$(OS)" = "Windows_NT" || (echo "tigerengine-windows-memory requires Windows" >&2; exit 2)
	@test -n "$(TIGER_ENGINE_DLL)" -a -n "$(TIGER_NGRAM)" -a -n "$(TIGER_LEXICON)" || \
		(echo "set TIGER_ENGINE_DLL, TIGER_NGRAM, and TIGER_LEXICON" >&2; exit 2)
	@mkdir -p "$(dir $(TIGER_MEMORY_OUTPUT))"
	python tests/tigerengine_windows_mapping_test.py > "$(TIGER_MEMORY_OUTPUT)"

# 词级上下文候选评分引擎测试：load_word_scorer/context_word_scores 的
# 可用性语义、方向性、OOV、确定性与 MHCTN01 容器词层等价；模型缺失
# （未安装或未设 TIGER_NGRAM/TIGER_WORD_NGRAM）时自动跳过。
tigerengine-word-score:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_word_score_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_word_score_test
	/tmp/tigerengine_word_score_test

# 词边先验：静态多字词句中内部边 + 有界加分（0=旧行为）。真实 V5 模型
# 上的翻案/可逆/整段查询不变性；模型缺失（未安装或未设 TIGER_NGRAM）时
# 自动跳过。
tigerengine-word-edge:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_word_edge_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_word_edge_test
	/tmp/tigerengine_word_edge_test

# 词证据分歧门：top1 接不成词而 top2 接词典词 → 开门标志。真实模型 +
# 码表上的分类断言；资源缺失时自动跳过。
tigerengine-word-gate:
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tests/tigerengine_word_gate_test.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_word_gate_test
	/tmp/tigerengine_word_gate_test

# Decode latency benchmark; pass the installed model explicitly, e.g.
#   make tigerengine-bench TIGER_NGRAM=~/Library/Rime/mohu-sentence-ngram-v5.bin
tigerengine-bench:
	@test -n "$(TIGER_NGRAM)" || (echo "Error: set TIGER_NGRAM to mohu-sentence-ngram-v5.bin" >&2; exit 2)
	clang++ -std=c++17 -O2 $(ORT_INCLUDES) tiger_sentence_native/bench_decode.cc \
		tiger_sentence_native/tigerengine.cc $(ORT_TEST_LIBS) $(TIGER_EXTRA_LDFLAGS) -o /tmp/tigerengine_bench
	/tmp/tigerengine_bench "$(TIGER_NGRAM)" tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt \
		$(TIGER_BENCH_ARGS)


dist-zrm: quick mohu_lexicons tigerengine-native
	uv run tools/build_flat_dist.py zrm "$(ZRM_DESTDIR)" $(WINDOWS_RUNTIME_ARG)

dist-flypy: quick mohu_lexicons tigerengine-native
	uv run tools/build_flat_dist.py flypy "$(FLYPY_DESTDIR)" $(WINDOWS_RUNTIME_ARG)

# 手机精简包（Trime/Hamster）：无模型、无 native 二进制，模型由用户自行
# 导入 mohu-sentence-ngram-v5.bin。同时产出目录与发行 zip。
dist-mobile-zrm: quick mohu_lexicons
	uv run tools/build_mobile_dist.py zrm "$(MOBILE_ZRM_DESTDIR)" --zip rime-mohu-mobile-zrm-lite.zip

dist-mobile-flypy: quick mohu_lexicons
	uv run tools/build_mobile_dist.py flypy "$(MOBILE_FLYPY_DESTDIR)" --zip rime-mohu-mobile-flypy-lite.zip

model-dist:
	@test -f "$(TIGER_NGRAM)" || (echo "Error: set TIGER_NGRAM to mohu-sentence-ngram-v5.bin" >&2; exit 1)
	rm -rf model-dist
	mkdir -p model-dist/mohu/model
	install -m 0644 "$(TIGER_NGRAM)" model-dist/mohu/model/mohu-sentence-ngram-v5.bin

test: dist-zrm dist-flypy mohu_lexicons
	$(MAKE) tigerengine-safety
	$(MAKE) tigerengine-lua-safety
	$(MAKE) tigerengine-snapshot-io
	$(MAKE) tigerengine-user-model
	$(MAKE) tigerengine-reading-prior
	$(MAKE) tigerengine-word-edge
	$(MAKE) tigerengine-word-gate
	$(MAKE) tigerengine-context
	$(MAKE) tigerengine-semantic
	uv run python -m unittest tests.test_neural_toggle_schema -v
	$(MAKE) tigerengine-mobile
	$(MAKE) tigerengine-mapping
	$(MAKE) tigerengine-word-score
	uv run tools/import_classics.py check
	uv run python -m unittest tests.test_classics_import -v
	uv run python -m unittest tests.test_tiger_aux -v
	uv run python -m unittest tests.test_qwen_semantic_rerank -v
	uv run --with torch python -m unittest tests.test_semantic_pipeline_model -v
	uv run python -m unittest tests.test_tiger_lexicon_fly -v
	uv run python -m unittest tests.test_reading_coverage -v
	uv run python -m unittest tests.test_mohu_lexicons -v
	uv run python -m unittest tests.test_flat_distribution -v
	uv run python -m unittest tests.test_mobile_distribution -v
	uv run python -m unittest tests.test_collect_windows_runtime -v
	uv run python -m unittest tests.test_split_release_workflow -v
	uv run python -m unittest tests.test_flypy_assets -v
	uv run python -m unittest tests.test_mohu_migration -v
	uv run python -m unittest tests.test_tiger_symbol_workflow -v
	uv run python -m unittest tests.test_merge_emoji -v
	bash tests/rime_sync_conf_test.sh
	lua tests/mohu_candidate_override_test.lua
	lua tests/mohu_candidate_weight_reset_test.lua
	lua tests/mohu_pin_store_test.lua
	lua tests/option_sync_test.lua
	lua tests/mohu_tab_nav_test.lua
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
	lua tests/mohu_sentence_visibility_filter_test.lua
	lua tests/mohu_semantic_meta_test.lua
	lua tests/mohu_semantic_producer_test.lua
	lua tests/mohu_semantic_gate_filter_test.lua
	lua tests/mohu_freestyle_config_test.lua
	lua tests/mohu_contextual_translator_test.lua
	lua tests/mohu_charset_filter_test.lua
	lua tests/mohu_hint_filter_runtime_test.lua
	lua tests/mohu_express_tiger_test.lua
	lua tests/mohu_pin_test.lua
	lua tests/mohu_symbol_commands_test.lua
	$(MOHU_LUA_BIN) tests/mohu_candidate_override_test.lua
	$(MOHU_LUA_BIN) tests/mohu_candidate_weight_reset_test.lua
	$(MOHU_LUA_BIN) tests/mohu_pin_store_test.lua
	$(MOHU_LUA_BIN) tests/option_sync_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tab_nav_test.lua
	$(MOHU_LUA_BIN) tests/mohu_candidate_manager_test.lua
	$(MOHU_LUA_BIN) tests/mohu_candidate_manager_config_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_sentence_native_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_log_compat_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_user_model_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_context_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_two_char_test.lua
	$(MOHU_LUA_BIN) tests/mohu_personal_lexicon_test.lua
	$(MOHU_LUA_BIN) tests/mohu_path_test.lua
	$(MOHU_LUA_BIN) tests/mohu_model_version_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_no_early_commit_test.lua
	$(MOHU_LUA_BIN) tests/mohu_tiger_selected_segment_test.lua
	$(MOHU_LUA_BIN) tests/mohu_reorder_filter_lexicon_test.lua
	$(MOHU_LUA_BIN) tests/mohu_word_order_filter_test.lua
	$(MOHU_LUA_BIN) tests/mohu_freestyle_config_test.lua
	$(MOHU_LUA_BIN) tests/mohu_contextual_translator_test.lua
	$(MOHU_LUA_BIN) tests/mohu_charset_filter_test.lua
	$(MOHU_LUA_BIN) tests/mohu_hint_filter_runtime_test.lua
	$(MOHU_LUA_BIN) tests/mohu_express_tiger_test.lua
	$(MOHU_LUA_BIN) tests/mohu_pin_test.lua
	$(MOHU_LUA_BIN) tests/mohu_symbol_commands_test.lua
	cp -a /usr/share/opencc/* dist/opencc       2>/dev/null || true
	cp -a /usr/local/share/opencc/* dist/opencc 2>/dev/null || true
	cp -a /opt/homebrew/share/opencc/* dist/opencc 2>/dev/null || true
	test -f dist/opencc/t2tw.json || (echo "Error: cannot find shared opencc data!" && exit 1)

	mira -C /tmp/mira-cache tests/mohu_zrm.test.yaml
	mira -C /tmp/mira-cache tests/mohu_semantic_gate.test.yaml
	mira -C /tmp/mira-cache tests/mohu_flypy.test.yaml
	mira -C /tmp/mira-cache tests/tiger.test.yaml
	mira -C /tmp/mira-cache tests/mohu_tiger_priority.test.yaml
	mira -C /tmp/mira-cache tests/mohu_candidate_override.test.yaml
	mira -C /tmp/mira-cache tests/mohu.hint.test.yaml
	mira -C /tmp/mira-cache tests/mohu.ijrq.test.yaml
	rm -rf /tmp/mira-cache

.PHONY: quick all dict mohu_lexicons tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen emoji update-compact-dicts sync-essay dazhu opencc mdict model-dist tigerengine-native tigerengine-safety tigerengine-lua-safety tigerengine-user-model tigerengine-context tigerengine-semantic tigerengine-word-score tigerengine-word-edge tigerengine-word-gate tigerengine-bench dist-zrm dist-flypy dist-mobile-zrm dist-mobile-flypy test lint-python
.PHONY: quick all dict mohu_lexicons tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen emoji update-compact-dicts sync-essay dazhu opencc mdict model-dist tigerengine-native tigerengine-safety tigerengine-lua-safety tigerengine-snapshot-io tigerengine-user-model tigerengine-context tigerengine-word-score tigerengine-bench tigerengine-mapping tigerengine-mobile tigerengine-windows-memory dist-zrm dist-flypy dist-mobile-zrm dist-mobile-flypy flykey-check test lint-python
