#!/usr/bin/env bash
# Run this ON THE HYPERVISOR HOST as root. Installs KVM/libvirt tooling and
# runs main.py (the agent) as the "vps-agent" systemd service.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/vps-agent
IMAGES_DIR=/var/lib/libvirt/images

apt-get update
apt-get install -y \
  qemu-kvm libvirt-daemon-system libvirt-clients \
  cloud-image-utils genisoimage virtinst \
  python3 python3-venv python3-pip

if ! [ -e /dev/kvm ]; then
  echo "WARNING: /dev/kvm is not present. Real VMs cannot be created here." >&2
fi

systemctl enable --now libvirtd

mkdir -p "$INSTALL_DIR" "$IMAGES_DIR/base"
cp "$SCRIPT_DIR/main.py" "$INSTALL_DIR/main.py"
cp "$SCRIPT_DIR/requirements-agent.txt" "$INSTALL_DIR/requirements.txt"

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --no-cache-dir -r "$INSTALL_DIR/requirements.txt"

if [ ! -f "$INSTALL_DIR/.env" ]; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$INSTALL_DIR/.env" <<EOF
AGENT_TOKEN=$TOKEN
BASE_IMAGE=$IMAGES_DIR/base/base.qcow2
IMAGES_DIR=$IMAGES_DIR
LIBVIRT_NET=default
EOF
  echo "Generated a new AGENT_TOKEN in $INSTALL_DIR/.env"
  echo "Copy this same value into the panel's AGENT_TOKEN env var: $TOKEN"
fi

cp "$SCRIPT_DIR/vps-agent.service" /etc/systemd/system/vps-agent.service
systemctl daemon-reload
systemctl enable --now vps-agent

cat <<'EOF'

Agent installed and started as the "vps-agent" systemd service.

Still needed before creating VMs:
  1. Download a cloud image as the base for new VMs, e.g.:
       curl -L -o /var/lib/libvirt/images/base/base.qcow2 \
         https://cloud-images.ubuntu.com/releases/22.04/release/ubuntu-22.04-server-cloudimg-amd64.img
  2. Point the panel at this host: set AGENT_URL=http://<this-host>:9000
     and AGENT_TOKEN to the value printed above.

Check status with: systemctl status vps-agent
EOF
