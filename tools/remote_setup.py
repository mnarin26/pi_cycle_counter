import os
import stat
from pathlib import Path

import paramiko


HOST = os.environ.get("PI_HOST", "100.92.41.26")
USER = os.environ.get("PI_USER", "pi")
PASSWORD = os.environ["PI_PASS"]
LOCAL_ROOT = Path(os.environ.get("LOCAL_ROOT", r"c:\Users\Bilgisayar02\injection-monitor"))
REMOTE_ROOT = os.environ.get("REMOTE_ROOT", "/home/pi/injection-monitor")

EXCLUDES = {".git", "node_modules", "__pycache__", ".venv", "data", "logs", ".cursor"}
INCLUDE_ROOTS = {"backend", "frontend", "deploy", "README.md", ".gitignore"}


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    parts = remote_path.strip("/").split("/")
    cur = ""
    for part in parts:
        cur += f"/{part}"
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def upload_tree(sftp: paramiko.SFTPClient, local_root: Path, remote_root: str) -> None:
    ensure_remote_dir(sftp, remote_root)
    files = []
    for p in local_root.rglob("*"):
        rel = p.relative_to(local_root)
        if rel.parts and rel.parts[0] not in INCLUDE_ROOTS:
            continue
        if any(part in EXCLUDES for part in rel.parts):
            continue
        files.append(p)

    sent = 0
    for p in files:
        rel = p.relative_to(local_root)
        remote_path = f"{remote_root}/{rel.as_posix()}"
        if p.is_dir():
            ensure_remote_dir(sftp, remote_path)
            continue
        ensure_remote_dir(sftp, f"{remote_root}/{rel.parent.as_posix()}")
        sftp.put(str(p), remote_path)
        sent += 1
        if sent % 30 == 0:
            print(f"Uploaded {sent} files...")


def run(ssh: paramiko.SSHClient, cmd: str) -> int:
    def safe_print(text: str) -> None:
        print(text.encode("ascii", errors="replace").decode("ascii"))

    print(f"\n$ {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    if out.strip():
        safe_print(out)
    if err.strip():
        safe_print(err)
    code = stdout.channel.recv_exit_status()
    print(f"[exit={code}]")
    return code


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

    sftp = ssh.open_sftp()
    upload_tree(sftp, LOCAL_ROOT, REMOTE_ROOT)
    sftp.close()
    print("Upload completed.")

    cmds = [
        f"cd {REMOTE_ROOT}/backend && python3 -m venv .venv",
        f"cd {REMOTE_ROOT}/backend && . .venv/bin/activate && pip install -U pip",
        f"cd {REMOTE_ROOT}/backend && . .venv/bin/activate && pip install -r requirements.txt",
        f"cd {REMOTE_ROOT}/backend && . .venv/bin/activate && python -c \"from app.main import app; print('ok', app.title)\"",
        f"cd {REMOTE_ROOT}/frontend && (npm ci || npm install) && npm run build",
        f"echo '{PASSWORD}' | sudo -S cp {REMOTE_ROOT}/deploy/systemd/injection-monitor.service /etc/systemd/system/injection-monitor.service",
        f"echo '{PASSWORD}' | sudo -S cp {REMOTE_ROOT}/deploy/systemd/injection-monitor-admin.service /etc/systemd/system/injection-monitor-admin.service",
        f"echo '{PASSWORD}' | sudo -S systemctl daemon-reload",
        f"echo '{PASSWORD}' | sudo -S systemctl enable --now injection-monitor.service",
        f"echo '{PASSWORD}' | sudo -S systemctl enable --now injection-monitor-admin.service",
        f"echo '{PASSWORD}' | sudo -S systemctl status injection-monitor.service --no-pager -l | head -n 20",
        f"echo '{PASSWORD}' | sudo -S systemctl status injection-monitor-admin.service --no-pager -l | head -n 20",
    ]
    for cmd in cmds:
        code = run(ssh, cmd)
        if code != 0 and "npm" in cmd:
            print("Frontend build skipped/failed; continuing.")
            continue
        if code != 0:
            raise SystemExit(code)

    ssh.close()


if __name__ == "__main__":
    main()
