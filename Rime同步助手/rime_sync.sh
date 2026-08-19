#!/bin/bash
# rime_sync.sh -- Rime 用户数据自动同步（macOS，无常驻进程）
#
# 形态：launchd 定时任务。登录时运行一次 + 按检查间隔被系统拉起，
#       每次运行检查完就退出，平时系统里没有任何同步进程。
#
# 用法:
#   bash rime_sync.sh install    注册 LaunchAgent（开机 + 定时拉起）并立即运行一次
#   bash rime_sync.sh uninstall  注销 LaunchAgent
#   bash rime_sync.sh run        单次运行（由 launchd 调用）：判断开机/空闲并按需同步
#   bash rime_sync.sh status     查看设置、注册状态和上次同步结果
#
# 开机同步：开机后第一次被拉起时同步一次（靠「同步游标」记录的开机时间判断）。
# 空闲同步：被拉起时读系统空闲时间，达到设定分钟数才同步；持续空闲只同步一次，
#           恢复操作后重新武装。判定粒度是检查间隔。
# 有更新才执行：真正同步前先做门卫检查——本地用户库和同步目录都没有比
#           「上次同步」更新的文件时直接跳过，不调用 Squirrel。
# 没有关机同步：无常驻进程接不到关机信号；未同步的数据不会丢，
#           下次开机同步会补上。
#
# 用户设置在同目录的「同步设置.conf」里编辑，改完保存即生效。
# 例外：「检查间隔分钟数」决定 launchd 拉起频率，改后需重新执行 install（双击 安装.command）。

set -u

LABEL="cn.zrmfans.rime-mohu.sync"
SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/同步设置.conf"
STATE_FILE="${SCRIPT_DIR}/同步状态.json"
CURSOR_FILE="${SCRIPT_DIR}/同步游标.txt"
SQUIRREL="/Library/Input Methods/Squirrel.app/Contents/MacOS/Squirrel"
RIME_ROOT="${RIME_ROOT:-${HOME}/Library/Rime}"

# 默认设置（同步设置.conf 缺失或某项缺失时使用）
CONF_IDLE=1
CONF_STARTUP=1
CONF_IDLE_MINUTES=10
CONF_INTERVAL_MINUTES=5

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

truthy() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        开|on|true|1|yes) return 0 ;;
        *) return 1 ;;
    esac
}

rime_sync_parse_conf() {
    local file="$1" line key val
    local idle=$CONF_IDLE startup=$CONF_STARTUP minutes=$CONF_IDLE_MINUTES interval=$CONF_INTERVAL_MINUTES
    if [[ -f "$file" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line%%#*}"
            line="$(trim "$line")"
            [[ -z "$line" || "$line" != *:* ]] && continue
            key="$(trim "${line%%:*}")"
            val="$(trim "${line#*:}")"
            case "$key" in
                空闲同步)
                    if truthy "$val"; then idle=1; else idle=0; fi ;;
                开机同步)
                    if truthy "$val"; then startup=1; else startup=0; fi ;;
                空闲分钟数)
                    if [[ "$val" =~ ^[0-9]+$ ]] && (( val >= 1 && val <= 1440 )); then
                        minutes="$val"
                    fi ;;
                检查间隔分钟数)
                    if [[ "$val" =~ ^[0-9]+$ ]] && (( val >= 1 && val <= 1440 )); then
                        interval="$val"
                    fi ;;
            esac
        done < "$file"
    fi
    CONF_IDLE=$idle
    CONF_STARTUP=$startup
    CONF_IDLE_MINUTES=$minutes
    CONF_INTERVAL_MINUTES=$interval
}

# 从「键: 值」格式的文件里取一个键的值
state_get() {
    local file="$1" key="$2" line
    [[ -f "$file" ]] || return 0
    while IFS= read -r line; do
        line="${line%%#*}"
        [[ "$line" == *:* ]] || continue
        if [[ "$(trim "${line%%:*}")" == "$key" ]]; then
            printf '%s' "$(trim "${line#*:}")"
            return 0
        fi
    done < "$file"
    printf ''
}

# 读取系统键鼠空闲时间（纳秒）。测试时可用 RIME_SYNC_IOREG_CMD 注入假命令。
rime_sync_idle_nanos() {
    ${RIME_SYNC_IOREG_CMD:-ioreg -c IOHIDSystem -d 4} \
        | awk '/"HIDIdleTime"/ {gsub(/[^0-9]/, "", $NF); print $NF; exit}'
}

# 读取系统本次开机时间（秒，用于判断是不是开机后第一次运行）。
# 测试时可用 RIME_SYNC_BOOTTIME_CMD 注入假命令。
rime_sync_boottime() {
    ${RIME_SYNC_BOOTTIME_CMD:-sysctl -n kern.boottime} \
        | awk -F'sec = ' '{n=$2; gsub(/[^0-9].*/, "", n); print n}'
}

# 同步目录：优先读 installation.yaml 里的 sync_dir，没有则用 <Rime目录>/sync。
# 测试时可用 RIME_SYNC_SYNC_DIR 注入。
rime_sync_sync_dir() {
    if [[ -n "${RIME_SYNC_SYNC_DIR:-}" ]]; then
        printf '%s' "$RIME_SYNC_SYNC_DIR"
        return 0
    fi
    local dir=""
    if [[ -f "${RIME_ROOT}/installation.yaml" ]]; then
        dir="$(awk -F'"' '/^sync_dir:/ {print $2; exit}' "${RIME_ROOT}/installation.yaml")"
    fi
    [[ -z "$dir" ]] && dir="${RIME_ROOT}/sync"
    printf '%s' "$dir"
}

# 「有更新才执行」门卫：取本地用户库 + 同步目录里最新的文件修改时间（epoch 秒）。
# 大于游标里的「上次同步」即有新数据——本地是打字产生的调频/造词，
# 同步目录是云盘从其他设备带来的新快照。两边都没动过就跳过同步。
rime_sync_data_newest() {
    local sync_dir
    sync_dir="$(rime_sync_sync_dir)"
    {
        find "$RIME_ROOT" -maxdepth 2 -path '*.userdb/*' -type f -print0 2>/dev/null
        find "$sync_dir" -maxdepth 2 -type f ! -name '.DS_Store' -print0 2>/dev/null
    } | xargs -0 /usr/bin/stat -f %m 2>/dev/null | awk 'BEGIN{m=0} $1>m{m=$1} END{print m}'
}

rime_sync_sync() {
    local reason="$1" ok=0 out msg stamp
    if [[ -f "$SQUIRREL" ]]; then
        if out="$("$SQUIRREL" --sync 2>&1)"; then
            ok=1
        fi
        msg="$(printf '%s' "$out" | tr '\r\n' '  ')"
        msg="$(trim "$msg")"
        [[ -z "$msg" ]] && msg="同步完成。"
    else
        msg="未找到鼠须管 Squirrel，无法同步。"
    fi
    stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    msg="${msg//\\/\\\\}"
    msg="${msg//\"/\\\"}"
    printf '{\n  "ok": %s,\n  "reason": "%s",\n  "message": "%s",\n  "time": "%s"\n}\n' \
        "$([[ $ok = 1 ]] && echo true || echo false)" "$reason" "$msg" "$stamp" > "$STATE_FILE"
    echo "[$stamp] $reason: $([[ $ok = 1 ]] && echo 成功 || echo 失败) $msg"
}

# 单次运行：开机判断 + 空闲判断 + 「有更新才执行」门卫，最多同步一两次，然后退出。
# 同步实际在输入法进程里异步完成，写游标前等几秒让快照落盘，
# 避免自己刚写的快照被下一轮误判为“有更新”（测试用 RIME_SYNC_SETTLE_SECONDS 调整）。
rime_sync_run() {
    rime_sync_parse_conf "$CONF_FILE"

    local boot saved_boot armed last_sync newest has_changes nanos threshold
    boot="$(rime_sync_boottime)"
    saved_boot="$(state_get "$CURSOR_FILE" 开机时间)"
    armed="$(state_get "$CURSOR_FILE" 已武装)"
    [[ -z "$armed" ]] && armed=是
    last_sync="$(state_get "$CURSOR_FILE" 上次同步)"

    newest="$(rime_sync_data_newest)"
    if [[ -z "$last_sync" ]] || (( newest > last_sync )); then
        has_changes=1
    else
        has_changes=0
    fi

    if [[ -n "$boot" && "$saved_boot" != "$boot" ]]; then
        if (( CONF_STARTUP && has_changes )); then
            rime_sync_sync startup
            sleep "${RIME_SYNC_SETTLE_SECONDS:-5}"
            last_sync="$(date +%s)"
        fi
        armed=是
    fi

    if (( CONF_IDLE )); then
        nanos="$(rime_sync_idle_nanos)"
        if [[ "$nanos" =~ ^[0-9]+$ ]]; then
            (( threshold = CONF_IDLE_MINUTES * 60000000000 ))
            if (( nanos < threshold )); then
                armed=是
            elif [[ "$armed" == 是 ]]; then
                if (( has_changes )); then
                    armed=否
                    rime_sync_sync idle
                    sleep "${RIME_SYNC_SETTLE_SECONDS:-5}"
                    last_sync="$(date +%s)"
                fi
                # 没有新数据时保持武装：一出现新数据（本地打字或云盘新快照）就同步
            fi
        fi
    fi

    printf '已武装: %s\n开机时间: %s\n上次同步: %s\n' "$armed" "$boot" "$last_sync" > "$CURSOR_FILE"
}

rime_sync_plist_content() {
    local script="$1" interval_minutes=$2
    cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>${LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>${script}</string>
		<string>run</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>StartInterval</key>
	<integer>$(( interval_minutes * 60 ))</integer>
	<key>ProcessType</key>
	<string>Background</string>
</dict>
</plist>
PLIST
}

rime_sync_install() {
    rime_sync_parse_conf "$CONF_FILE"
    local plist="$HOME/Library/LaunchAgents/${LABEL}.plist"
    mkdir -p "$(dirname "$plist")"
    rime_sync_plist_content "$SCRIPT_PATH" "$CONF_INTERVAL_MINUTES" > "$plist"
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
    if ! launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
        launchctl load "$plist"
    fi
    echo "已注册开机 + 定时同步（LaunchAgent: ${plist}）。"
    echo "无常驻进程：登录时和每 ${CONF_INTERVAL_MINUTES} 分钟被系统拉起，跑完即退。"
    echo "设置文件：${CONF_FILE}（直接编辑保存即可，即时生效）"
    echo "注意：修改「检查间隔分钟数」后需重新执行本安装。"
}

rime_sync_uninstall() {
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
    rm -f "$HOME/Library/LaunchAgents/${LABEL}.plist"
    echo "已注销同步（LaunchAgent 已移除）。"
}

rime_sync_status() {
    rime_sync_parse_conf "$CONF_FILE"
    local registered="未注册"
    if launchctl list "$LABEL" >/dev/null 2>&1; then
        registered="已注册（定时触发，无常驻进程）"
    fi
    echo "注册状态：${registered}"
    echo "空闲同步：$([[ $CONF_IDLE = 1 ]] && echo 开 || echo 关)（${CONF_IDLE_MINUTES} 分钟，每 ${CONF_INTERVAL_MINUTES} 分钟检查一次）"
    echo "开机同步：$([[ $CONF_STARTUP = 1 ]] && echo 开 || echo 关)"
    echo "同步程序：$([[ -f $SQUIRREL ]] && echo 已找到 Squirrel || echo 未找到 Squirrel)"
    echo "设置文件：${CONF_FILE}"
    if [[ -f "$STATE_FILE" ]]; then
        echo "上次同步：$(cat "$STATE_FILE" | tr -d '\n' | sed 's/  */ /g')"
    else
        echo "上次同步：尚未执行"
    fi
}

main() {
    case "${1:-status}" in
        install) rime_sync_install ;;
        uninstall) rime_sync_uninstall ;;
        run) rime_sync_run ;;
        status) rime_sync_status ;;
        *)
            echo "用法：rime_sync.sh <install|uninstall|run|status>" >&2
            exit 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
