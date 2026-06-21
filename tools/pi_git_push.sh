#!/bin/bash
set -euo pipefail
cd /home/pi/pi_cycle_counter
git config user.email "pi@rsp3b.local"
git config user.name "pi"
git add -A
/usr/bin/git commit -F /tmp/commitmsg.txt
/usr/bin/git push origin main
