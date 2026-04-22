#!/bin/bash
echo "Deploying to VPS..."
rsync -avz --exclude='.env' --exclude='*.db' \
  --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='*.pyc' --exclude='.git/' \
  --exclude='credentials/' \
  ~/projects/MarketingAgency/ \
  root@2.24.195.186:/opt/marts/app/
ssh root@2.24.195.186 'systemctl daemon-reload && systemctl restart marts'
echo "Done!"
