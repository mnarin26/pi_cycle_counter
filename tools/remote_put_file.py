import os
import sys

import paramiko


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: remote_put_file.py <local_path> <remote_path>")
        return 2
    local_path = sys.argv[1]
    remote_path = sys.argv[2]
    host = os.environ.get("PI_HOST", "100.92.41.26")
    user = os.environ.get("PI_USER", "pi")
    password = os.environ["PI_PASS"]

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=password, timeout=12)
    sftp = c.open_sftp()
    sftp.put(local_path, remote_path)
    sftp.close()
    c.close()
    print("uploaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
