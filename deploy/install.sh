#!/usr/bin/env bash
# 在已上传的源码目录执行；使用独立用户、持久数据目录与本机只读看板。
set -euo pipefail

if [ "$(id -u)" != 0 ]; then
  echo '请使用 root 执行部署安装器。' >&2
  exit 1
fi
TASK_CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TASK_USER=lawcrawler
TASK_HOME=/home/lawcrawler
TASK_BASE=/var/lib/law-crawler

if ! id "$TASK_USER" >/dev/null 2>&1; then
  useradd --create-home --home-dir "$TASK_HOME" --shell /bin/bash "$TASK_USER"
fi
install -d -m 0755 /opt/law-crawler
install -d -o "$TASK_USER" -g "$TASK_USER" -m 0700 "$TASK_BASE" "$TASK_BASE/workspace" "$TASK_BASE/downloads" "$TASK_BASE/runtime"

# 原子切换代码版本，下载资料与工作区始终在独立数据目录。
ln -sfn "$TASK_CODE_DIR" /opt/law-crawler/current.new
mv -Tf /opt/law-crawler/current.new /opt/law-crawler/current
chmod -R a+rX "$TASK_CODE_DIR"

if [ ! -x "$TASK_HOME/.local/bin/claude" ]; then
  runuser -u "$TASK_USER" -- bash -c 'set -euo pipefail; curl -fsSL --connect-timeout 20 --max-time 180 https://claude.ai/install.sh | bash -s stable'
fi
runuser -u "$TASK_USER" -- python3 "$TASK_CODE_DIR/deploy/tools/configure_server.py" \
  --workspace "$TASK_BASE/workspace" --download-base "$TASK_BASE/downloads" --runtime-dir "$TASK_BASE/runtime"

cat > /usr/local/bin/law-claude <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" = 0 ]; then
  exec runuser -u lawcrawler -- /usr/local/bin/law-claude "$@"
fi
if [ "$(id -un)" != lawcrawler ]; then
  echo '请使用 lawcrawler 用户运行，或由 root 执行 law-claude。' >&2
  exit 1
fi
cd /opt/law-crawler/current/web
exec /usr/bin/python3 -m observer.launch --binary /home/lawcrawler/.local/bin/claude --workspace /var/lib/law-crawler/workspace --runtime-dir /var/lib/law-crawler/runtime -- --permission-mode manual "$@"
SH
chmod 0755 /usr/local/bin/law-claude
if [ ! -e /usr/local/bin/claude ]; then
  ln -s /usr/local/bin/law-claude /usr/local/bin/claude
fi

cat > /etc/systemd/system/law-crawler-observer.service <<'UNIT'
[Unit]
Description=Law crawler read-only observer
After=network.target

[Service]
Type=simple
User=lawcrawler
Group=lawcrawler
WorkingDirectory=/opt/law-crawler/current/web
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m observer.server --workspace /var/lib/law-crawler/workspace --download-base /var/lib/law-crawler/downloads --runtime-dir /var/lib/law-crawler/runtime
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
MemoryMax=192M
TasksMax=32

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now law-crawler-observer.service
systemctl restart law-crawler-observer.service
TASK_READY=0
for TASK_ATTEMPT in {1..10}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8765/healthz >/dev/null 2>&1; then
    TASK_READY=1
    break
  fi
  sleep 1
done
if [ "$TASK_READY" != 1 ]; then
  echo '看板未通过本机健康检查，请查看 systemctl status law-crawler-observer。' >&2
  exit 1
fi
echo '部署完成。运行 law-claude；只读看板监听 127.0.0.1:8765。'
