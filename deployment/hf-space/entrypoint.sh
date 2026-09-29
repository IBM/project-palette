#!/bin/sh
# Render the nginx config from env, then run nginx in the foreground.
set -eu

UPSTREAM_URL="${UPSTREAM_URL%/}"
case "$UPSTREAM_URL" in
    http://*|https://*) ;;
    *) echo "UPSTREAM_URL must start with http:// or https:// (got '$UPSTREAM_URL')" >&2; exit 1 ;;
esac
UPSTREAM_HOST=$(echo "$UPSTREAM_URL" | sed -E 's#^https?://([^/:]+).*#\1#')
# nginx re-resolves the upstream through this resolver (variable proxy_pass),
# so a changed Code Engine IP doesn't strand the proxy on a stale address.
RESOLVER=$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf)
case "$RESOLVER" in *:*) RESOLVER="[$RESOLVER]" ;; esac

AUTH_BLOCK=""
if [ -n "${PROXY_USER:-}" ] && [ -n "${PROXY_PASSWORD:-}" ]; then
    printf '%s:%s\n' "$PROXY_USER" "$(openssl passwd -apr1 "$PROXY_PASSWORD")" > /tmp/htpasswd
    AUTH_BLOCK='auth_basic "Palette"; auth_basic_user_file /tmp/htpasswd;'
    echo "basic auth: on (user '$PROXY_USER')"
else
    echo "basic auth: off (set PROXY_USER + PROXY_PASSWORD secrets to enable)"
fi

export UPSTREAM_URL UPSTREAM_HOST RESOLVER AUTH_BLOCK
envsubst '${UPSTREAM_URL} ${UPSTREAM_HOST} ${RESOLVER} ${AUTH_BLOCK}' \
    < /etc/nginx/nginx.conf.template > /tmp/nginx.conf
echo "proxying :7860 -> $UPSTREAM_URL (resolver $RESOLVER)"
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
