#!/bin/bash
# rime_sync.sh 单元测试：配置解析、开关判定、状态文件、单次运行逻辑、plist 生成
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0

check() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        PASS=$(( PASS + 1 ))
        echo "ok - $label"
    else
        FAIL=$(( FAIL + 1 ))
        echo "not ok - $label: expected [$expected] got [$actual]"
    fi
}

check_fail() {
    local label="$1"
    PASS=$(( PASS + 1 ))
    echo "ok - $label"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# shellcheck source=../Rime同步助手/rime_sync.sh
source 'Rime同步助手/rime_sync.sh'

# --- truthy 判定 ---
truthy "开"   && check "truthy 开" 0 0 || check "truthy 开" 0 1
truthy "on"  && check "truthy on" 0 0 || check "truthy on" 0 1
truthy "TRUE" && check "truthy TRUE" 0 0 || check "truthy TRUE" 0 1
truthy "关"  && check "truthy 关 not" 0 1 || check "truthy 关 not" 0 0
truthy "off" && check "truthy off not" 0 1 || check "truthy off not" 0 0
truthy ""    && check "truthy empty not" 0 1 || check "truthy empty not" 0 0

# --- 配置解析：默认值 ---
rm -f "$TMP/conf"
rime_sync_parse_conf "$TMP/conf"
check "default idle" "1" "$CONF_IDLE"
check "default startup" "1" "$CONF_STARTUP"
check "default minutes" "10" "$CONF_IDLE_MINUTES"
check "default interval" "5" "$CONF_INTERVAL_MINUTES"

# --- 配置解析：正常编辑 ---
cat > "$TMP/conf" <<'EOF'
# 注释行
空闲同步: 关
空闲分钟数: 15
开机同步: off
检查间隔分钟数: 30
EOF
rime_sync_parse_conf "$TMP/conf"
check "idle off" "0" "$CONF_IDLE"
check "minutes 15" "15" "$CONF_IDLE_MINUTES"
check "startup off" "0" "$CONF_STARTUP"
check "interval 30" "30" "$CONF_INTERVAL_MINUTES"

# --- 配置解析：非法间隔被忽略（保持默认/当前值） ---
CONF_INTERVAL_MINUTES=5
cat > "$TMP/conf" <<'EOF'
检查间隔分钟数: 0
检查间隔分钟数: abc
EOF
rime_sync_parse_conf "$TMP/conf"
check "invalid interval ignored" "5" "$CONF_INTERVAL_MINUTES"

# --- 配置解析：非法分钟数被忽略，后面的合法值生效；行内注释、多余空格 ---
cat > "$TMP/conf" <<'EOF'
空闲分钟数: abc
空闲分钟数: 2000
空闲分钟数: 7   # 行内注释
空闲同步:    开
EOF
rime_sync_parse_conf "$TMP/conf"
check "invalid minutes ignored, valid 7 wins" "7" "$CONF_IDLE_MINUTES"
check "idle on with spaces" "1" "$CONF_IDLE"

# --- state_get：键值读取 ---
printf '已武装: 否\n开机时间: 1755573742\n' > "$TMP/state"
check "state_get first key" "否" "$(state_get "$TMP/state" 已武装)"
check "state_get second key" "1755573742" "$(state_get "$TMP/state" 开机时间)"
check "state_get missing key" "" "$(state_get "$TMP/state" 不存在)"
check "state_get missing file" "" "$(state_get "$TMP/nope" 已武装)"

# --- 空闲时间读取：注入假 ioreg 输出 ---
printf '"HIDIdleTime" = 2500000000\n' > "$TMP/ioreg.txt"
export RIME_SYNC_IOREG_CMD="cat $TMP/ioreg.txt"
check "idle nanos parsed" "2500000000" "$(rime_sync_idle_nanos)"

printf 'no match here\n' > "$TMP/ioreg.txt"
check "idle nanos missing" "" "$(rime_sync_idle_nanos)"

# --- 开机时间读取：注入假 sysctl 输出 ---
printf '{ sec = 1755573742, usec = 0 } Thu Aug 20 09:00:00 2026\n' > "$TMP/boottime.txt"
export RIME_SYNC_BOOTTIME_CMD="cat $TMP/boottime.txt"
check "boottime parsed" "1755573742" "$(rime_sync_boottime)"

# --- 同步目录解析：installation.yaml 的 sync_dir 优先 ---
mkdir -p "$TMP/root2"
printf 'sync_dir: "/tmp/fake-sync-dir"\n' > "$TMP/root2/installation.yaml"
check "sync_dir parsed from installation.yaml" "/tmp/fake-sync-dir" "$(RIME_ROOT="$TMP/root2" RIME_SYNC_SYNC_DIR="" bash -c "$(declare -f rime_sync_sync_dir); rime_sync_sync_dir")"
rm "$TMP/root2/installation.yaml"
check "sync_dir fallback without yaml" "$TMP/root2/sync" "$(RIME_ROOT="$TMP/root2" RIME_SYNC_SYNC_DIR="" bash -c "$(declare -f rime_sync_sync_dir); rime_sync_sync_dir")"
check "sync_dir override wins" "/tmp/override" "$(RIME_SYNC_SYNC_DIR="/tmp/override" bash -c "$(declare -f rime_sync_sync_dir); rime_sync_sync_dir")"

# --- 门卫：data_newest 取本地用户库 + 同步目录的最新修改时间 ---
mkdir -p "$TMP/root3/a.userdb" "$TMP/sync3/other"
printf 'x\n' > "$TMP/root3/a.userdb/000001.log"
printf 'x\n' > "$TMP/sync3/other/snapshot.txt"
touch -t 202001010000 "$TMP/root3/a.userdb/000001.log"
touch -t 202101010000 "$TMP/sync3/other/snapshot.txt"
NEWEST3="$(RIME_ROOT="$TMP/root3" RIME_SYNC_SYNC_DIR="$TMP/sync3" bash -c "$(declare -f rime_sync_sync_dir rime_sync_data_newest); rime_sync_data_newest")"
check "data_newest picks newest across trees" "$(stat -f %m "$TMP/sync3/other/snapshot.txt")" "$NEWEST3"

# --- 单次运行逻辑：封闭环境 + 假的同步函数记录触发原因 ---
# bash 3.2 + set -u 下空数组不能直接展开，用 helper 兼容
export RIME_SYNC_SETTLE_SECONDS=0
RIME_ROOT="$TMP/root"
RIME_SYNC_SYNC_DIR="$TMP/sync"
export RIME_ROOT RIME_SYNC_SYNC_DIR
mkdir -p "$TMP/root/x.userdb" "$TMP/sync/other"
CONF_FILE="$TMP/conf"
CURSOR_FILE="$TMP/cursor"
STATE_FILE="$TMP/state.json"
REASONS=()
reasons_joined() {
    if (( ${#REASONS[@]} == 0 )); then printf ''
    else printf '%s' "${REASONS[*]}"
    fi
}
rime_sync_sync() { REASONS+=("$1"); }

cat > "$CONF_FILE" <<'EOF'
空闲同步: 开
空闲分钟数: 10
开机同步: 开
EOF

# 场景 1：开机后第一次运行（游标不存在），空闲很短 → 只触发 startup
printf '{ sec = 1111, usec = 0 }\n' > "$TMP/boottime.txt"
export RIME_SYNC_BOOTTIME_CMD="cat $TMP/boottime.txt"
printf '"HIDIdleTime" = 100000000000\n' > "$TMP/ioreg.txt"
export RIME_SYNC_IOREG_CMD="cat $TMP/ioreg.txt"
rime_sync_run
check "first run syncs startup only" "startup" "$(reasons_joined)"
check "cursor boot saved" "1111" "$(state_get "$CURSOR_FILE" 开机时间)"
check "cursor armed after boot" "是" "$(state_get "$CURSOR_FILE" 已武装)"
check "cursor last sync recorded" "yes" "$([[ -n $(state_get "$CURSOR_FILE" 上次同步) ]] && echo yes)"

# 场景 2：出现新数据（本地用户库更新），空闲很长 → 触发一次 idle
touch -t 209901010000 "$TMP/root/x.userdb/000002.log"
printf '"HIDIdleTime" = 700000000000\n' > "$TMP/ioreg.txt"
REASONS=()
rime_sync_run
check "new data + long idle triggers idle once" "idle" "$(reasons_joined)"
check "cursor disarmed" "否" "$(state_get "$CURSOR_FILE" 已武装)"

# 场景 3：持续空闲（同周期再次拉起）→ 不重复同步
REASONS=()
rime_sync_run
check "still idle no retrigger" "" "$(reasons_joined)"

# 场景 4：恢复操作（空闲变短）→ 重新武装，随后再次长空闲 → 再同步一次
printf '"HIDIdleTime" = 100000000000\n' > "$TMP/ioreg.txt"
rime_sync_run
check "activity rearms" "是" "$(state_get "$CURSOR_FILE" 已武装)"
printf '"HIDIdleTime" = 700000000000\n' > "$TMP/ioreg.txt"
REASONS=()
rime_sync_run
check "triggers again after rearm" "idle" "$(reasons_joined)"

# 场景 5：重启后（开机时间变化），开机同步关闭 → 不触发 startup，仅更新游标
cat > "$CONF_FILE" <<'EOF'
空闲同步: 关
开机同步: 关
EOF
printf '{ sec = 2222, usec = 0 }\n' > "$TMP/boottime.txt"
REASONS=()
rime_sync_run
check "no sync when both off on new boot" "" "$(reasons_joined)"
check "cursor boot updated" "2222" "$(state_get "$CURSOR_FILE" 开机时间)"

# 场景 6（门卫）：没有任何新数据 → 空闲达标也不执行，保持武装
cat > "$CONF_FILE" <<'EOF'
空闲同步: 开
空闲分钟数: 10
开机同步: 开
EOF
rm -f "$TMP/root/x.userdb/000002.log"
printf '已武装: 是\n开机时间: 2222\n上次同步: %s\n' "$(date +%s)" > "$CURSOR_FILE"
REASONS=()
rime_sync_run
check "guard: no changes, no sync" "" "$(reasons_joined)"
check "guard: stays armed" "是" "$(state_get "$CURSOR_FILE" 已武装)"

# 场景 7（门卫）：本地用户库出现新数据 → 恢复执行，并更新上次同步时间
printf '已武装: 是\n开机时间: 2222\n上次同步: %s\n' "$(date +%s -v-10M)" > "$CURSOR_FILE"
touch "$TMP/root/x.userdb/000003.log"
REASONS=()
rime_sync_run
check "guard: new local data triggers idle sync" "idle" "$(reasons_joined)"
check "guard: last sync updated" "yes" "$([[ -n $(state_get "$CURSOR_FILE" 上次同步) ]] && echo yes)"

# 场景 8（门卫）：同步目录出现新快照（云盘带来其他设备数据）→ 也触发
printf '已武装: 是\n开机时间: 2222\n上次同步: %s\n' "$(date +%s -v-10M)" > "$CURSOR_FILE"
touch "$TMP/sync/other/new-snapshot.userdb.txt"
REASONS=()
rime_sync_run
check "guard: new remote snapshot triggers idle sync" "idle" "$(reasons_joined)"

# --- plist 内容 ---
PLIST_CONTENT="$(rime_sync_plist_content '/some/path/rime_sync.sh' 5)"
case "$PLIST_CONTENT" in
    *"<string>cn.zrmfans.rime-mohu.sync</string>"*) check_fail "plist contains label" ;;
    *) check "plist contains label" "found" "missing" ;;
esac
case "$PLIST_CONTENT" in
    *"/bin/bash"*) check_fail "plist uses bash" ;;
    *) check "plist uses bash" "found" "missing" ;;
esac
case "$PLIST_CONTENT" in
    *"<key>RunAtLoad</key>"*) check_fail "plist RunAtLoad" ;;
    *) check "plist RunAtLoad" "found" "missing" ;;
esac
case "$PLIST_CONTENT" in
    *"<key>StartInterval</key>"*) check_fail "plist StartInterval" ;;
    *) check "plist StartInterval" "found" "missing" ;;
esac
case "$PLIST_CONTENT" in
    *"<integer>300</integer>"*) check_fail "plist interval 5min = 300s" ;;
    *) check "plist interval 5min = 300s" "found" "missing" ;;
esac
case "$PLIST_CONTENT" in
    *KeepAlive*) check "plist has no KeepAlive" "absent" "present" ;;
    *) check_fail "plist has no KeepAlive" ;;
esac

# --- plist 间隔跟随配置 ---
PLIST_15="$(rime_sync_plist_content '/some/path/rime_sync.sh' 15)"
case "$PLIST_15" in
    *"<integer>900</integer>"*) check_fail "plist interval 15min = 900s" ;;
    *) check "plist interval 15min = 900s" "found" "missing" ;;
esac

echo ""
echo "passed: $PASS, failed: $FAIL"
[[ $FAIL -eq 0 ]]
