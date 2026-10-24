#!/bin/sh
set -eu

log_file=/var/log/prl/access.jsonl
rotate_bytes=${PRL_GATEWAY_ROTATE_BYTES:-10485760}
rotate_files=${PRL_GATEWAY_ROTATE_FILES:-4}
rotate_interval=${PRL_GATEWAY_ROTATE_INTERVAL:-1}

case "$rotate_bytes" in
    ''|0|0*|*[!0-9]*)
        printf '%s\n' 'gateway log rotation rejected: byte threshold must be a positive decimal integer' >&2
        exit 64
        ;;
esac
[ "$rotate_bytes" -le 67108864 ] || {
    printf '%s\n' 'gateway log rotation rejected: byte threshold exceeds the 64 MiB spool bound' >&2
    exit 64
}

case "$rotate_files" in
    ''|0|0*|*[!0-9]*)
        printf '%s\n' 'gateway log rotation rejected: rotated file count must be a positive decimal integer' >&2
        exit 64
        ;;
esac
[ "$rotate_files" -le 4 ] || {
    printf '%s\n' 'gateway log rotation rejected: rotated file count exceeds four' >&2
    exit 64
}

case "$rotate_interval" in
    ''|.*|*.|*.*.*|*[!0-9.]*|0|0.0|0.00|0.000)
        printf '%s\n' 'gateway log rotation rejected: interval must be a positive decimal number of seconds' >&2
        exit 64
        ;;
esac

[ -d /var/log/prl ] || {
    printf '%s\n' 'gateway log rotation failed: shared log volume is unavailable' >&2
    exit 1
}

nginx_master_pid() {
    for process in /proc/[0-9]*; do
        [ -r "$process/cmdline" ] || continue
        command_line=$(tr '\000' ' ' < "$process/cmdline" 2>/dev/null || true)
        case "$command_line" in
            *'nginx: master process'*)
                printf '%s\n' "${process##*/}"
                return 0
                ;;
        esac
    done
    return 1
}

rotate_if_full() {
    [ -f "$log_file" ] || return 0
    size=$(wc -c < "$log_file" | tr -d '[:space:]')
    [ "$size" -ge "$rotate_bytes" ] || return 0

    master_pid=$(nginx_master_pid) || return 0
    rm -f "$log_file.$rotate_files"
    index=$((rotate_files - 1))
    while [ "$index" -ge 1 ]; do
        if [ -f "$log_file.$index" ]; then
            next=$((index + 1))
            mv -f "$log_file.$index" "$log_file.$next"
        fi
        index=$((index - 1))
    done
    mv -f "$log_file" "$log_file.1"
    kill -USR1 "$master_pid"

    attempt=0
    while [ "$attempt" -lt 40 ]; do
        [ -f "$log_file" ] && return 0
        sleep 0.05
        attempt=$((attempt + 1))
    done
    printf '%s\n' 'gateway log rotation failed: Nginx did not reopen the access log' >&2
    return 1
}

while :; do
    rotate_if_full
    sleep "$rotate_interval"
done
