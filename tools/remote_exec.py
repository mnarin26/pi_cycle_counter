import os
import sys

import paramiko


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: remote_exec.py <command>")
        return 2
    cmd = sys.argv[1]
    host = os.environ.get("PI_HOST", "100.92.41.26")
    user = os.environ.get("PI_USER", "pi")
    password = os.environ["PI_PASS"]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=12)
    _stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    if out:
        print(out.encode("ascii", errors="replace").decode("ascii"))
    if err:
        print(err.encode("ascii", errors="replace").decode("ascii"))
    code = stdout.channel.recv_exit_status()
    print(f"[exit={code}]")
    client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
