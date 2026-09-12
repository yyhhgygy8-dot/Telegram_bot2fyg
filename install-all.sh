#!/usr/bin/env bash
# One-shot installer. Run as root on a single dedicated Linux host that has
# real /dev/kvm access. Installs KVM/libvirt + Docker, runs the agent
# (main.py) as a systemd service, downloads a base cloud image, and builds +
# runs the panel container from Dockerfile in this same flat folder.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root, e.g.: sudo ./install-all.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR=/var/lib/libvirt/images
BASE_IMAGE_URL="https://cloud-images.ubuntu.com/releases/22.04/release/ubuntu-22.04-server-cloudimg-amd64.img"
BASE_IMAGE_PATH="$IMAGES_DIR/base/base.qcow2"
AGENT_INSTALL_DIR=/opt/vps-agent
PANEL_PORT=8000
AGENT_PORT=9000

echo "== 1/6: system packages =="
apt-get update
apt-get install -y \
  qemu-kvm libvirt-daemon-system libvirt-clients \
  cloud-image-utils genisoimage virtinst \
  python3 python3-venv python3-pip curl ca-certificates

if ! [ -e /dev/kvm ]; then
  echo "WARNING: /dev/kvm is not present on this machine. Packages will" >&2
  echo "install fine, but virt-install will fail later." >&2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "== installing Docker =="
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now libvirtd

echo "== 2/6: installing agent as a systemd service =="
mkdir -p "$AGENT_INSTALL_DIR" "$IMAGES_DIR/base"
cp "$SCRIPT_DIR/main.py" "$AGENT_INSTALL_DIR/main.py"
cp "$SCRIPT_DIR/requirements-agent.txt" "$AGENT_INSTALL_DIR/requirements.txt"
python3 -m venv "$AGENT_INSTALL_DIR/venv"
"$AGENT_INSTALL_DIR/venv/bin/pip" install --no-cache-dir -q \
  -r "$AGENT_INSTALL_DIR/requirements.txt" "passlib[bcrypt]"

if [ -f "$AGENT_INSTALL_DIR/.env" ]; then
  AGENT_TOKEN="$(grep '^AGENT_TOKEN=' "$AGENT_INSTALL_DIR/.env" | cut -d= -f2-)"
else
  AGENT_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$AGENT_INSTALL_DIR/.env" <<EOF
AGENT_TOKEN=$AGENT_TOKEN
BASE_IMAGE=$BASE_IMAGE_PATH
IMAGES_DIR=$IMAGES_DIR
LIBVIRT_NET=default
EOF
fi

cp "$SCRIPT_DIR/vps-agent.service" /etc/systemd/system/vps-agent.service
systemctl daemon-reload
systemctl enable --now vps-agent

echo "== 3/6: downloading base cloud image (skipped if already present) =="
if [ ! -f "$BASE_IMAGE_PATH" ]; then
  curl -L --progress-bar -o "$BASE_IMAGE_PATH" "$BASE_IMAGE_URL"
fi

echo "== 4/6: generating panel secrets =="
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
ADMIN_USER="admin"
ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
ADMIN_PASSWORD_HASH="$("$AGENT_INSTALL_DIR/venv/bin/python" -c \
  "from passlib.hash import bcrypt; print(bcrypt.hash('$ADMIN_PASSWORD'))")"

echo "== 5/6: building and starting the panel container =="
docker build -f "$SCRIPT_DIR/Dockerfile" -t real-vps-panel "$SCRIPT_DIR"
docker rm -f real-vps-panel >/dev/null 2>&1 || true
docker run -d --name real-vps-panel --restart unless-stopped \
  --network host \
  -e SECRET_KEY="$SECRET_KEY" \
  -e PANEL_ADMIN_USER="$ADMIN_USER" \
  -e PANEL_ADMIN_PASSWORD_HASH="$ADMIN_PASSWORD_HASH" \
  -e AGENT_URL="http://127.0.0.1:$AGENT_PORT" \
  -e AGENT_TOKEN="$AGENT_TOKEN" \
  real-vps-panel

echo "== 6/6: done =="
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

------------------------------------------------------------
Panel URL : http://${HOST_IP:-<this-server-ip>}:$PANEL_PORT
Username  : $ADMIN_USER
Password  : $ADMIN_PASSWORD
------------------------------------------------------------
This password is only shown here -- save it now.

Checks:
  systemctl status vps-agent
  docker logs -f real-vps-panel
  curl -s http://127.0.0.1:$AGENT_PORT/health
EOF
