#!/bin/bash

STRICT="$1"

echo Strict about errors? $STRICT

compact_dicts=(
    "mohu_zrm.base.dict.yaml"
    "mohu_zrm.tencent.dict.yaml"
    "mohu_zrm.moe.dict.yaml"
    "mohu_zrm.computer.dict.yaml"
    "mohu_zrm.words.dict.yaml"
)

UPDATE_LINE_RE=$'^.+\t'

set -x

update_compact_dict() {
    DICT_FILE="$1"
    INPUT_FILE="${DICT_FILE%.dict.yaml}.in"
    OUTPUT_FILE="${DICT_FILE%.dict.yaml}.out"

    cp $DICT_FILE $INPUT_FILE
    uv run tools/schemagen.py update-compact-dict --rime-dict="$INPUT_FILE" > "$OUTPUT_FILE"

    if grep '^# BAD' "$OUTPUT_FILE"
    then
        echo '!!! BAD DICT !!!'

        # Still allow grep to show bad entries.
        if [ x$STRICT = x"yes" ]; then
            rm -f $INPUT_FILE
            return 1
        else
            mv $OUTPUT_FILE $DICT_FILE
            rm -f $INPUT_FILE $OUTPUT_FILE
            return 0
        fi
    else
        mv $OUTPUT_FILE $DICT_FILE
        rm -f $INPUT_FILE $OUTPUT_FILE
        return 0
    fi
}

job() {
    local dict="$1"
    echo "* Updating $dict"
    if update_compact_dict "$dict"; then
        echo "  $dict success"
    else
        echo "  $dict ERROR!"
    fi
}

for dict in "${compact_dicts[@]}"; do
    job "$dict" &
done

wait
