#!/usr/bin/env bash
# NXBlogger updater: pulls the latest code from GitHub, updates Python packages,
# migrates the database, refreshes nginx/systemd config and restarts the service.
#
#   sudo bash /opt/nxblogger/update.sh            update if GitHub has new commits
#   sudo bash /opt/nxblogger/update.sh --force    re-apply even when already up to date
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/common.sh
source "$APP_DIR/deploy/common.sh"

# git runs as the owner of the code folder, so pulled files keep the right owner
# and the owner's own GitHub access (SSH key) is used.
git_c() { as_owner git -C "$APP_DIR" -c safe.directory="$APP_DIR" "$@"; }

pull_phase() {
  local force="$1" old new branch
  [[ -d "$APP_DIR/.git" ]] || die "$APP_DIR is not a git clone. Install with: git clone <repo> /opt/nxblogger"
  branch="$(git_c rev-parse --abbrev-ref HEAD)"
  old="$(git_c rev-parse HEAD)"
  log "Checking GitHub for updates (branch $branch)"
  git_c fetch --quiet origin "$branch"
  new="$(git_c rev-parse "origin/$branch")"

  if [[ "$old" == "$new" && "$force" != "1" ]]; then
    log "Already up to date ($(git_c log -1 --format='%h %s'))"
    exit 0
  fi
  if ! git_c diff --quiet || ! git_c diff --cached --quiet; then
    git_c status --short
    die "Local changes in $APP_DIR would be overwritten. Commit/stash them (or run: git -C $APP_DIR checkout -- .) and retry."
  fi
  if [[ "$old" != "$new" ]]; then
    log "New commits:"
    git_c log --oneline "$old..$new" | sed 's/^/    /'
    git_c merge --ff-only --quiet "origin/$branch" || die "Fast-forward failed – history differs from GitHub."
  fi
  # Continue with the freshly pulled version of this script.
  exec bash "$APP_DIR/update.sh" --apply "$old"
}

apply_phase() {
  local old="$1"
  ensure_env_defaults
  load_service_user

  log "Backing up database"
  manage backup --db-only --keep 10 || warn "Backup failed (continuing)"

  setup_venv
  log "Migrating database"
  manage init-db
  fix_permissions
  sync_static
  install_manage_wrapper

  log "Refreshing nginx and systemd configuration"
  render_nginx
  render_systemd
  if nginx -t 2>/dev/null; then
    systemctl reload nginx
  else
    nginx -t || true
    warn "nginx configuration test failed – nginx was NOT reloaded."
  fi

  log "Restarting service"
  systemctl restart nxblogger
  if health_check; then
    log "Update complete: $(git_c log -1 --format='%h %s (%cd)' --date=short)"
  else
    warn "The new version is not healthy. To roll back:"
    warn "  sudo git -C $APP_DIR reset --hard $old && sudo bash $APP_DIR/update.sh --apply $old"
    exit 1
  fi
}

main() {
  require_root
  case "${1:-}" in
    --apply) apply_phase "${2:-HEAD}" ;;
    --force) pull_phase 1 ;;
    -h|--help) sed -n '2,7p' "$0" ;;
    "") pull_phase 0 ;;
    *) die "Unknown option: $1" ;;
  esac
}

main "$@"; exit $?
