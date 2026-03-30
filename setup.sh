#!/usr/bin/env bash
# =============================================================================
# Build Service — Production Setup Script
# Target: Ubuntu 22.04+ / Debian 12+ (systemd + Nginx + Let's Encrypt)
#
# Usage:
#   sudo ./setup.sh            # Full setup with SSL
#   sudo ./setup.sh --no-ssl   # Skip Certbot (handle TLS elsewhere, e.g. Cloudflare)
#   sudo ./setup.sh --update   # Re-deploy app files and restart service (skip prompts)
# =============================================================================
set -euo pipefail

# ── ANSI colours ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()   { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()    { echo -e "${RED}[FAIL]${NC}  $*" >&2; exit 1; }
header() { echo -e "\n${BOLD}${CYAN}$*${NC}"; printf "${CYAN}%0.s─${NC}" {1..52}; echo; }

# ── Defaults ──────────────────────────────────────────────────────────────────
APP_USER="buildsvc"
APP_DIR="/opt/build_service"
VENV_DIR="${APP_DIR}/venv"
LOG_DIR="/var/log/build_service"
SERVICE_NAME="build_service"
GUNICORN_BIND="127.0.0.1:8000"
WORKERS=2
THREADS=2

SKIP_SSL=0
UPDATE_ONLY=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse flags ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-ssl)  SKIP_SSL=1 ;;
        --update)  UPDATE_ONLY=1 ;;
        *) die "Unknown flag: $1  (valid: --no-ssl | --update)" ;;
    esac
    shift
done

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root:  sudo ./setup.sh"

# ── OS check ──────────────────────────────────────────────────────────────────
command -v apt-get &>/dev/null || die "This script requires a Debian/Ubuntu system."

# ═════════════════════════════════════════════════════════════════════════════
#  UPDATE MODE — re-deploy files + restart (no prompts)
# ═════════════════════════════════════════════════════════════════════════════
if [[ $UPDATE_ONLY -eq 1 ]]; then
    header "Update: re-deploying application files"
    rsync -a --delete \
        --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \
        --exclude='venv/'  --exclude='mydatabase.db' \
        --exclude='.env'   --exclude='config/credentials.json' \
        "${SCRIPT_DIR}/" "${APP_DIR}/"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    "${VENV_DIR}/bin/pip" install -q --upgrade -r "${APP_DIR}/requirements.txt"
    systemctl restart "${SERVICE_NAME}"
    ok "Update complete — service restarted."
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════════
#  FULL INSTALL
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${CYAN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║  Build Service — Production Setup                  ║${NC}"
echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Gather config ─────────────────────────────────────────────────────────────
header "Configuration"
read -rp "  Domain name  (e.g. builds.example.com)       : " DOMAIN
read -rp "  Admin e-mail (Let's Encrypt / alerts)        : " ADMIN_EMAIL
read -rp "  EVE SSO Client ID                            : " EVE_CLIENT_ID
read -rsp "  EVE SSO Client Secret                        : " EVE_CLIENT_SECRET; echo
read -rp "  Discord Webhook URL  [blank to skip]         : " DISCORD_WEBHOOK
read -rp "  EVE Alliance ID to enforce  [498125261]      : " ALLIANCE_ID
ALLIANCE_ID="${ALLIANCE_ID:-498125261}"
read -rp "  Default pickup/delivery location             : " DELIVERY_DEFAULT
read -rp "  Default build markup %  [15]                 : " MARKUP_PCT
MARKUP_PCT="${MARKUP_PCT:-15}"

[[ -n "$DOMAIN" ]]          || die "Domain name is required."
[[ -n "$EVE_CLIENT_ID" ]]   || die "EVE SSO Client ID is required."
[[ -n "$EVE_CLIENT_SECRET" ]] || die "EVE SSO Client Secret is required."

REDIRECT_URI="https://${DOMAIN}/auth/callback"
[[ $SKIP_SSL -eq 1 ]] && REDIRECT_URI="http://${DOMAIN}/auth/callback"

# ── System packages ───────────────────────────────────────────────────────────
header "Installing System Packages"
apt-get update -qq

CERTBOT_PKGS=""
[[ $SKIP_SSL -eq 0 ]] && CERTBOT_PKGS="certbot python3-certbot-nginx"

# shellcheck disable=SC2086
apt-get install -y -q \
    python3 python3-venv python3-pip \
    nginx \
    sqlite3 \
    rsync \
    $CERTBOT_PKGS
ok "Packages installed"

# ── System user ───────────────────────────────────────────────────────────────
header "System User"
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${APP_USER}"
    ok "Created user '${APP_USER}'"
else
    ok "User '${APP_USER}' already exists"
fi

# ── Deploy application files ──────────────────────────────────────────────────
header "Deploying Application"
mkdir -p "${APP_DIR}"
rsync -a --delete \
    --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \
    --exclude='venv/'  --exclude='mydatabase.db' \
    --exclude='.env'   --exclude='config/credentials.json' \
    "${SCRIPT_DIR}/" "${APP_DIR}/"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
ok "Files deployed to ${APP_DIR}"

# ── Python virtual environment ────────────────────────────────────────────────
header "Python Environment"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install -q --upgrade pip
"${VENV_DIR}/bin/pip" install -q -r "${APP_DIR}/requirements.txt"
chown -R "${APP_USER}:${APP_USER}" "${VENV_DIR}"
ok "Virtualenv ready: ${VENV_DIR}"

# ── Secrets ───────────────────────────────────────────────────────────────────
header "Writing Secrets"
mkdir -p "${APP_DIR}/config"

# Write credentials.json only if it does not already exist (protect re-runs).
if [[ ! -f "${APP_DIR}/config/credentials.json" ]]; then
    cat > "${APP_DIR}/config/credentials.json" <<JSON
{
    "client_id":     "${EVE_CLIENT_ID}",
    "client_secret": "${EVE_CLIENT_SECRET}"
}
JSON
    ok "Written config/credentials.json"
else
    warn "config/credentials.json already exists — skipping (delete to regenerate)"
fi

# Generate a stable FLASK_SECRET only on first install.
if [[ ! -f "${APP_DIR}/.env" ]]; then
    FLASK_SECRET="$("${VENV_DIR}/bin/python" -c "import secrets; print(secrets.token_hex(32))")"
    cat > "${APP_DIR}/.env" <<ENV
FLASK_SECRET=${FLASK_SECRET}
ENV
    ok "Written .env with new FLASK_SECRET"
else
    warn ".env already exists — skipping (existing secret preserved)"
fi

chmod 600 "${APP_DIR}/config/credentials.json" "${APP_DIR}/.env"
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/config/credentials.json" "${APP_DIR}/.env"

# ── Database ──────────────────────────────────────────────────────────────────
header "Database Initialisation"
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${APP_DIR}/init_db.py" \
    --db               "${APP_DIR}/mydatabase.db" \
    --webhook          "${DISCORD_WEBHOOK}" \
    --redirect-uri     "${REDIRECT_URI}" \
    --alliance-id      "${ALLIANCE_ID}" \
    --delivery-default "${DELIVERY_DEFAULT}" \
    --markup-pct       "${MARKUP_PCT}"
ok "Database ready: ${APP_DIR}/mydatabase.db"

# ── Log directory ─────────────────────────────────────────────────────────────
mkdir -p "${LOG_DIR}"
chown "${APP_USER}:${APP_USER}" "${LOG_DIR}"

# ── Systemd service ───────────────────────────────────────────────────────────
header "Systemd Service"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=Build Service (Flask/Gunicorn)
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/gunicorn \\
    --workers ${WORKERS} \\
    --threads ${THREADS} \\
    --bind ${GUNICORN_BIND} \\
    --timeout 60 \\
    --access-logfile ${LOG_DIR}/access.log \\
    --error-logfile  ${LOG_DIR}/error.log \\
    wsgi:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
ok "Service '${SERVICE_NAME}' enabled and started"

# ── Nginx ─────────────────────────────────────────────────────────────────────
header "Nginx Configuration"
NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"

cat > "${NGINX_CONF}" <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};

    # Security headers
    add_header X-Frame-Options        "SAMEORIGIN"                      always;
    add_header X-Content-Type-Options "nosniff"                         always;
    add_header Referrer-Policy        "strict-origin-when-cross-origin" always;

    # Static files served directly by Nginx (bypasses Gunicorn entirely)
    location /static/ {
        alias ${APP_DIR}/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # Proxy everything else to Gunicorn
    location / {
        proxy_pass          http://${GUNICORN_BIND};
        proxy_set_header    Host              \$host;
        proxy_set_header    X-Real-IP         \$remote_addr;
        proxy_set_header    X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header    X-Forwarded-Proto \$scheme;
        proxy_read_timeout  60s;
        proxy_send_timeout  60s;
        client_max_body_size 1m;
    }
}
NGINX

ln -sf "${NGINX_CONF}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
# Remove the default placeholder site if it is still symlinked
[[ -L /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
ok "Nginx configured for http://${DOMAIN}"

# ── SSL ───────────────────────────────────────────────────────────────────────
if [[ $SKIP_SSL -eq 0 ]]; then
    header "SSL Certificate (Let's Encrypt)"
    certbot --nginx --non-interactive --agree-tos \
        --email "${ADMIN_EMAIL}" -d "${DOMAIN}"
    systemctl reload nginx
    ok "SSL certificate issued for https://${DOMAIN}"
else
    warn "SSL skipped (--no-ssl). Configure TLS before going live."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║  Setup complete!                                   ║${NC}"
echo -e "${BOLD}${GREEN}╚════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  App URL    : ${CYAN}https://${DOMAIN}${NC}"
echo -e "  App files  : ${CYAN}${APP_DIR}/${NC}"
echo -e "  Logs       : ${CYAN}${LOG_DIR}/${NC}"
echo ""
echo -e "${YELLOW}ACTION REQUIRED — Register this OAuth redirect URI in your EVE developer app:${NC}"
echo -e "  ${GREEN}${REDIRECT_URI}${NC}"
echo -e "  Portal: ${CYAN}https://developers.eveonline.com/applications${NC}"
echo ""
echo -e "${YELLOW}OPTIONAL — Import EVE SDE for item search (inv_types / inv_groups):${NC}"
echo -e "  Download: ${CYAN}https://developers.eveonline.com/resource/resources${NC}"
echo ""
echo -e "Service management:"
echo -e "  sudo systemctl status|restart|stop ${SERVICE_NAME}"
echo -e "  sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
