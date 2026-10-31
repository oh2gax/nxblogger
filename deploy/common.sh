# Shared helpers for install.sh and update.sh (sourced, not executed).
# shellcheck shell=bash

SERVICE_NAME="nxblogger"
SERVICE_USER="nxblogger"
ENV_DIR="/etc/nxblogger"
ENV_FILE="$ENV_DIR/nxblogger.env"
SSL_DIR="$ENV_DIR/ssl"
DATA_DIR_DEFAULT="/var/lib/nxblogger"
ACME_ROOT="/var/www/certbot"
NGINX_SITE="/etc/nginx/sites-available/nxblogger"
NGINX_LINK="/etc/nginx/sites-enabled/nxblogger"

log()  { printf '\033[1;34m[nxblogger]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[nxblogger] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[nxblogger] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || die "Run as root, e.g.: sudo bash $0"
}

env_get() {  # env_get KEY [default]
  local value=""
  if [[ -f "$ENV_FILE" ]]; then
    value="$(grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  fi
  printf '%s' "${value:-${2:-}}"
}

env_set() {  # env_set KEY VALUE
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -qE "^$key=" "$ENV_FILE"; then
    sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

env_default() {  # set only when missing
  local key="$1" value="$2"
  grep -qE "^$key=" "$ENV_FILE" 2>/dev/null || env_set "$key" "$value"
}

ensure_env_defaults() {
  mkdir -p "$ENV_DIR"
  if [[ ! -f "$ENV_FILE" ]]; then
    printf '# NXBlogger configuration – used by the systemd service and manage.py\n' > "$ENV_FILE"
  fi
  env_default NXB_SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  env_default NXB_DATA_DIR "$DATA_DIR_DEFAULT"
  env_default NXB_ADMIN_USER "oh2gax"
  env_default NXB_BLOG_TITLE '"OH2GAX Blog"'
  env_default NXB_HTTPS_PORT "8800"
  env_default NXB_APP_PORT "8801"
  env_default NXB_DOMAIN ""
  env_default NXB_LE_EMAIL ""
  env_default NXB_LETSENCRYPT "0"
  env_default NXB_TIMEZONE "Europe/Helsinki"
  env_default NXB_COOKIE_SECURE "1"
  env_default NXB_BEHIND_PROXY "1"
  env_default NXB_MAX_UPLOAD_MB "200"
  env_default NXB_MAX_IMAGE_MB "25"
  env_default NXB_GUNICORN_WORKERS "2"
  chown root:"$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
  chmod 640 "$ENV_FILE"
}

render() {  # render TEMPLATE DEST  (replaces @@VAR@@ placeholders)
  local tmpl="$1" dest="$2"
  local data_dir https_port app_port server_name max_mb port_suffix
  data_dir="$(env_get NXB_DATA_DIR "$DATA_DIR_DEFAULT")"
  https_port="$(env_get NXB_HTTPS_PORT 8800)"
  app_port="$(env_get NXB_APP_PORT 8801)"
  server_name="$(env_get NXB_DOMAIN)"; server_name="${server_name:-_}"
  max_mb="$(env_get NXB_MAX_UPLOAD_MB 200)"
  port_suffix=""; [[ "$https_port" != "443" ]] && port_suffix=":$https_port"
  sed -e "s|@@APP_DIR@@|$APP_DIR|g" \
      -e "s|@@DATA_DIR@@|$data_dir|g" \
      -e "s|@@HTTPS_PORT@@|$https_port|g" \
      -e "s|@@APP_PORT@@|$app_port|g" \
      -e "s|@@SERVER_NAME@@|$server_name|g" \
      -e "s|@@MAX_UPLOAD_MB@@|$max_mb|g" \
      -e "s|@@PORT_SUFFIX@@|$port_suffix|g" \
      -e "s|@@SSL_DIR@@|$SSL_DIR|g" \
      -e "s|@@ACME_ROOT@@|$ACME_ROOT|g" \
      -e "s|@@SERVICE_USER@@|$SERVICE_USER|g" \
      "$tmpl" > "$dest"
}

render_nginx() {
  local tmp domain
  tmp="$(mktemp)"
  domain="$(env_get NXB_DOMAIN)"
  render "$APP_DIR/deploy/nginx/https.conf.tmpl" "$tmp"
  if [[ -n "$domain" ]]; then
    # Port 80 is only used for Let's Encrypt validation and redirects.
    render "$APP_DIR/deploy/nginx/http.conf.tmpl" "$tmp.http"
    cat "$tmp.http" >> "$tmp"
    rm -f "$tmp.http"
  fi
  install -m 644 "$tmp" "$NGINX_SITE"
  rm -f "$tmp"
  ln -sf "$NGINX_SITE" "$NGINX_LINK"
}

render_systemd() {
  render "$APP_DIR/deploy/systemd/nxblogger.service" /etc/systemd/system/nxblogger.service
  render "$APP_DIR/deploy/systemd/nxblogger-update.service" /etc/systemd/system/nxblogger-update.service
  cp "$APP_DIR/deploy/systemd/nxblogger-update.timer" /etc/systemd/system/nxblogger-update.timer
  render "$APP_DIR/deploy/systemd/nxblogger-backup.service" /etc/systemd/system/nxblogger-backup.service
  cp "$APP_DIR/deploy/systemd/nxblogger-backup.timer" /etc/systemd/system/nxblogger-backup.timer
  systemctl daemon-reload
}

install_manage_wrapper() {
  cat > /usr/local/bin/nxblogger-manage <<EOF
#!/bin/bash
# Run NXBlogger management commands as the service user.
cd "$APP_DIR" && exec sudo -u $SERVICE_USER "$APP_DIR/venv/bin/python" "$APP_DIR/manage.py" "\$@"
EOF
  chmod 755 /usr/local/bin/nxblogger-manage
}

manage() {  # run manage.py as service user
  (cd "$APP_DIR" && sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/python" "$APP_DIR/manage.py" "$@")
}

setup_venv() {
  if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    log "Creating Python virtual environment"
    python3 -m venv "$APP_DIR/venv"
  fi
  log "Installing Python packages"
  "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
  "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
}

fix_permissions() {
  local data_dir
  data_dir="$(env_get NXB_DATA_DIR "$DATA_DIR_DEFAULT")"
  mkdir -p "$data_dir/data" "$data_dir/uploads" "$data_dir/backups"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$data_dir"
  chmod 750 "$data_dir" "$data_dir/uploads"
  chmod 700 "$data_dir/data" "$data_dir/backups"
  find "$data_dir/uploads" -type d -exec chmod 750 {} + 2>/dev/null || true
  find "$data_dir/uploads" -type f -exec chmod 640 {} + 2>/dev/null || true
  # nginx (www-data) serves /uploads directly, so it needs group read access.
  usermod -aG "$SERVICE_USER" www-data 2>/dev/null || true
}

health_check() {
  local port
  port="$(env_get NXB_APP_PORT 8801)"
  for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
      log "Health check OK"
      return 0
    fi
    sleep 1
  done
  warn "Service did not answer on 127.0.0.1:$port/healthz. Last log lines:"
  journalctl -u "$SERVICE_NAME" -n 30 --no-pager >&2 || true
  return 1
}
