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

umask 077
cat > /tmp/prl-metadata.conf <<EOF
map "" \$prl_environment { default "$environment"; }
map "" \$prl_release { default "$release"; }
EOF
# Keep metadata private while allowing the designated Collector group to read logs.
umask 027
nginx -t
exec nginx -g 'daemon off;'
