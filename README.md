# Real VPS Panel (flat layout)

All files sit directly in this one folder — no subfolders. `main.py` is the
Host Agent, `app.py` is the web Panel. Each has its own Dockerfile
(`Dockerfile.agent`, `Dockerfile`) and requirements file
(`requirements-agent.txt`, `requirements-panel.txt`), all built with the
repo root as build context.

The agent shells out to `virsh`, `qemu-img`, `virt-install`, and
`cloud-localds` to actually create, start, stop, and delete KVM virtual
machines. **It must run on a host with real `/dev/kvm` access** — a normal
unprivileged PaaS container cannot create real VMs.

## One-command setup (recommended)

On a single dedicated Ubuntu/Debian host with `/dev/kvm` available:

```bash
sudo ./install-all.sh
```

Installs KVM/libvirt + Docker, runs the agent as the `vps-agent` systemd
service, downloads an Ubuntu 22.04 cloud image as the VM base image,
generates all secrets, and builds + starts the panel container from
`Dockerfile` — printing the panel URL and admin login at the end.

## Manual setup (panel and agent on separate hosts)

### 1. Agent host (needs `/dev/kvm`)

```bash
sudo ./install-agent.sh
```

Prints a generated `AGENT_TOKEN`. Then download a base cloud image (the
script prints the exact command).

### 2. Panel (anywhere, e.g. a PaaS)

Build from **`Dockerfile`** with build context set to this folder's
root, port `8000`, and these environment variables:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | random string, signs session cookies |
| `PANEL_ADMIN_USER` | admin username (default `admin`) |
| `PANEL_ADMIN_PASSWORD_HASH` | bcrypt hash, generate with `gen_password_hash.py` |
| `AGENT_URL` | e.g. `http://<agent-host>:9000` |
| `AGENT_TOKEN` | must match the value install-agent.sh generated |

## Security notes

This is a working starter, not a hardened production panel: add HTTPS
termination, CSRF protection, and rate limiting on `/login` before exposing
it publicly. The shared `AGENT_TOKEN` is the only thing protecting the agent
API, so keep the panel-agent link on a private network if possible.
