#!/bin/bash

set -euo pipefail

DIST_DIR="${1:-dist}"

grep -Fq 'caption: 〔方案选单〕' "$DIST_DIR/default.yaml"
grep -Fq 'states: [ 常用字, 全字集 ]' "$DIST_DIR/mohu_zrm.schema.yaml"
grep -Fq 'states: [ 动词, 固词 ]' "$DIST_DIR/mohu_zrm.schema.yaml"

for schema in \
    mohu_zrm \
    mohu_flypy mohu_flypy_fixed mohu_flypy_sentence mohu_flypy_aux; do
    grep -Fq 'states: [ 常用字, 全字集 ]' "$DIST_DIR/$schema.schema.yaml"
    grep -Fq 'reverse_lookup_translator@reverse_tiger' "$DIST_DIR/$schema.schema.yaml"
    grep -Fq 'reverse_lookup_translator@reverse_tiger_backtick' "$DIST_DIR/$schema.schema.yaml"
    grep -Fq 'prefix: "ohm"' "$DIST_DIR/$schema.schema.yaml"
    grep -Fq 'prefix: "`"' "$DIST_DIR/$schema.schema.yaml"
done

if grep -E -q 'std_(t2|s2)|mohu_(english|japanese)|states: \[ (通用|简, 通)' \
    "$DIST_DIR"/*.schema.yaml; then
    echo "Error: distribution contains a removed language or character-standard option" >&2
    exit 1
fi

if find "$DIST_DIR" -maxdepth 1 -name 'moran*' -print -quit | grep -q .; then
    echo "Error: distribution contains legacy Moran assets" >&2
    exit 1
fi

if grep -F -e '方案選單' -e '增廣' -e '動詞' -e '固詞' \
    "$DIST_DIR/default.yaml" "$DIST_DIR"/*.schema.yaml; then
    echo "Error: simplified config contains traditional UI labels" >&2
    exit 1
fi
