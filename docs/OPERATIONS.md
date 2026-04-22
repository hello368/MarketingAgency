# Operations Manual

## Server Lifecycle

### Start (tmux session)
```bash
tmux new-session -d -s marts \
  'cd ~/projects/MarketingAgency/tracker && \
   source ../.venv/bin/activate && \
   uvicorn main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/marts_server.log'
```

### View live logs
```bash
tmux attach -t marts
# Exit without killing: Ctrl+B then D
```

### Stop
```bash
tmux kill-session -t marts
```

### Restart (full cycle)
```bash
tmux kill-session -t marts 2>/dev/null
sleep 3
# Then run Start command above
```

### Check status
```bash
tmux ls                                    # Session exists?
ps aux | grep uvicorn | grep -v grep       # Process running?
curl -s http://localhost:8000/health       # Responding?
tail -20 /tmp/marts_server.log             # Recent logs
```

## Notes

- WSL: tmux session survives SSH disconnects but dies if WSL itself shuts down
- After PC restart: run Start command manually
- Consider systemd user service for auto-start (see TODO.md)
