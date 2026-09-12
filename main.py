"""
VPS Host Agent
==============
Runs ON THE HYPERVISOR HOST (bare metal or a host with real /dev/kvm access),
NOT inside a normal unprivileged container. It shells out to virsh/qemu-img/
virt-install/cloud-localds -- the same tools host-installer/install-agent.sh
provisions -- to create, control, and destroy real KVM virtual machines.

Required environment variables:
  AGENT_TOKEN   shared secret the panel must send as `X-Agent-Token`
  BASE_IMAGE    path to a cloud image (e.g. Ubuntu/Debian cloud .img) used as
                the backing file for new VM disks
  IMAGES_DIR    directory to store per-VM disks/seed isos (default below)
  LIBVIRT_NET   libvirt network to attach VMs to (default "default")
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
BASE_IMAGE = os.environ.get("BASE_IMAGE", "/var/lib/libvirt/images/base/base.qcow2")
IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "/var/lib/libvirt/images"))
LIBVIRT_NET = os.environ.get("LIBVIRT_NET", "default")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}$")

app = FastAPI(title="VPS Host Agent")


def check_token(x_agent_token: str | None):
    if not AGENT_TOKEN:
        raise HTTPException(500, "AGENT_TOKEN is not configured on the agent host")
    if not x_agent_token or x_agent_token != AGENT_TOKEN:
        raise HTTPException(401, "invalid or missing X-Agent-Token")


def run(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def valid_name(name: str):
    if not NAME_RE.match(name):
        raise HTTPException(400, "invalid VM name (alnum, dot, dash, underscore only)")


class VMCreate(BaseModel):
    name: str
    vcpus: int = Field(default=1, ge=1, le=32)
    memory_mb: int = Field(default=1024, ge=512, le=131072)
    disk_gb: int = Field(default=10, ge=5, le=2000)
    ssh_authorized_key: str = ""
    password: str = ""


@app.get("/health")
def health():
    return {"status": "ok", "service": "vps-agent"}


@app.get("/vms")
def list_vms(x_agent_token: str | None = Header(default=None)):
    check_token(x_agent_token)
    out = run(["virsh", "list", "--all", "--name"], check=False)
    names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
    vms = []
    for name in names:
        state = run(["virsh", "domstate", name], check=False).stdout.strip()
        ip = get_ip(name)
        vms.append({"name": name, "state": state, "ip": ip})
    return {"vms": vms}


def get_ip(name: str) -> str | None:
    r = run(["virsh", "domifaddr", name, "--source", "agent"], check=False)
    if r.returncode != 0:
        r = run(["virsh", "domifaddr", name], check=False)
    for line in r.stdout.splitlines():
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})/\d+", line)
        if m:
            return m.group(1)
    return None


@app.get("/vms/{name}")
def get_vm(name: str, x_agent_token: str | None = Header(default=None)):
    check_token(x_agent_token)
    valid_name(name)
    r = run(["virsh", "dominfo", name], check=False)
    if r.returncode != 0:
        raise HTTPException(404, "vm not found")
    state = run(["virsh", "domstate", name], check=False).stdout.strip()
    return {"name": name, "state": state, "ip": get_ip(name), "dominfo": r.stdout}


@app.post("/vms")
def create_vm(vm: VMCreate, x_agent_token: str | None = Header(default=None)):
    check_token(x_agent_token)
    valid_name(vm.name)
    if not vm.ssh_authorized_key and not vm.password:
        raise HTTPException(400, "provide ssh_authorized_key and/or password")
    if not Path(BASE_IMAGE).exists():
        raise HTTPException(
            500, f"BASE_IMAGE not found at {BASE_IMAGE}; download a cloud image first"
        )

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = IMAGES_DIR / f"{vm.name}.qcow2"
    seed_path = IMAGES_DIR / f"{vm.name}-seed.iso"
    if disk_path.exists() or run(["virsh", "dominfo", vm.name], check=False).returncode == 0:
        raise HTTPException(409, "a VM with this name already exists")

    try:
        run([
            "qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
            "-b", BASE_IMAGE, str(disk_path), f"{vm.disk_gb}G",
        ])

        with tempfile.TemporaryDirectory() as td:
            user_data = Path(td) / "user-data"
            meta_data = Path(td) / "meta-data"
            lines = ["#cloud-config", f"hostname: {vm.name}", "chpasswd: {expire: false}"]
            if vm.password:
                lines += ["ssh_pwauth: true", f"password: {vm.password}"]
            if vm.ssh_authorized_key:
                lines += [
                    "users:",
                    "  - default",
                    "    ssh_authorized_keys:",
                    f"      - {vm.ssh_authorized_key}",
                ]
            user_data.write_text("\n".join(lines) + "\n")
            meta_data.write_text(f"instance-id: {vm.name}\nlocal-hostname: {vm.name}\n")
            run(["cloud-localds", str(seed_path), str(user_data), str(meta_data)])

        run([
            "virt-install",
            "--name", vm.name,
            "--memory", str(vm.memory_mb),
            "--vcpus", str(vm.vcpus),
            "--disk", f"path={disk_path},format=qcow2",
            "--disk", f"path={seed_path},device=cdrom",
            "--os-variant", "generic",
            "--network", f"network={LIBVIRT_NET}",
            "--import",
            "--graphics", "none",
            "--noautoconsole",
        ])
    except subprocess.CalledProcessError as e:
        for p in (disk_path, seed_path):
            if p.exists():
                p.unlink()
        raise HTTPException(500, f"provisioning failed: {e.stderr or e.stdout}")

    return {"name": vm.name, "state": "created"}


@app.post("/vms/{name}/start")
def start_vm(name: str, x_agent_token: str | None = Header(default=None)):
    check_token(x_agent_token)
    valid_name(name)
    r = run(["virsh", "start", name], check=False)
    if r.returncode != 0:
        raise HTTPException(400, r.stderr or "failed to start")
    return {"name": name, "state": "started"}


@app.post("/vms/{name}/stop")
def stop_vm(name: str, force: bool = False, x_agent_token: str | None = Header(default=None)):
    check_token(x_agent_token)
    valid_name(name)
    r = run(["virsh", "destroy" if force else "shutdown", name], check=False)
    if r.returncode != 0:
        raise HTTPException(400, r.stderr or "failed to stop")
    return {"name": name, "state": "stopping"}


@app.delete("/vms/{name}")
def delete_vm(name: str, x_agent_token: str | None = Header(default=None)):
    check_token(x_agent_token)
    valid_name(name)
    run(["virsh", "destroy", name], check=False)
    r = run(["virsh", "undefine", name, "--remove-all-storage"], check=False)
    for p in (IMAGES_DIR / f"{name}.qcow2", IMAGES_DIR / f"{name}-seed.iso"):
        if p.exists():
            p.unlink()
    if r.returncode != 0 and "failed to get domain" not in (r.stderr or ""):
        raise HTTPException(400, r.stderr or "failed to delete")
    return {"name": name, "state": "deleted"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
