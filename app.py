"""
Real VPS Panel
==============
Web UI that authenticates an admin and talks to the Host Agent (agent/main.py)
over HTTP to list, create, start, stop, and delete real KVM VMs.

Required environment variables:
  SECRET_KEY               random string used to sign session cookies
  PANEL_ADMIN_USER          admin username (default "admin")
  PANEL_ADMIN_PASSWORD_HASH bcrypt hash of the admin password
                             (generate with scripts/gen_password_hash.py)
  AGENT_URL                 base URL of the host agent, e.g. http://host:9000
  AGENT_TOKEN                shared secret, must match the agent's AGENT_TOKEN
"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.hash import bcrypt

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT))

SECRET_KEY = os.environ.get("SECRET_KEY", "")
ADMIN_USER = os.environ.get("PANEL_ADMIN_USER", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("PANEL_ADMIN_PASSWORD_HASH", "")
AGENT_URL = os.environ.get("AGENT_URL", "http://agent:9000")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")

app = FastAPI(title="Real VPS Panel")
serializer = URLSafeSerializer(SECRET_KEY or "dev-insecure-key-change-me", salt="session")
COOKIE_NAME = "vps_session"


def create_session_cookie(username: str) -> str:
    return serializer.dumps({"user": username})


def current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = serializer.loads(token)
    except BadSignature:
        return None
    return data.get("user")


def require_login(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


async def agent_request(method: str, path: str, **kwargs):
    headers = {"X-Agent-Token": AGENT_TOKEN}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, f"{AGENT_URL}{path}", headers=headers, **kwargs)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.json()


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not ADMIN_PASSWORD_HASH:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Server has no PANEL_ADMIN_PASSWORD_HASH configured."},
            status_code=500,
        )
    valid = username == ADMIN_USER and bcrypt.verify(password, ADMIN_PASSWORD_HASH)
    if not valid:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid credentials."}, status_code=401
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, create_session_cookie(username), httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(require_login)):
    error = None
    vms = []
    try:
        data = await agent_request("GET", "/vms")
        vms = data.get("vms", [])
    except HTTPException as e:
        error = f"Could not reach host agent: {e.detail}"
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "vms": vms, "error": error, "user": user}
    )


@app.post("/vms")
async def create_vm(
    request: Request,
    user: str = Depends(require_login),
    name: str = Form(...),
    vcpus: int = Form(1),
    memory_mb: int = Form(1024),
    disk_gb: int = Form(10),
    ssh_authorized_key: str = Form(""),
    password: str = Form(""),
):
    try:
        await agent_request(
            "POST",
            "/vms",
            json={
                "name": name,
                "vcpus": vcpus,
                "memory_mb": memory_mb,
                "disk_gb": disk_gb,
                "ssh_authorized_key": ssh_authorized_key,
                "password": password,
            },
        )
    except HTTPException as e:
        vms_data = {"vms": []}
        try:
            vms_data = await agent_request("GET", "/vms")
        except HTTPException:
            pass
        return templates.TemplateResponse(
            "dashboard.html",
            {"request": request, "vms": vms_data.get("vms", []), "error": e.detail, "user": user},
            status_code=e.status_code,
        )
    return RedirectResponse("/", status_code=303)


@app.post("/vms/{name}/start")
async def start_vm(name: str, user: str = Depends(require_login)):
    await agent_request("POST", f"/vms/{name}/start")
    return RedirectResponse("/", status_code=303)


@app.post("/vms/{name}/stop")
async def stop_vm(name: str, user: str = Depends(require_login)):
    await agent_request("POST", f"/vms/{name}/stop")
    return RedirectResponse("/", status_code=303)


@app.post("/vms/{name}/delete")
async def delete_vm(name: str, user: str = Depends(require_login)):
    await agent_request("DELETE", f"/vms/{name}")
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
