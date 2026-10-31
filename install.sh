#!/usr/bin/env bash
# NXBlogger installer for Ubuntu 24.04 LTS.
#
#   sudo bash install.sh --domain blog.oh2gax.org --port 8800 [--email you@example.com]
#
# Safe to run again: it keeps the database, uploads and settings, and only
# updates what changed (options given on the command line are saved to
# /etc/nxblogger/nxblogger.env).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/common.sh
source "$APP_DIR/deploy/common.sh"

usage() {
  cat <<EOF
Usage: sudo bash install.sh [options]

  --domain NAME        DNS name of the blog (default: blog.oh2gax.org)
  --port N             public HTTPS port nginx listens on (default: 8800)
  --app-port N         internal gunicorn port on 127.0.0.1 (default: 8801)
  --email ADDR         e-mail for Let's Encrypt expiry notices (optional)
  --self-signed        skip Let's Encrypt and use a self-signed certificate
  --auto-update        enable daily automatic update from GitHub (update.sh)
  --no-auto-update     disable the daily automatic update
  --admin-password PW  initial password for the admin user (otherwise asked)
  -h, --help           show this help
EOF
}

DOMAIN="" PORT="" APP_PORT="" EMAIL="" SELF_SIGNED=0 AUTO_UPDATE="" ADMIN_PW=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --app-port) APP_PORT="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --self-signed) SELF_SIGNED=1; shift ;;
    --auto-update) AUTO_UPDATE=1; shift ;;
    --no-auto-update) AUTO_UPDATE=0; shift ;;
    --admin-password) ADMIN_PW="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unknown option: $1" ;;
  esac
done

require_root
[[ "$APP_DIR" == /opt/* ]] || warn "Recommended location is /opt/nxblogger (now: $APP_DIR)."

# --- 1. System packages --------------------------------------------------------
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx git openssl curl sqlite3 certbot >/dev/null

# --- 2. Service user -------------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "Creating system user $SERVICE_USER"
  useradd --system --home-dir "$DATA_DIR_DEFAULT" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# --- 3. Configuration --------------------------------------------------------------
ensure_env_defaults
[[ -n "$PORT" ]] && env_set NXB_HTTPS_PORT "$PORT"
[[ -n "$APP_PORT" ]] && env_set NXB_APP_PORT "$APP_PORT"
[[ -n "$EMAIL" ]] && env_set NXB_LE_EMAIL "$EMAIL"
if [[ -n "$DOMAIN" ]]; then
  env_set NXB_DOMAIN "$DOMAIN"
elif [[ -z "$(env_get NXB_DOMAIN)" ]]; then
  env_set NXB_DOMAIN "blog.oh2gax.org"
fi
DOMAIN="$(env_get NXB_DOMAIN)"
HTTPS_PORT="$(env_get NXB_HTTPS_PORT)"
[[ "$(env_get NXB_APP_PORT)" != "$HTTPS_PORT" ]] || die "--app-port must differ from --port"

# --- 4. Directories, Python environment, database --------------------------------
fix_permissions
setup_venv
log "Preparing database"
manage init-db
install_manage_wrapper

if [[ -n "$ADMIN_PW" ]]; then
  manage set-password --password "$ADMIN_PW"
elif manage has-admin >/dev/null 2>&1; then
  log "Admin password already set (change it in Maintenance → Settings)"
elif [[ -t 0 ]]; then
  log "Choose the password for admin user '$(env_get NXB_ADMIN_USER)'"
  manage set-password
else
  GEN_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  manage set-password --password "$GEN_PW"
  warn "Generated admin password: $GEN_PW  (change it after first login)"
fi

# --- 5. TLS certificate (self-signed first, so nginx can start) -------------------
mkdir -p "$SSL_DIR" "$ACME_ROOT"
chmod 750 "$SSL_DIR"
if [[ ! -f "$SSL_DIR/selfsigned-cert.pem" ]]; then
  log "Creating self-signed certificate for $DOMAIN"
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$SSL_DIR/selfsigned-key.pem" -out "$SSL_DIR/selfsigned-cert.pem" \
    -subj "/CN=$DOMAIN" -addext "subjectAltName=DNS:$DOMAIN" >/dev/null 2>&1
  chmod 600 "$SSL_DIR/selfsigned-key.pem"
fi
if [[ ! -e "$SSL_DIR/fullchain.pem" ]]; then
  ln -sf "$SSL_DIR/selfsigned-cert.pem" "$SSL_DIR/fullchain.pem"
  ln -sf "$SSL_DIR/selfsigned-key.pem" "$SSL_DIR/privkey.pem"
fi

# --- 6. nginx ------------------------------------------------------------------------
log "Configuring nginx (HTTPS on port $HTTPS_PORT)"
render_nginx
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
  log "Disabled nginx default site"
fi
nginx -t
systemctl enable --quiet nginx
systemctl restart nginx

# --- 7. systemd service ----------------------------------------------------------------
log "Installing systemd service"
render_systemd
systemctl enable --quiet nxblogger
systemctl restart nxblogger
systemctl enable --quiet --now nxblogger-backup.timer
health_check || die "Service failed to start – see the log above."

# --- 8. Let's Encrypt ------------------------------------------------------------------
link_letsencrypt() {
  ln -sf "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$SSL_DIR/fullchain.pem"
  ln -sf "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$SSL_DIR/privkey.pem"
  env_set NXB_LETSENCRYPT 1
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/nxblogger-reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
  chmod 755 /etc/letsencrypt/renewal-hooks/deploy/nxblogger-reload-nginx.sh
  nginx -t && systemctl reload nginx
}

if [[ $SELF_SIGNED -eq 1 ]]; then
  log "Using self-signed certificate (--self-signed)"
elif [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
  log "Let's Encrypt certificate for $DOMAIN already exists"
  link_letsencrypt
else
  log "Requesting Let's Encrypt certificate for $DOMAIN"
  if ! getent hosts "$DOMAIN" >/dev/null; then
    warn "$DOMAIN does not resolve yet. Point its DNS A record to this server and run install.sh again."
  else
    EMAIL="$(env_get NXB_LE_EMAIL)"
    if [[ -n "$EMAIL" ]]; then email_args=(--email "$EMAIL"); else email_args=(--register-unsafely-without-email); fi
    if certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" --agree-tos --non-interactive \
         --keep-until-expiring "${email_args[@]}"; then
      link_letsencrypt
      log "Let's Encrypt certificate installed (renews automatically via certbot.timer)"
    else
      warn "Let's Encrypt failed. Check that $DOMAIN points to this server and that TCP port 80 is open"
      warn "in the AWS security group, then run: sudo bash $APP_DIR/install.sh"
      warn "The blog keeps working with the self-signed certificate meanwhile."
    fi
  fi
fi

# --- 9. Automatic updates (optional) -------------------------------------------------------
if [[ "$AUTO_UPDATE" == "1" ]]; then
  systemctl enable --quiet --now nxblogger-update.timer
  log "Daily automatic update enabled (nxblogger-update.timer)"
elif [[ "$AUTO_UPDATE" == "0" ]]; then
  systemctl disable --quiet --now nxblogger-update.timer || true
  log "Automatic update disabled"
fi

# --- Done ------------------------------------------------------------------------------------
suffix=""; [[ "$HTTPS_PORT" != "443" ]] && suffix=":$HTTPS_PORT"
echo
log "NXBlogger is running."
log "  Blog:        https://$DOMAIN$suffix/"
log "  Maintenance: https://$DOMAIN$suffix/admin/   (user: $(env_get NXB_ADMIN_USER))"
log "  Open in the AWS security group: TCP $HTTPS_PORT (blog) and TCP 80 (Let's Encrypt)."
