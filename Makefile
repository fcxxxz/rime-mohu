DESTDIR ?= $(abspath ./dist)
ZRM_DESTDIR ?= $(abspath ./dist-zrm)
FLYPY_DESTDIR ?= $(abspath ./dist-flypy)
LLM_DESTDIR ?= $(abspath ./dist-llm)
MOHU_LLM_ZRM_DESTDIR ?= $(abspath ./dist-mohu-llm-zrm)
MOHU_LLM_FLYPY_DESTDIR ?= $(abspath ./dist-mohu-llm-flypy)
TIGER_NGRAM ?= tiger_sentence_native/sentence-ngram-mobile.bin
# Optional Windows engine (libtigerengine.dll); staged by CI from the
# windows-builder job. Local macOS-only builds may leave it unset.
TIGER_ENGINE_DLL ?=

quick: classics tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen opencc
	uv run tools/build_flypy_assets.py
dict: classics tiger_aux chars fixed_tiger update-compact-dicts
	uv run tools/build_flypy_assets.py
all: quick dict

mohu_llm_lexicons: tiger_sentence_native/mohu_tiger.lexicon.txt mohu_zrm.chars.dict.yaml tools/build_mohu_llm_lexicons.py tools/flypyify.py tools/zrmify.py
	uv run tools/build_mohu_llm_lexicons.py
	test -f tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt
	test -f tiger_sentence_native/data/flypy/mohu_llm_flypy.lexicon.txt

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
	rm -rf dist-mohu-llm-zrm dist-mohu-llm-flypy
	rm -f $(chars_output)
	rm -f $(tiger_rank_output)
	rm -f dazhu*.txt
	make -C opencc clean

# Installs the traditional version into DESTDIR
dist: quick
	if [ "$(abspath $(DESTDIR))" = "$(abspath ./dist)" ]; then rm -rf "$(DESTDIR)"; fi
	mkdir -p $(DESTDIR)
	# A caller may reuse an existing staging directory; remove only known LLM
	# outputs so the standard package cannot inherit optional runtime files.
	rm -f "$(DESTDIR)"/mohu_llm_*.schema.yaml "$(DESTDIR)"/install_mohu_llm_*.command
	rm -rf "$(DESTDIR)/mohu_llm"
	cp -a README*.md LICENSE etc $(DESTDIR)
	for path in mohu*; do \
		case "$$path" in mohu_llm_*.schema.yaml) ;; *) cp -a "$$path" "$(DESTDIR)" ;; esac; \
	done
	cp -a key_bindings.yaml punctuation.yaml symbols.yaml $(DESTDIR)
	# The standard bundle has no native runtime; keep its schema list free of
	# optional LLM entries, which are registered by the complete package installer.
	sed -E '/^[[:space:]]*-[[:space:]]*schema:[[:space:]]*mohu_llm_[^[:space:]]+[[:space:]]*$$/d' default.yaml > "$(DESTDIR)/default.yaml"
	cp -a recipe.yaml recipes $(DESTDIR)
	cp -a squirrel.yaml $(DESTDIR)
	cp -a tiger.*.yaml $(DESTDIR)
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

# Decode latency benchmark; pass the installed model explicitly, e.g.
#   make tigerengine-bench TIGER_NGRAM=~/Library/Rime/mohu_llm/data/sentence-ngram-mobile.bin
tigerengine-bench:
	@test -n "$(TIGER_NGRAM)" || (echo "Error: set TIGER_NGRAM to sentence-ngram-mobile.bin" >&2; exit 2)
	clang++ -std=c++17 -O2 -I tiger_sentence_native tiger_sentence_native/bench_decode.cc \
		tiger_sentence_native/tigerengine.cc -o /tmp/tigerengine_bench
	/tmp/tigerengine_bench "$(TIGER_NGRAM)" tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt \
		$(TIGER_BENCH_ARGS)

dist-zrm: quick
	uv run tools/build_split_dist.py zrm "$(ZRM_DESTDIR)"

dist-flypy: quick
	uv run tools/build_split_dist.py flypy "$(FLYPY_DESTDIR)"

mohu-llm-zrm-dist: tigerengine-native mohu_llm_lexicons
	@test -f "$(TIGER_NGRAM)" || (echo "Error: TIGER_NGRAM not found at $(TIGER_NGRAM); set TIGER_NGRAM=/path/to/sentence-ngram-mobile.bin" >&2; exit 1)
	zrm_dest="$(abspath $(MOHU_LLM_ZRM_DESTDIR))"; repo_root="$(abspath .)"; zrm_base="$${zrm_dest##*/}"; \
	case "$$zrm_dest" in /|"$$HOME"|"$$repo_root") echo "unsafe zrm destination" >&2; exit 1;; esac; \
	case "$$zrm_base" in tmp|private|Users|home|Library|Rime) echo "unsafe zrm destination" >&2; exit 1;; esac; \
	case "$$repo_root/" in "$$zrm_dest/"*) echo "unsafe zrm destination" >&2; exit 1;; esac
	rm -rf "$(MOHU_LLM_ZRM_DESTDIR)"
	mkdir -p "$(MOHU_LLM_ZRM_DESTDIR)/lua" "$(MOHU_LLM_ZRM_DESTDIR)/runtime" "$(MOHU_LLM_ZRM_DESTDIR)/data/zrm" "$(MOHU_LLM_ZRM_DESTDIR)/models"
	cp -a lua/. "$(MOHU_LLM_ZRM_DESTDIR)/lua/"
	install -m 0644 mohu_llm_zrm.schema.yaml "$(MOHU_LLM_ZRM_DESTDIR)/mohu_llm_zrm.schema.yaml"
	install -m 0755 tiger_sentence_native/install_mohu_llm_zrm.command tiger_sentence_native/install_mohu_llm_scheme.command "$(MOHU_LLM_ZRM_DESTDIR)/"
	install -m 0644 tiger_sentence_native/install_mohu_llm_windows.ps1 "$(MOHU_LLM_ZRM_DESTDIR)/"
	install -m 0644 tiger_sentence_native/mohu_llm_zrm.package.json "$(MOHU_LLM_ZRM_DESTDIR)/package.json"
	install -m 0644 tiger_sentence_native/mohu_llm_runtime.lua tiger_sentence_native/mohu_sentence.lua tiger_sentence_native/mohu_tiger_sentence.lua lua/mohu_personal_lexicon.lua tiger_sentence_native/mohu_tiger_reranker.lua tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_model_catalog.lua tiger_sentence_native/mohu_tiger_model_menu.lua "$(MOHU_LLM_ZRM_DESTDIR)/lua/"
	install -m 0755 tiger_sentence_native/run_qwen35_scorer.command tiger_sentence_native/install_qwen35_launch_agent.command tiger_sentence_native/switch_qwen_model.command "$(MOHU_LLM_ZRM_DESTDIR)/runtime/"
	install -m 0644 tiger_sentence_native/libtigerengine.dylib tiger_sentence_native/qwen35_scorer.py tiger_sentence_native/scorer_models.zsh tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_reranker_profile_qwen3_06b.lua "$(MOHU_LLM_ZRM_DESTDIR)/runtime/"
	if command -v codesign >/dev/null 2>&1; then codesign --verify --strict "$(MOHU_LLM_ZRM_DESTDIR)/runtime/libtigerengine.dylib"; fi
	if [ -n "$(TIGER_ENGINE_DLL)" ]; then \
		install -m 0644 "$(TIGER_ENGINE_DLL)" "$(MOHU_LLM_ZRM_DESTDIR)/runtime/libtigerengine.dll"; \
		if [ -f "$(dir $(TIGER_ENGINE_DLL))lua54.dll" ]; then install -m 0644 "$(dir $(TIGER_ENGINE_DLL))lua54.dll" "$(MOHU_LLM_ZRM_DESTDIR)/runtime/lua54.dll"; fi; \
	fi
	install -m 0644 "$(TIGER_NGRAM)" "$(MOHU_LLM_ZRM_DESTDIR)/data/sentence-ngram-mobile.bin"
	install -m 0644 tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt "$(MOHU_LLM_ZRM_DESTDIR)/data/zrm/"
	install -m 0644 tiger_sentence_native/models/*.manifest tiger_sentence_native/models/README.md "$(MOHU_LLM_ZRM_DESTDIR)/models/"
	install -m 0644 tiger_sentence_native/README.md "$(MOHU_LLM_ZRM_DESTDIR)/README.md"
	test -f "$(MOHU_LLM_ZRM_DESTDIR)/mohu_llm_zrm.schema.yaml"
	test ! -e "$(MOHU_LLM_ZRM_DESTDIR)/mohu_llm_flypy.schema.yaml"
	test -f "$(MOHU_LLM_ZRM_DESTDIR)/data/zrm/mohu_llm_zrm.lexicon.txt"
	test ! -e "$(MOHU_LLM_ZRM_DESTDIR)/data/flypy"
	test -x "$(MOHU_LLM_ZRM_DESTDIR)/install_mohu_llm_zrm.command"
	! find "$(MOHU_LLM_ZRM_DESTDIR)" -type f \( -name '*.safetensors' -o -name '*.gguf' \) -print -quit | grep -q .

mohu-llm-flypy-dist: tigerengine-native mohu_llm_lexicons
	@test -f "$(TIGER_NGRAM)" || (echo "Error: TIGER_NGRAM not found at $(TIGER_NGRAM); set TIGER_NGRAM=/path/to/sentence-ngram-mobile.bin" >&2; exit 1)
	flypy_dest="$(abspath $(MOHU_LLM_FLYPY_DESTDIR))"; repo_root="$(abspath .)"; flypy_base="$${flypy_dest##*/}"; \
	case "$$flypy_dest" in /|"$$HOME"|"$$repo_root") echo "unsafe flypy destination" >&2; exit 1;; esac; \
	case "$$flypy_base" in tmp|private|Users|home|Library|Rime) echo "unsafe flypy destination" >&2; exit 1;; esac; \
	case "$$repo_root/" in "$$flypy_dest/"*) echo "unsafe flypy destination" >&2; exit 1;; esac
	rm -rf "$(MOHU_LLM_FLYPY_DESTDIR)"
	mkdir -p "$(MOHU_LLM_FLYPY_DESTDIR)/lua" "$(MOHU_LLM_FLYPY_DESTDIR)/runtime" "$(MOHU_LLM_FLYPY_DESTDIR)/data/flypy" "$(MOHU_LLM_FLYPY_DESTDIR)/models"
	cp -a lua/. "$(MOHU_LLM_FLYPY_DESTDIR)/lua/"
	install -m 0644 mohu_llm_flypy.schema.yaml "$(MOHU_LLM_FLYPY_DESTDIR)/mohu_llm_flypy.schema.yaml"
	install -m 0755 tiger_sentence_native/install_mohu_llm_flypy.command tiger_sentence_native/install_mohu_llm_scheme.command "$(MOHU_LLM_FLYPY_DESTDIR)/"
	install -m 0644 tiger_sentence_native/install_mohu_llm_windows.ps1 "$(MOHU_LLM_FLYPY_DESTDIR)/"
	install -m 0644 tiger_sentence_native/mohu_llm_flypy.package.json "$(MOHU_LLM_FLYPY_DESTDIR)/package.json"
	install -m 0644 tiger_sentence_native/mohu_llm_runtime.lua tiger_sentence_native/mohu_sentence.lua tiger_sentence_native/mohu_tiger_sentence.lua lua/mohu_personal_lexicon.lua tiger_sentence_native/mohu_tiger_reranker.lua tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_model_catalog.lua tiger_sentence_native/mohu_tiger_model_menu.lua "$(MOHU_LLM_FLYPY_DESTDIR)/lua/"
	install -m 0755 tiger_sentence_native/run_qwen35_scorer.command tiger_sentence_native/install_qwen35_launch_agent.command tiger_sentence_native/switch_qwen_model.command "$(MOHU_LLM_FLYPY_DESTDIR)/runtime/"
	install -m 0644 tiger_sentence_native/libtigerengine.dylib tiger_sentence_native/qwen35_scorer.py tiger_sentence_native/scorer_models.zsh tiger_sentence_native/mohu_tiger_reranker_profile.lua tiger_sentence_native/mohu_tiger_reranker_profile_qwen3_06b.lua "$(MOHU_LLM_FLYPY_DESTDIR)/runtime/"
	if command -v codesign >/dev/null 2>&1; then codesign --verify --strict "$(MOHU_LLM_FLYPY_DESTDIR)/runtime/libtigerengine.dylib"; fi
	if [ -n "$(TIGER_ENGINE_DLL)" ]; then \
		install -m 0644 "$(TIGER_ENGINE_DLL)" "$(MOHU_LLM_FLYPY_DESTDIR)/runtime/libtigerengine.dll"; \
		if [ -f "$(dir $(TIGER_ENGINE_DLL))lua54.dll" ]; then install -m 0644 "$(dir $(TIGER_ENGINE_DLL))lua54.dll" "$(MOHU_LLM_FLYPY_DESTDIR)/runtime/lua54.dll"; fi; \
	fi
	install -m 0644 "$(TIGER_NGRAM)" "$(MOHU_LLM_FLYPY_DESTDIR)/data/sentence-ngram-mobile.bin"
	install -m 0644 tiger_sentence_native/data/flypy/mohu_llm_flypy.lexicon.txt "$(MOHU_LLM_FLYPY_DESTDIR)/data/flypy/"
	install -m 0644 tiger_sentence_native/models/*.manifest tiger_sentence_native/models/README.md "$(MOHU_LLM_FLYPY_DESTDIR)/models/"
	install -m 0644 tiger_sentence_native/README.md "$(MOHU_LLM_FLYPY_DESTDIR)/README.md"
	test -f "$(MOHU_LLM_FLYPY_DESTDIR)/mohu_llm_flypy.schema.yaml"
	test ! -e "$(MOHU_LLM_FLYPY_DESTDIR)/mohu_llm_zrm.schema.yaml"
	test -f "$(MOHU_LLM_FLYPY_DESTDIR)/data/flypy/mohu_llm_flypy.lexicon.txt"
	test ! -e "$(MOHU_LLM_FLYPY_DESTDIR)/data/zrm"
	test -x "$(MOHU_LLM_FLYPY_DESTDIR)/install_mohu_llm_flypy.command"
	! find "$(MOHU_LLM_FLYPY_DESTDIR)" -type f \( -name '*.safetensors' -o -name '*.gguf' \) -print -quit | grep -q .

test: dist mohu_llm_lexicons
	$(MAKE) tigerengine-safety
	$(MAKE) tigerengine-lua-safety
	uv run tools/import_classics.py check
	uv run python -m unittest tests.test_classics_import -v
	uv run python -m unittest tests.test_tiger_aux -v
	uv run python -m unittest tests.test_mohu_config -v
	uv run python -m unittest tests.test_mohu_tiger_sentence_native -v
	uv run python -m unittest tests.test_tiger_lexicon_fly -v
	uv run python -m unittest tests.test_mohu_llm_lexicons -v
	uv run python -m unittest tests.test_mohu_llm_distribution -v
	uv run python -m unittest tests.test_mohu_llm_installers -v
	uv run python -m unittest tests.test_qwen35_scorer -v
	uv run python -m unittest tests.test_qwen_model_supervisor -v
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
	lua tests/mohu_tiger_log_compat_test.lua
	lua tests/mohu_personal_lexicon_test.lua
	lua tests/mohu_llm_path_test.lua
	lua tests/mohu_llm_schema_split_test.lua
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

.PHONY: quick all dict mohu_llm_lexicons tiger_aux fixed_tiger chars pinyin_reverse zrmdb chaifen emoji update-compact-dicts sync-essay dazhu opencc mdict dist tigerengine-native mohu-llm-zrm-dist mohu-llm-flypy-dist tigerengine-safety tigerengine-lua-safety tigerengine-bench dist-zrm dist-flypy test lint-python
