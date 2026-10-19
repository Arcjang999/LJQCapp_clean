#!/usr/bin/env bash
# Run from an existing checkout: sudo bash scripts/deploy_cloud.sh PUBLIC_IP
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo 'Run this deployment script with sudo.' >&2
  exit 1
fi
QC_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
QC_HOST="${1:?Usage: sudo bash scripts/deploy_cloud.sh PUBLIC_IP_OR_HOSTNAME}"
if [[ ! "$QC_HOST" =~ ^[a-zA-Z0-9.-]+$ ]]; then
  echo 'The public host must be an IPv4 address or hostname.' >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv nginx fontconfig fonts-noto-cjk fonts-wqy-microhei poppler-utils
if ! id ljqc >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/ljqc --create-home --shell /usr/sbin/nologin ljqc
fi
install -d -m 0750 -o ljqc -g ljqc /var/lib/ljqc /var/lib/ljqc/backups /var/cache/ljqc /var/cache/ljqc/matplotlib
if [[ ! -x "$QC_ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$QC_ROOT/.venv"
fi
"$QC_ROOT/.venv/bin/pip" install --disable-pip-version-check -r "$QC_ROOT/requirements-cloud.txt"

# Keep mutable records outside the Git checkout. Never overwrite an existing location.
QC_ROOT="$QC_ROOT" python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ['QC_ROOT'])
config = root / 'data/storage_config.json'
expected = '/var/lib/ljqc/qc_lj_app.db'
if config.exists():
    actual = json.loads(config.read_text()).get('database_path')
    if actual != expected:
        raise SystemExit(f'Existing database configuration differs: {actual}; deployment stopped.')
else:
    if (root / 'data/qc_lj_app.db').exists():
        raise SystemExit('An existing checkout database needs an explicit migration; deployment stopped.')
    config.write_text(json.dumps({'database_path': expected}) + '\n')
backup = root / 'data/backups'
if not backup.exists() and not backup.is_symlink():
    backup.symlink_to('/var/lib/ljqc/backups', target_is_directory=True)
elif backup.resolve() != Path('/var/lib/ljqc/backups'):
    raise SystemExit('Existing backup directory differs; deployment stopped.')
PY

cat > /etc/systemd/system/ljqcapp.service <<EOF
[Unit]
Description=LJQC laboratory quality control
After=network.target

[Service]
Type=simple
User=ljqc
Group=ljqc
WorkingDirectory=$QC_ROOT
Environment=LJQC_DEPLOYMENT_MODE=cloud
Environment=MPLBACKEND=Agg
Environment=MPLCONFIGDIR=/var/cache/ljqc/matplotlib
Environment=XDG_CACHE_HOME=/var/cache/ljqc
Environment=PYTHONUNBUFFERED=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=1
Environment=TZ=Asia/Shanghai
UMask=0027
ExecStart=$QC_ROOT/.venv/bin/python -m streamlit run $QC_ROOT/app.py --server.headless=true --server.address=127.0.0.1 --server.port=8501 --server.fileWatcherType=none --browser.gatherUsageStats=false --client.showErrorDetails=false
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/sites-available/ljqcapp <<EOF
server {
    listen 80;
    server_name $QC_HOST;
    client_max_body_size 200m;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
        proxy_buffering off;
    }
}
EOF
ln -sfn /etc/nginx/sites-available/ljqcapp /etc/nginx/sites-enabled/ljqcapp
nginx -t
systemctl daemon-reload
systemctl enable --now ljqcapp nginx
systemctl restart ljqcapp
systemctl reload nginx
for QC_ATTEMPT in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8501/_stcore/health; then
    echo
    echo "LJQC is ready at http://$QC_HOST"
    exit 0
  fi
  sleep 1
done
journalctl -u ljqcapp --no-pager -n 40
exit 1
