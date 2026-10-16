#!/usr/bin/env bash
# 独立公网入口：先 bootstrap 供 HTTP-01 验证，再由 activate 启用 HTTPS 登录。
# 依赖由管理员预装；本脚本不注册 ACME 账户、不生成密码、不修改 observer 或数据。
set -euo pipefail
umask 0027

fail() { printf '%s\n' "$*" >&2; exit 1; }
if [ "$#" != 2 ] || [[ "$1" != bootstrap && "$1" != activate ]]; then
  fail '用法：bash deploy/install-public-web.sh bootstrap|activate 公网IPv4'
fi
TASK_PHASE=$1
TASK_IP="$(/usr/bin/python3 - "$2" <<'PY'
import ipaddress
import sys
try:
    address = ipaddress.ip_address(sys.argv[1])
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError
except ValueError:
    sys.exit('需要规范的公网 IPv4 地址。')
print(address)
PY
)"
[ "$(id -u)" = 0 ] || fail '请使用 root 执行公网入口安装器。'
for TASK_BINARY in /usr/sbin/nginx /usr/bin/systemctl /usr/bin/flock /usr/bin/curl /usr/bin/openssl /usr/sbin/runuser; do
  [ -x "$TASK_BINARY" ] || fail "缺少预装依赖：$TASK_BINARY"
done
id nginx >/dev/null 2>&1 || fail '请先安装 nginx，确保 nginx 用户及组存在。'
getent group nginx >/dev/null || fail '缺少 nginx 组。'
exec 9>/run/lock/law-crawler-web.lock
/usr/bin/flock -n 9 || fail '公网入口安装或证书重载正在执行。'

TASK_CONFIG_DIR=/etc/law-crawler-web
TASK_CONFIG=$TASK_CONFIG_DIR/nginx.conf
TASK_AUTH=$TASK_CONFIG_DIR/htpasswd
TASK_ACME=/var/lib/law-crawler-web/acme
TASK_WEB_UNIT=/etc/systemd/system/law-crawler-web.service
TASK_RENEW_UNIT=/etc/systemd/system/law-crawler-cert-renew.service
TASK_TIMER_UNIT=/etc/systemd/system/law-crawler-cert-renew.timer
TASK_HOOK=/usr/local/sbin/law-crawler-web-reload
TASK_CERT=/etc/letsencrypt/live/law-crawler-web/fullchain.pem
TASK_KEY=/etc/letsencrypt/live/law-crawler-web/privkey.pem

if [ -e "$TASK_CONFIG" ] || [ -L "$TASK_CONFIG" ]; then
  [ -f "$TASK_CONFIG" ] && [ ! -L "$TASK_CONFIG" ] || fail '现有 nginx.conf 必须是普通文件。'
  head -n 1 "$TASK_CONFIG" | grep -q '^# law-crawler-web managed phase=' || fail '拒绝覆盖非本安装器管理的 nginx.conf。'
  if [ "$TASK_PHASE" = bootstrap ] && grep -Eq 'phase=activate|^[[:space:]]*listen[[:space:]]+443' "$TASK_CONFIG"; then
    fail '已有 HTTPS 配置；bootstrap 不会覆盖。请使用 activate 更新。'
  fi
fi

if [ "$TASK_PHASE" = activate ]; then
  [ -s "$TASK_AUTH" ] && [ -f "$TASK_AUTH" ] && [ ! -L "$TASK_AUTH" ] || fail '请先安全创建非空的 /etc/law-crawler-web/htpasswd。'
  [ "$(stat -c '%u:%G:%a' "$TASK_AUTH")" = '0:nginx:640' ] || fail 'htpasswd 必须属于 root:nginx，权限为 0640。'
  [ -s "$TASK_CERT" ] && [ -s "$TASK_KEY" ] || fail '缺少 law-crawler-web 的 fullchain.pem 或 privkey.pem。'
  /usr/bin/openssl x509 -in "$TASK_CERT" -noout -checkip "$TASK_IP" >/dev/null || fail '证书不覆盖指定公网 IP。'
  /usr/bin/openssl x509 -in "$TASK_CERT" -noout -checkend 0 >/dev/null || fail '证书已经到期。'
  [ -x /opt/law-crawler/certbot/bin/certbot ] || fail '请先在 /opt/law-crawler/certbot 安装 Certbot 5.4+。'
fi

install -d -o root -g nginx -m 0750 "$TASK_CONFIG_DIR" /var/log/law-crawler-web
install -d -o root -g nginx -m 0755 /run/law-crawler-web
install -d -o root -g root -m 0755 "$TASK_ACME/.well-known/acme-challenge"
for TASK_TEMP in client_body proxy fastcgi uwsgi scgi; do
  install -d -o nginx -g nginx -m 0700 "/run/law-crawler-web/$TASK_TEMP"
done
if [ "$TASK_PHASE" = activate ]; then
  /usr/sbin/runuser -u nginx -- test -r "$TASK_AUTH" || fail 'nginx 用户无法读取登录文件。'
fi

TASK_STAGE=$(mktemp -d "$TASK_CONFIG_DIR/.install.XXXXXX")
TASK_APPLYING=0
TASK_WEB_ACTIVE=0
TASK_WEB_ENABLED=0
TASK_TIMER_ACTIVE=0
TASK_TIMER_ENABLED=0
/usr/bin/systemctl is-active --quiet law-crawler-web.service && TASK_WEB_ACTIVE=1
/usr/bin/systemctl is-enabled --quiet law-crawler-web.service && TASK_WEB_ENABLED=1
/usr/bin/systemctl is-active --quiet law-crawler-cert-renew.timer && TASK_TIMER_ACTIVE=1
/usr/bin/systemctl is-enabled --quiet law-crawler-cert-renew.timer && TASK_TIMER_ENABLED=1
TASK_TARGETS=("$TASK_CONFIG" "$TASK_WEB_UNIT")
TASK_SOURCES=("$TASK_STAGE/nginx.conf" "$TASK_STAGE/web.service")
TASK_MODES=(0640 0644)
if [ "$TASK_PHASE" = activate ]; then
  TASK_TARGETS+=("$TASK_RENEW_UNIT" "$TASK_TIMER_UNIT" "$TASK_HOOK")
  TASK_SOURCES+=("$TASK_STAGE/renew.service" "$TASK_STAGE/renew.timer" "$TASK_STAGE/reload")
  TASK_MODES+=(0644 0644 0755)
fi

# 任一步失败都恢复切换前的配置、单元文件与启用/运行状态。
finish() {
  TASK_STATUS=$?
  trap - EXIT
  set +e
  if [ "$TASK_STATUS" != 0 ] && [ "$TASK_APPLYING" = 1 ]; then
    printf '%s\n' '安装失败，正在恢复原配置与服务状态。' >&2
    TASK_ROLLBACK_OK=1
    if [ "$TASK_PHASE" = activate ]; then
      /usr/bin/systemctl stop law-crawler-cert-renew.timer >/dev/null 2>&1
    fi
    for TASK_INDEX in "${!TASK_TARGETS[@]}"; do
      if [ -f "$TASK_STAGE/previous-$TASK_INDEX" ]; then
        cp -p "$TASK_STAGE/previous-$TASK_INDEX" "$TASK_STAGE/restore-$TASK_INDEX" &&
          mv -fT "$TASK_STAGE/restore-$TASK_INDEX" "${TASK_TARGETS[$TASK_INDEX]}" || TASK_ROLLBACK_OK=0
      else
        rm -f -- "${TASK_TARGETS[$TASK_INDEX]}" || TASK_ROLLBACK_OK=0
      fi
    done
    /usr/bin/systemctl daemon-reload || TASK_ROLLBACK_OK=0
    if [ "$TASK_WEB_ACTIVE" = 1 ]; then
      /usr/bin/systemctl reload law-crawler-web.service || /usr/bin/systemctl restart law-crawler-web.service || TASK_ROLLBACK_OK=0
    else
      /usr/bin/systemctl stop law-crawler-web.service || TASK_ROLLBACK_OK=0
    fi
    if [ "$TASK_WEB_ENABLED" = 0 ]; then
      /usr/bin/systemctl disable --quiet law-crawler-web.service >/dev/null 2>&1
    fi
    if [ "$TASK_PHASE" = activate ]; then
      if [ "$TASK_TIMER_ENABLED" = 0 ]; then
        /usr/bin/systemctl disable --quiet law-crawler-cert-renew.timer >/dev/null 2>&1
      fi
      if [ "$TASK_TIMER_ACTIVE" = 1 ]; then
        /usr/bin/systemctl start law-crawler-cert-renew.timer || TASK_ROLLBACK_OK=0
      fi
    fi
    if [ "$TASK_ROLLBACK_OK" = 1 ]; then
      printf '%s\n' '已恢复切换前配置与服务状态。' >&2
    else
      printf '%s\n' "自动恢复未完成；备份保留在 $TASK_STAGE，请立即检查服务。" >&2
      exit "$TASK_STATUS"
    fi
  fi
  rm -rf -- "$TASK_STAGE"
  exit "$TASK_STATUS"
}
trap finish EXIT

printf '# law-crawler-web managed phase=%s ip=%s\n' "$TASK_PHASE" "$TASK_IP" > "$TASK_STAGE/nginx.conf"
cat >> "$TASK_STAGE/nginx.conf" <<'NGINX'
user nginx nginx;
worker_processes 1;
pid /run/law-crawler-web/nginx.pid;
# nginx 错误日志可能包含原始 query/用户名；请求只保留下面的精简访问日志。
error_log /dev/null crit;
events { worker_connections 256; }
http {
    log_format public_web '$request_method $uri $status';
    access_log /var/log/law-crawler-web/access.log public_web;
    server_tokens off;
    default_type application/octet-stream;
    client_max_body_size 1k;
    client_header_timeout 15s;
    client_body_timeout 15s;
    keepalive_timeout 30s;
    send_timeout 300s;
    client_body_temp_path /run/law-crawler-web/client_body;
    proxy_temp_path /run/law-crawler-web/proxy;
    fastcgi_temp_path /run/law-crawler-web/fastcgi;
    uwsgi_temp_path /run/law-crawler-web/uwsgi;
    scgi_temp_path /run/law-crawler-web/scgi;
    map "$server_port:$http_host" $public_host_ok {
        default 0;
        "80:@PUBLIC_IP@" 1;
        "80:@PUBLIC_IP@:80" 1;
        "443:@PUBLIC_IP@" 1;
        "443:@PUBLIC_IP@:443" 1;
    }
    map $http_origin $public_origin_ok {
        default 0;
        "" 1;
        "https://@PUBLIC_IP@" 1;
    }
    server {
        listen 80 default_server;
        server_name @PUBLIC_IP@;
        if ($public_host_ok = 0) { return 400; }
        if ($public_origin_ok = 0) { return 403; }
        if ($request_method !~ ^(GET|HEAD)$) { return 405; }
        location ~ ^/\.well-known/acme-challenge/[A-Za-z0-9_-]+$ {
            root /var/lib/law-crawler-web/acme;
            disable_symlinks on;
            default_type text/plain;
            try_files $uri =404;
        }
        location / { return 308 https://@PUBLIC_IP@$request_uri; }
    }
NGINX
if [ "$TASK_PHASE" = activate ]; then
  cat >> "$TASK_STAGE/nginx.conf" <<'NGINX'
    server {
        listen 443 ssl default_server;
        server_name @PUBLIC_IP@;
        ssl_certificate /etc/letsencrypt/live/law-crawler-web/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/law-crawler-web/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:law_crawler_web:1m;
        ssl_session_tickets off;
        auth_basic "Law Crawler";
        auth_basic_user_file /etc/law-crawler-web/htpasswd;
        if ($public_host_ok = 0) { return 400; }
        if ($public_origin_ok = 0) { return 403; }
        if ($request_method !~ ^(GET|HEAD)$) { return 405; }
        location / {
            proxy_pass http://127.0.0.1:8765;
            proxy_http_version 1.1;
            proxy_pass_request_headers off;
            proxy_pass_request_body off;
            proxy_set_header Host "127.0.0.1:8765";
            proxy_set_header Connection "";
            proxy_set_header Content-Length "";
            proxy_set_header Origin "";
            proxy_set_header Authorization "";
            proxy_set_header Accept $http_accept;
            proxy_set_header Range $http_range;
            proxy_set_header If-Range $http_if_range;
            proxy_set_header If-Modified-Since $http_if_modified_since;
            proxy_set_header If-None-Match $http_if_none_match;
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_max_temp_file_size 0;
            proxy_connect_timeout 5s;
            proxy_read_timeout 300s;
            proxy_send_timeout 30s;
            proxy_redirect off;
        }
    }
NGINX
fi
printf '}\n' >> "$TASK_STAGE/nginx.conf"
sed -i "s/@PUBLIC_IP@/$TASK_IP/g" "$TASK_STAGE/nginx.conf"
chown root:nginx "$TASK_STAGE/nginx.conf"
chmod 0640 "$TASK_STAGE/nginx.conf"

cat > "$TASK_STAGE/web.service" <<'UNIT'
[Unit]
Description=Law crawler authenticated public web gateway
After=network.target law-crawler-observer.service
Wants=law-crawler-observer.service

[Service]
Type=simple
Group=nginx
ExecStartPre=/usr/bin/install -d -o nginx -g nginx -m 0700 /run/law-crawler-web/client_body /run/law-crawler-web/proxy /run/law-crawler-web/fastcgi /run/law-crawler-web/uwsgi /run/law-crawler-web/scgi
ExecStartPre=/usr/sbin/nginx -t -c /etc/law-crawler-web/nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/law-crawler-web/nginx.conf -g "daemon off;"
ExecReload=/usr/sbin/nginx -t -c /etc/law-crawler-web/nginx.conf
ExecReload=/bin/kill -s HUP $MAINPID
KillSignal=SIGQUIT
KillMode=mixed
TimeoutStopSec=30
Restart=on-failure
RestartSec=3
RuntimeDirectory=law-crawler-web
RuntimeDirectoryMode=0755
LogsDirectory=law-crawler-web
LogsDirectoryMode=0750
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
InaccessiblePaths=/var/lib/law-crawler
ReadWritePaths=/run/law-crawler-web /var/log/law-crawler-web
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0027
MemoryMax=96M
TasksMax=16
LimitNOFILE=2048

[Install]
WantedBy=multi-user.target
UNIT

if [ "$TASK_PHASE" = activate ]; then
  cat > "$TASK_STAGE/reload" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec 9>/run/lock/law-crawler-web.lock
/usr/bin/flock -w 60 9
/usr/sbin/nginx -t -c /etc/law-crawler-web/nginx.conf
/usr/bin/systemctl reload law-crawler-web.service
SH
  cat > "$TASK_STAGE/renew.service" <<'UNIT'
[Unit]
Description=Renew the law crawler public IP certificate
Wants=network-online.target
After=network-online.target law-crawler-web.service

[Service]
Type=oneshot
ExecStart=/opt/law-crawler/certbot/bin/certbot renew --cert-name law-crawler-web --quiet --no-random-sleep-on-renew --deploy-hook /usr/local/sbin/law-crawler-web-reload
PrivateTmp=true
NoNewPrivileges=true
UMask=0077
TimeoutStartSec=10min
UNIT
  cat > "$TASK_STAGE/renew.timer" <<'UNIT'
[Unit]
Description=Check the law crawler short-lived IP certificate twice daily

[Timer]
OnCalendar=*-*-* 00,12:15:00
RandomizedDelaySec=15min
Persistent=true
Unit=law-crawler-cert-renew.service

[Install]
WantedBy=timers.target
UNIT
fi

# 先验证候选完整配置；不依赖系统 nginx 默认站点或 conf.d。
/usr/sbin/nginx -t -c "$TASK_STAGE/nginx.conf"
for TASK_INDEX in "${!TASK_TARGETS[@]}"; do
  TASK_TARGET=${TASK_TARGETS[$TASK_INDEX]}
  if [ -e "$TASK_TARGET" ] || [ -L "$TASK_TARGET" ]; then
    [ -f "$TASK_TARGET" ] && [ ! -L "$TASK_TARGET" ] || fail "拒绝覆盖非普通文件：$TASK_TARGET"
    cp -p "$TASK_TARGET" "$TASK_STAGE/previous-$TASK_INDEX"
  fi
done
TASK_APPLYING=1
for TASK_INDEX in "${!TASK_TARGETS[@]}"; do
  if [ "$TASK_INDEX" != 0 ]; then
    install -o root -g root -m "${TASK_MODES[$TASK_INDEX]}" "${TASK_SOURCES[$TASK_INDEX]}" "${TASK_TARGETS[$TASK_INDEX]}"
  fi
done
mv -fT "$TASK_STAGE/nginx.conf" "$TASK_CONFIG"
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable --quiet law-crawler-web.service
if [ "$TASK_WEB_ACTIVE" = 1 ]; then
  /usr/bin/systemctl reload law-crawler-web.service
else
  /usr/bin/systemctl start law-crawler-web.service
fi

# HUP 异步返回；验证新监听已可用，否则由 EXIT trap 恢复旧版本。
TASK_SCHEME=http
TASK_PORT=80
TASK_EXPECTED=308
if [ "$TASK_PHASE" = activate ]; then
  TASK_SCHEME=https
  TASK_PORT=443
  TASK_EXPECTED=401
fi
TASK_READY=0
for TASK_ATTEMPT in {1..10}; do
  TASK_CODE="$(/usr/bin/curl --silent --output /dev/null --write-out '%{http_code}' --max-time 2 --noproxy '*' --resolve "$TASK_IP:$TASK_PORT:127.0.0.1" "$TASK_SCHEME://$TASK_IP/healthz" 2>/dev/null)" || TASK_CODE=
  if [ "$TASK_CODE" = "$TASK_EXPECTED" ]; then TASK_READY=1; break; fi
  sleep 1
done
[ "$TASK_READY" = 1 ] || fail '新入口未通过本机状态检查。'
if [ "$TASK_PHASE" = activate ]; then
  /usr/bin/systemctl enable --now --quiet law-crawler-cert-renew.timer
fi
TASK_APPLYING=0
printf '%s\n' "公网入口 $TASK_PHASE 完成：$TASK_SCHEME://$TASK_IP（状态 $TASK_EXPECTED）。"
