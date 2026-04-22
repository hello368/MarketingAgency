# Development Environment

## Canonical Paths
- **Source (canonical):** /home/mikenam/projects/MarketingAgency/
- **Git repo:** /home/mikenam/projects/MarketingAgency/.git
- **Server CWD:** /home/mikenam/projects/MarketingAgency/tracker
- **venv:** /home/mikenam/projects/MarketingAgency/.venv
- **Logs:** /tmp/marts_server.log

## venv Warning
The `.venv` was originally created at `/mnt/c/Users/hsnam/projects/MarketingAgency/.venv`
and later copied to `/home/mikenam/...`. The `activate` script and `pyvenv.cfg` still
hardcode the original Windows path as `VIRTUAL_ENV`.

Impact: Python binary and pip packages load from `/mnt/c/Users/hsnam/...`, but source
code (CWD) loads correctly from `/home/mikenam/projects/MarketingAgency/tracker/`.
Code changes ARE reflected on server restart.

Long-term fix: recreate venv at Linux path.
```bash
# Requires server downtime
cd /home/mikenam/projects/MarketingAgency
python3 -m venv .venv.new
source .venv.new/bin/activate
pip install -r tracker/requirements.txt
mv .venv .venv.old && mv .venv.new .venv
# restart server
```

## Server Startup
```bash
tmux new-session -d -s marts \
  "cd ~/projects/MarketingAgency/tracker && source ../.venv/bin/activate && \
   uvicorn main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/marts_server.log"
```

## sys.path Order
`main.py` appends the parent directory (`/home/mikenam/projects/MarketingAgency/`) to
`sys.path`, so modules at the repo root are importable from tracker code.
Import resolution order: tracker/ → repo root → site-packages.

## Past Analysis Corrections
- 2026-04-21: "두 저장소 분리" 진단은 오진단이었음.
  서버는 /home/mikenam/ 에서만 코드를 읽음.
  /mnt/c/Users/hsnam/ 경로는 오래된 복사본 (venv만 해당, 소스코드 아님).
