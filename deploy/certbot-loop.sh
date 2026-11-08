#!/bin/sh
# Let's Encrypt certificates for the nginx web container (compose service
# "certbot"). Uses the webroot challenge: nginx serves
# /.well-known/acme-challenge/ from the shared acme volume on port 80, so DNS
# for IMS_DOMAIN must point at this server and port 80 must be reachable.
# nginx (deploy/nginx/40-ims.sh) installs new certificates within 5 minutes.
set -u

DOMAIN="${IMS_DOMAIN:-localhost}"
WEBROOT=/var/www/certbot

is_public() {
  case "$1" in *.*) ;; *) return 1 ;; esac
  case "$1" in *.localhost|*.local|*.lan|*.internal|*.home.arpa|*.test|*.example|*.invalid) return 1 ;; esac
  case "$1" in *[!0-9.]*) return 0 ;; *) return 1 ;; esac  # digits and dots only = an IP address
}

if ! is_public "$DOMAIN"; then
  echo "certbot: '$DOMAIN' is not a public domain name; nginx keeps its self-signed certificate."
  while :; do sleep 86400; done
fi

if [ -n "${LETSENCRYPT_EMAIL:-}" ]; then
  set -- --email "$LETSENCRYPT_EMAIL" --no-eff-email
else
  set -- --register-unsafely-without-email
fi

# The console's own subdomain (if any) shares the certificate.
NAMES="-d $DOMAIN"
case "${IMS_CONSOLE_DOMAIN:-}" in ""|*[!A-Za-z0-9.-]*) ;; *) NAMES="$NAMES -d $IMS_CONSOLE_DOMAIN" ;; esac

while :; do
  # --keep-until-expiring: a no-op until renewal is due; --expand adds a newly
  # configured console subdomain to the existing certificate.
  # shellcheck disable=SC2086
  if certbot certonly --webroot -w "$WEBROOT" $NAMES --cert-name "$DOMAIN" --keep-until-expiring --expand \
      --non-interactive --agree-tos "$@"; then
    wait=43200  # twice a day
  else
    echo "certbot: could not get a certificate for $DOMAIN${IMS_CONSOLE_DOMAIN:+ and $IMS_CONSOLE_DOMAIN}. Check that DNS points here and port 80 is open. Retrying in 1 hour."
    wait=3600
  fi
  sleep "$wait"
done
