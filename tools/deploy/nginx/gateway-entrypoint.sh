#!/bin/sh
set -eu

case "${PRL_GATEWAY_ENVIRONMENT:-local}" in
    local|demo|production) environment=${PRL_GATEWAY_ENVIRONMENT:-local} ;;
    *) printf '%s\n' 'gateway metadata rejected: PRL_GATEWAY_ENVIRONMENT must be local, demo, or production' >&2; exit 64 ;;
esac

release=${SERVICE_RELEASE:-development}
case "$release" in
    development) ;;
    *)
        case "$release" in *[!a-f0-9]*|'') printf '%s\n' 'gateway metadata rejected: SERVICE_RELEASE must be development or a 40-character lowercase source SHA' >&2; exit 64 ;; esac
        [ "${#release}" -eq 40 ] || { printf '%s\n' 'gateway metadata rejected: SERVICE_RELEASE must be development or a 40-character lowercase source SHA' >&2; exit 64; }
        ;;
esac

ops_host=${PRL_OPS_HOST:-}
ops_cidrs=${PRL_OPS_CIDRS:-}
umask 077
cat > /tmp/prl-metadata.conf <<EOF
map "" \$prl_environment { default "$environment"; }
map "" \$prl_release { default "$release"; }
EOF
: > /tmp/prl-ops-server.conf
if [ -n "$ops_host" ] || [ -n "$ops_cidrs" ]; then
    case "$ops_host" in
        ''|*[!A-Za-z0-9.-]*|.*|*..*|*.) printf '%s\n' 'gateway operations host rejected' >&2; exit 64 ;;
    esac
    [ "${#ops_host}" -le 253 ] || { printf '%s\n' 'gateway operations host rejected' >&2; exit 64; }
    saved_ifs=$IFS
    IFS=.
    set -- $ops_host
    IFS=$saved_ifs
    for label do
        case "$label" in
            ''|-*|*-) printf '%s\n' 'gateway operations host rejected' >&2; exit 64 ;;
            *[!A-Za-z0-9-]*) printf '%s\n' 'gateway operations host rejected' >&2; exit 64 ;;
        esac
        [ "${#label}" -le 63 ] || { printf '%s\n' 'gateway operations host rejected' >&2; exit 64; }
    done
    case "$ops_cidrs" in
        ''|,*|*,|*,,*|*[!0-9A-Fa-f:./,]*)
            printf '%s\n' 'gateway operations allowlist rejected' >&2
            exit 64
            ;;
    esac
    [ -r /etc/nginx/tls/ops.crt ] && [ -r /etc/nginx/tls/ops.key ] || {
        printf '%s\n' 'gateway operations TLS files are unavailable' >&2
        exit 64
    }
    cat > /tmp/prl-ops-server.conf <<EOF
server {
    listen 8443 ssl;
    server_name $ops_host;
    ssl_certificate /etc/nginx/tls/ops.crt;
    ssl_certificate_key /etc/nginx/tls/ops.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:TLS:1m;
    ssl_session_tickets off;
    access_log /dev/stdout prl_operations;
    access_log /var/log/prl/access.jsonl prl_operations;
EOF
    remaining=$ops_cidrs
    while :; do
        case "$remaining" in
            *,*) cidr=${remaining%%,*}; remaining=${remaining#*,}; more=1 ;;
            *) cidr=$remaining; remaining=; more=0 ;;
        esac
        case "$cidr" in
            ''|*/*/*|*[!0-9A-Fa-f:./]*)
                printf '%s\n' 'gateway operations allowlist rejected' >&2
                exit 64
                ;;
        esac
        case "$cidr" in
            */*) ;;
            *) printf '%s\n' 'gateway operations allowlist rejected' >&2; exit 64 ;;
        esac
        printf '    allow %s;\n' "$cidr" >> /tmp/prl-ops-server.conf
        [ "$more" -eq 1 ] || break
    done
    cat >> /tmp/prl-ops-server.conf <<'EOF'
    deny all;
    location / {
        set $prl_ops_upstream http://grafana:3000;
        proxy_pass $prl_ops_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-Port "";
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
EOF
fi
# Keep metadata private while allowing the designated Collector group to read logs.
umask 027
nginx -t
exec nginx -g 'daemon off;'
