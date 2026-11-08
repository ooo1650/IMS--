#!/bin/sh
# Runs from the nginx image's entrypoint before nginx starts.
#  1. Writes the /django-admin allowlist from IMS_ADMIN_ALLOW.
#  2. Installs a TLS certificate: Let's Encrypt (from the certbot service) if
#     there is one for IMS_DOMAIN, otherwise a self-signed one.
#  3. Starts a watcher that installs new/renewed Let's Encrypt certificates
#     and reloads nginx (checks every 5 minutes).
set -eu

RUN=/etc/nginx/runtime
DOMAIN="${IMS_DOMAIN:-localhost}"
LIVE="/etc/letsencrypt/live/$DOMAIN"
mkdir -p "$RUN/tls"

# --- 1. Allowlists: /django-admin (IMS_ADMIN_ALLOW) and the console (IMS_CONSOLE_ALLOW)
PRIVATE="127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 ::1 fc00::/7 fe80::/10"
write_allow() {  # write_allow <file> <space-separated CIDRs or private_ranges>
  : > "$1"
  for cidr in $2; do
    [ "$cidr" = "private_ranges" ] && cidr="$PRIVATE"
    for c in $cidr; do
      case "$c" in
        *[!0-9A-Fa-f:./]*) echo "40-ims: ignoring invalid allowlist entry: $c" >&2 ;;
        *) echo "allow $c;" >> "$1" ;;
      esac
    done
  done
  echo "deny all;" >> "$1"
}
write_allow "$RUN/admin-allow.inc" "${IMS_ADMIN_ALLOW:-private_ranges}"
write_allow "$RUN/console-allow.inc" "${IMS_CONSOLE_ALLOW:-private_ranges}"

# The operator console: port 8443 on the main domain (same certificate), or
# its own subdomain on 443 when IMS_CONSOLE_DOMAIN is set.
CONSOLE_DOMAIN="${IMS_CONSOLE_DOMAIN:-}"
case "$CONSOLE_DOMAIN" in *[!A-Za-z0-9.-]*) echo "40-ims: invalid IMS_CONSOLE_DOMAIN, using port 8443" >&2; CONSOLE_DOMAIN="" ;; esac
if [ -n "$CONSOLE_DOMAIN" ]; then
  printf 'listen 443 ssl;\nlisten [::]:443 ssl;\nserver_name %s;\n' "$CONSOLE_DOMAIN" > "$RUN/console-listen.inc"
else
  printf 'listen 8443 ssl;\nlisten [::]:8443 ssl;\nserver_name _;\n' > "$RUN/console-listen.inc"
fi

# --- 2. TLS certificate ------------------------------------------------------
install_le() {
  cp -L "$LIVE/fullchain.pem" "$RUN/tls/fullchain.pem.new"
  cp -L "$LIVE/privkey.pem" "$RUN/tls/privkey.pem.new"
  chmod 600 "$RUN/tls/privkey.pem.new"
  mv "$RUN/tls/fullchain.pem.new" "$RUN/tls/fullchain.pem"
  mv "$RUN/tls/privkey.pem.new" "$RUN/tls/privkey.pem"
}

if [ -s "$LIVE/fullchain.pem" ] && [ -s "$LIVE/privkey.pem" ]; then
  install_le
  echo "40-ims: using the Let's Encrypt certificate for $DOMAIN"
else
  case "$DOMAIN" in
    *[!0-9.]*) san="DNS:$DOMAIN,DNS:localhost,IP:127.0.0.1${CONSOLE_DOMAIN:+,DNS:$CONSOLE_DOMAIN}" ;;
    *) san="IP:$DOMAIN,DNS:localhost,IP:127.0.0.1" ;;
  esac
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=$DOMAIN" -addext "subjectAltName=$san" \
    -keyout "$RUN/tls/privkey.pem" -out "$RUN/tls/fullchain.pem" 2>/dev/null
  chmod 600 "$RUN/tls/privkey.pem"
  echo "40-ims: no Let's Encrypt certificate for $DOMAIN yet; using a self-signed one"
fi

# --- 3. Pick up new and renewed certificates ----------------------------------
(
  while sleep 300; do
    if [ -s "$LIVE/fullchain.pem" ] && [ -s "$LIVE/privkey.pem" ] \
        && ! cmp -s "$LIVE/fullchain.pem" "$RUN/tls/fullchain.pem"; then
      if install_le && nginx -t -q && nginx -s reload; then
        echo "40-ims: installed the Let's Encrypt certificate for $DOMAIN and reloaded nginx"
      fi
    fi
  done
) &
