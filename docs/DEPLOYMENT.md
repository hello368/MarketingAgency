# Deployment Guide

## Architecture

| Environment | Host | Role |
|---|---|---|
| Production | VPS `2.24.195.186` | 24/7 MARTS bot |
| Development | Local PC | Claude Code + Cursor |

> `.env` and `credentials/` exist **only on VPS** — never committed to git.

---

## Deploy (4 steps)

### 1. Commit changes locally
```bash
git add .
git commit -m "message"
```

### 2. Transfer to VPS
```bash
rsync -avz --progress \
  --exclude='.env' \
  --exclude='*.db' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.git/' \
  --exclude='credentials/' \
  ~/projects/MarketingAgency/ \
  root@2.24.195.186:/opt/marts/app/
```

### 3. Restart service
```bash
ssh root@2.24.195.186 'systemctl restart marts'
```

### 4. Verify logs
```bash
ssh root@2.24.195.186 'tail -30 /var/log/marts.log'
```

---

## VPS Server Management

```bash
# Connect
ssh root@2.24.195.186

# Service control
systemctl status marts      # check status
systemctl restart marts     # restart
systemctl stop marts        # stop

# Logs
tail -f /var/log/marts.log  # live log stream
tail -30 /var/log/marts.log # last 30 lines
```

---

## Local Development

- Write and test code locally
- Commit to git, then deploy via `rsync`
- Never copy `.env` or `credentials/` to local — keep them VPS-only
- Michael inputs API keys and credentials directly on VPS (not through Claude Code)

---

## Secrets Management

| File | Location | Managed by |
|---|---|---|
| `.env` | VPS only | Michael (direct) |
| `credentials/` | VPS only | Michael (direct) |
| `service-account.json` | VPS only | Michael (direct) |

**Never commit secrets to git.**
