#!/usr/bin/env python3
"""配置独立、仅下载的 Linux systemd worker；不安装模型、凭据或启动服务。"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or any(ord(c) < 32 for c in str(path)):
        raise ValueError('必须提供不含控制字符的绝对路径')
    for node in [path, *path.parents]:
        if node.is_symlink():
            raise ValueError('下载部署路径不得包含符号链接: ' + str(node))
    return path


def source_hash(release):
    skill = release / 'skill/law-crawler'
    files = [p for p in skill.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    if not files:
        raise ValueError('release 缺少完整canonical skill源码')
    rows = []
    for path in sorted(files):
        absolute(path)
        rows.append(str(path.relative_to(skill)) + ':' + digest(path.read_bytes()))
    return digest('\n'.join(rows).encode())


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def scalar_path(value):
    # WorkingDirectory是单一路径标量，不会移除ExecStart风格的token引号。
    text = str(value)
    if text != text.strip() or '\\' in text:
        raise ValueError('systemd工作目录不得以空白结尾或包含反斜杠')
    return text.replace('%', '%%')


def build(release, workspace, download_base, jurisdiction, batch_id, platforms=None, user='lawcrawler',
          python='/usr/bin/python3', min_free_gib=5, max_pages=1000, timeout=15, delay=.25):
    release, workspace, download_base = map(absolute, (release, workspace, download_base))
    # Linux的/usr/bin/python3通常是系统维护的符号链接；冻结其真实可执行文件。
    python = Path(python).expanduser().resolve(strict=True)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('Python解释器必须为可执行文件')
    try:
        checked = subprocess.run([str(python), '-I', '-c', 'import json,sys,ssl,sqlite3; print(json.dumps(list(sys.version_info[:2])))'],
            capture_output=True, text=True, timeout=15, check=True)
        runtime = json.loads(checked.stdout)
        if not isinstance(runtime, list) or len(runtime) != 2 or tuple(runtime) < (3, 10):
            raise ValueError('版本不足')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError('下载worker需要Python>=3.10及标准库ssl/sqlite3；请用--python指定') from exc
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('无效服务用户名称')
    for label, name in (('jurisdiction', jurisdiction), ('batch-id', batch_id)):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
            raise ValueError(label + ' 必须为安全目录名；法域使用countries中的规范目录名')
    if not all(math.isfinite(n) for n in (min_free_gib, max_pages, timeout, delay)) or min_free_gib < 1 or max_pages < 1 or timeout <= 0 or delay < 0:
        raise ValueError('磁盘、分页、超时须为正数，延时不得为负')
    for left, right in ((workspace, release), (download_base, release), (workspace, download_base)):
        if left == right or left in right.parents or right in left.parents:
            raise ValueError('release、workspace和download-base必须互相独立，不能重叠')
    reserved = {'/', '/etc', '/usr', '/var', '/opt', '/home', '/root', '/tmp', '/var/lib'}
    if str(workspace) in reserved or str(download_base) in reserved:
        raise ValueError('workspace和download-base不得使用系统根目录')
    source = release / 'skill/law-crawler/countries' / jurisdiction / 'index.md'
    absolute(source)
    content = source.read_bytes()
    registered = list(dict.fromkeys(urlsplit(url).hostname.removeprefix('www.') for url in
        re.findall(r'\]\((https?://[^\s)]+)\)', content.decode('utf-8-sig')) if urlsplit(url).hostname))
    selected = list(dict.fromkeys(platforms or registered))
    if not selected or set(selected) - set(registered):
        raise ValueError('平台必须在所选法域索引中登记')
    script = release / 'skill/law-crawler/scripts/bulk_archive.py'
    runner = release / 'deploy/tools/configure_downloader.py'
    absolute(script); absolute(runner)
    if not script.is_file() or not runner.is_file():
        raise ValueError('release 缺少canonical bulk_archive或download-only部署工具')
    config = ('downdloadDir=' + str(download_base) + '\n').encode()
    plan = {'version': 1, 'mode': 'download-only', 'release': str(release), 'workspace': str(workspace),
            'download_base': str(download_base), 'jurisdiction': jurisdiction, 'batch_id': batch_id,
            'platforms': selected, 'user': user, 'python': str(python), 'python_runtime': runtime, 'min_free_bytes': min_free_gib * 1024**3,
            'max_pages': max_pages, 'timeout': timeout, 'delay': delay,
            'index_sha256': digest(content), 'config_sha256': digest(config),
            'source_tree_sha256': source_hash(release), 'runner_sha256': digest(runner.read_bytes())}
    name = 'law-crawler-download-' + jurisdiction.lower() + '-' + batch_id + '.service'
    plan_path = workspace / 'downloader-deployment-plan.json'
    command = [python, runner, '--run-plan', plan_path, '--expected-plan-sha', digest(encoded(plan))]
    unit = '\n'.join([
        '[Unit]', 'Description=Official legislation raw archive ' + jurisdiction + ' ' + batch_id,
        'Wants=network-online.target', 'After=network-online.target', '', '[Service]', 'Type=simple',
        'User=' + user, 'WorkingDirectory=' + scalar_path(workspace),
        'ExecStart=' + ' '.join(map(quote, command)), 'Restart=no', 'KillSignal=SIGINT', 'KillMode=control-group',
        'TimeoutStopSec=45', 'UMask=0077', 'NoNewPrivileges=yes', 'PrivateTmp=yes',
        'ProtectSystem=strict', 'ProtectHome=read-only', 'ReadWritePaths=' + quote(workspace) + ' ' + quote(download_base),
        'MemoryMax=512M', 'TasksMax=32', 'LimitNOFILE=256', 'RuntimeMaxSec=2d',
        'Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1',
        'StandardOutput=journal', 'StandardError=journal', '', '[Install]', 'WantedBy=multi-user.target', ''])
    return plan, name, unit, content, config


def same_or_missing(path, data):
    absolute(path)
    if path.exists() and (not path.is_file() or path.read_bytes() != data):
        raise ValueError('拒绝覆写不一致的已有配置或计划: ' + str(path))


def check_bulk_plan(plan):
    country = Path(plan['download_base']) / plan['jurisdiction']
    existing = country / 'pipeline/bulk' / plan['batch_id'] / 'plan.json'
    if existing.exists():
        value = json.loads(existing.read_text())
        expected = {'jurisdiction': plan['jurisdiction'], 'batch_id': plan['batch_id'], 'country_directory': str(country),
                    'index_sha256': plan['index_sha256'], 'platforms': plan['platforms'], 'max_pages': plan['max_pages'], 'delay': plan['delay']}
        if any(value.get(k) != v for k, v in expected.items()):
            raise ValueError('已有下载批次计划与服务器部署配置不一致；使用独立新batch-id')
        signature = existing.with_name('plan.sha256')
        if not signature.is_file() or signature.read_text().strip() != digest(existing.read_bytes()):
            raise ValueError('已有下载批次冻结计划SHA校验失败')
        if value.get('retry') != {'timeout': plan['timeout'], 'max_bytes': 40*1024**2, 'max_attempts': 3, 'backoff_base': 1}:
            raise ValueError('已有下载批次重试配置不一致')
    return existing


def accessible(path, owner, bits):
    if owner.pw_uid == 0:
        return True
    info = path.stat()
    groups = os.getgrouplist(owner.pw_name, owner.pw_gid)
    shift = 6 if info.st_uid == owner.pw_uid else (3 if info.st_gid in groups else 0)
    return ((info.st_mode >> shift) & bits) == bits


def permission_preflight(plan, owner):
    release = Path(plan['release'])
    runner = release / 'deploy/tools/configure_downloader.py'
    entry = release / 'skill/law-crawler/scripts/bulk_archive.py'
    for target in (runner, entry, Path(plan['python']), Path(plan['workspace']), Path(plan['download_base'])):
        for path in target.parents:
            if path.exists() and not accessible(path, owner, 1):
                raise ValueError('专用用户无法遍历运行目录父链；使用可访问的独立目录: ' + str(path))
    for path in (runner, entry, Path(plan['python'])):
        if not accessible(path, owner, 5 if path == Path(plan['python']) else 4):
            raise ValueError('专用用户无法读取运行入口或执行解释器: ' + str(path))
    skill = release / 'skill/law-crawler'
    if not accessible(skill, owner, 5):
        raise ValueError('专用用户无法读取pinned skill根目录: ' + str(skill))
    for path in skill.rglob('*'):
        if not accessible(path, owner, 5 if path.is_dir() else 4):
            raise ValueError('专用用户无法读取pinned skill源码: ' + str(path))
    for base in (Path(plan['download_base']),):
        if base.exists():
            for path in base.rglob('*'):
                absolute(path)
                if not accessible(path, owner, 7 if path.is_dir() else 6):
                    raise ValueError('专用下载目录内存在不可访问的旧数据；仅对该独立目录人工核对属主，不自动递归修改: ' + str(path))


def install(plan, name, unit, index, config, unit_directory=Path('/etc/systemd/system')):
    owner = pwd.getpwnam(plan['user'])
    workspace, download_base = Path(plan['workspace']), Path(plan['download_base'])
    unit_path = absolute(Path(unit_directory)) / name
    if workspace.exists() and any(workspace.iterdir()) and not (workspace / 'downloader-deployment-plan.json').exists():
        raise ValueError('拒绝把已有非下载workspace当作部署快照')
    files = {workspace / 'countries' / plan['jurisdiction'] / 'index.md': index,
             workspace / 'countries/global-config.properties': config,
             workspace / 'downloader-deployment-plan.json': encoded(plan), unit_path: unit.encode()}
    permission_preflight(plan, owner)
    for path, data in files.items():
        same_or_missing(path, data)
    check_bulk_plan(plan)
    for path in (workspace, download_base):
        if path.exists() and path.stat().st_uid not in (owner.pw_uid, os.getuid()):
            raise ValueError('已有目录属于其他用户，拒绝更改所有权: ' + str(path))
    for leaf in (workspace, download_base):
        missing = [parent for parent in leaf.parents if not parent.exists()]
        for parent in reversed(missing):
            parent.mkdir(mode=0o755)
            os.chmod(parent, 0o755)  # 显式消除root的严格umask造成新父目录不可遍历。
    for path in (workspace, workspace / 'countries', workspace / 'countries' / plan['jurisdiction'], download_base):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700); os.chown(path, owner.pw_uid, owner.pw_gid)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
        os.chmod(path, 0o644 if path == unit_path else 0o600)
        os.chown(path, os.getuid() if path == unit_path else owner.pw_uid, os.getgid() if path == unit_path else owner.pw_gid)
    return unit_path


def worker_command(plan):
    existing = check_bulk_plan(plan)
    args = [plan['python'], str(Path(plan['release']) / 'skill/law-crawler/scripts/bulk_archive.py'),
            '--root', plan['workspace'], '--jurisdiction', plan['jurisdiction'],
            '--config', str(Path(plan['workspace']) / 'countries/global-config.properties'),
            '--rerun-batch' if existing.exists() else '--batch-id', plan['batch_id'], '--phase', 'all',
            '--max-pages', str(plan['max_pages']), '--timeout', str(plan['timeout']), '--delay', str(plan['delay'])]
    for platform in plan['platforms']:
        args += ['--platform', platform]
    return args


def run_worker(path, expected_sha, execute=True):
    path = absolute(path)
    data = path.read_bytes()
    if digest(data) != expected_sha:
        raise ValueError('下载部署计划SHA校验失败')
    plan = json.loads(data)
    if plan.get('mode') != 'download-only':
        raise ValueError('仅允许download-only部署计划')
    if os.getuid() != pwd.getpwnam(plan['user']).pw_uid:
        raise ValueError('必须以冻结计划中的专用下载用户执行')
    release, workspace, base = map(absolute, (plan['release'], plan['workspace'], plan['download_base']))
    for target, key in ((workspace / 'countries' / plan['jurisdiction'] / 'index.md', 'index_sha256'),
                        (workspace / 'countries/global-config.properties', 'config_sha256'),
                        (release / 'deploy/tools/configure_downloader.py', 'runner_sha256')):
        if digest(target.read_bytes()) != plan[key]:
            raise ValueError('下载worker冻结输入发生变化: ' + str(target))
    if source_hash(release) != plan['source_tree_sha256']:
        raise ValueError('pinned release skill源码发生变化')
    if shutil.disk_usage(base).free < plan['min_free_bytes']:
        raise ValueError('可用磁盘低于冻结下限；保留断点且不启动下载')
    command = worker_command(plan)
    if execute:
        os.execv(command[0], command)
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--download-base', type=Path)
    parser.add_argument('--jurisdiction')
    parser.add_argument('--batch-id')
    parser.add_argument('--platform', action='append')
    parser.add_argument('--user', default='lawcrawler')
    parser.add_argument('--python', default='/usr/bin/python3')
    parser.add_argument('--min-free-gib', type=int, default=5)
    parser.add_argument('--max-pages', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=15)
    parser.add_argument('--delay', type=float, default=.25)
    parser.add_argument('--unit-dir', type=Path, default=Path('/etc/systemd/system'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--run-plan', type=Path)
    parser.add_argument('--expected-plan-sha')
    args = parser.parse_args(argv)
    try:
        if args.run_plan:
            if not args.expected_plan_sha:
                raise ValueError('--run-plan必须提供systemd冻结的--expected-plan-sha')
            run_worker(args.run_plan, args.expected_plan_sha)
            return 0
        if not all((args.release, args.workspace, args.download_base, args.jurisdiction, args.batch_id)):
            raise ValueError('必须提供release、workspace、download-base、jurisdiction及batch-id')
        plan, name, unit, index, config = build(args.release, args.workspace, args.download_base, args.jurisdiction,
            args.batch_id, args.platform, args.user, args.python, args.min_free_gib, args.max_pages, args.timeout, args.delay)
        if args.dry_run:
            print(json.dumps({'plan': plan, 'unit_name': name, 'unit': unit, 'install_started': False,
                'next_commands': ['systemctl daemon-reload', 'systemctl start ' + name]}, ensure_ascii=False, indent=2))
        else:
            if sys.platform != 'linux' or os.getuid() != 0:
                raise ValueError('实际安装须在Linux以root执行；本机使用--dry-run')
            unit_path = install(plan, name, unit, index, config, args.unit_dir)
            print(json.dumps({'unit': str(unit_path), 'started': False, 'next_commands': [
                'systemctl daemon-reload', 'systemctl start ' + name, 'journalctl -u ' + name + ' -f']}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('下载部署配置失败：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
