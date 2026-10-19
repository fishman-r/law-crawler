#!/usr/bin/env python3
"""将工程旧 downloads/reports/tmp 归入 data；默认只读，--apply 才迁移。"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skill'))
from law_crawler.file_lock import FileLock


KINDS = ('downloads', 'reports', 'tmp')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('数据中存在符号链接，停止迁移: ' + str(path))
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = sha256(path)
    return result


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def rewrite_database(path, old_country, new_country, backup):
    """只改可重建的 objects.path；原件证据、请求计数及版本行保持原样。"""
    connection = sqlite3.connect(path)
    try:
        before = {table: connection.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall()
                  for table in ('objects', 'requests', 'versions')}
        updates = []
        for row in before['objects']:
            original = Path(row[1])
            if not original.is_absolute() or '..' in original.parts:
                raise ValueError('数据库中存在未识别的文件路径')
            try:
                relative = original.relative_to(old_country)
            except ValueError:
                raise ValueError('数据库文件路径不属于旧国家目录') from None
            new_path = new_country / relative
            if not new_path.is_file() or new_path.is_symlink() or not new_path.resolve().is_relative_to(new_country):
                raise ValueError('数据库目标文件缺失或越出新国家目录')
            updates.append((str(new_path), row[0]))
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as copy:
            connection.backup(copy)
        with connection:
            connection.executemany('UPDATE objects SET path=? WHERE sha256=?', updates)
            for table in ('requests', 'versions'):
                if connection.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall() != before[table]:
                    raise ValueError('数据库请求或版本记录意外改变')
            objects = connection.execute('SELECT * FROM objects ORDER BY 1,2').fetchall()
            if [(r[0], r[2]) for r in objects] != [(r[0], r[2]) for r in before['objects']]:
                raise ValueError('数据库原始元数据意外改变')
        return len(updates)
    finally:
        connection.close()


def migrate(root=ROOT, apply=False):
    root = Path(root).expanduser().resolve()
    data = root / 'data'
    if data.is_symlink():
        raise ValueError('data 不得为符号链接')
    sources = {kind: root / kind for kind in KINDS if (root / kind).exists()}
    manifest_path = data / 'downloads/.relocation.json'
    if not sources:
        return dict(status='already_migrated' if manifest_path.is_file() else 'nothing_to_migrate')
    for kind, source in sources.items():
        if source.is_symlink() or not source.is_dir():
            raise ValueError('旧数据目录必须是普通目录')
        if (data / kind).exists() or (data / kind).is_symlink():
            raise ValueError('新旧数据目录同时存在，停止以避免合并或覆盖: ' + kind)
    previews = {kind: dict(files=sum(p.is_file() for p in source.rglob('*')),
                           bytes=sum(p.stat().st_size for p in source.rglob('*') if p.is_file()))
                for kind, source in sources.items()}
    if not apply:
        return dict(status='dry_run', directories=previews)

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    moved, backups = [], []
    with ExitStack() as locks:
        backup_root = Path(locks.enter_context(tempfile.TemporaryDirectory(prefix='law-crawler-migration-')))
        downloads = sources.get('downloads')
        if downloads:
            lock_paths = [downloads / '.crawler.lock']
            lock_paths += list(downloads.glob('*/.crawler.lock'))
            lock_paths += list(downloads.glob('.batches/*/.execution.lock'))
            for lock in lock_paths:
                if lock.is_symlink():
                    raise ValueError('锁文件不能是符号链接')
                locks.enter_context(FileLock(lock, '采集数据正在使用，停止目录迁移'))
        before = {kind: snapshot(source) for kind, source in sources.items()}
        plans = {p.parent.name: sha256(p) for p in (root / 'downloads/.batches').glob('*/plan.json')}
        manifest = dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                        old_download_base=str(root / 'downloads'), download_base=str(data / 'downloads'),
                        roots=[dict(kind=kind, before=str(root / kind), after=str(data / kind)) for kind in KINDS],
                        batch_plans=plans)
        data.mkdir(exist_ok=True)
        try:
            for kind, source in sources.items():
                target = data / kind
                source.rename(target)
                moved.append((source, target))
                if snapshot(target) != before[kind]:
                    raise ValueError('移动后文件哈希不一致: ' + kind)
            updated = {}
            for database in sorted((data / 'downloads').glob('*/crawl-state.sqlite3')):
                backup = backup_root / database.parent.name / database.name
                backups.append((database, backup))
                updated[database.parent.name] = rewrite_database(database,
                    root / 'downloads' / database.parent.name, database.parent, backup)
            if downloads:
                write_json(manifest_path, manifest)
            # 所有证据文件原字节不变；数据库的唯一允许差异是已备份的 objects.path。
            database_names = {str(path.relative_to(data / 'downloads')) for path, _ in backups}
            for kind, previous in before.items():
                current = snapshot(data / kind)
                for name, digest in previous.items():
                    if kind == 'downloads' and name in database_names:
                        continue
                    if current.get(name) != digest:
                        raise ValueError('历史证据文件发生改变: ' + kind + '/' + name)
            report = dict(status='migrated', directories=previews, database_paths_updated=updated,
                          original_file_hashes=before, plan_hashes=plans, evidence_bytes_preserved=True,
                          created_at=manifest['created_at'])
            write_json(data / 'reports' / ('layout-migration-' + stamp + '.json'), report)
        except BaseException:
            for database, backup in backups:
                if backup.is_file():
                    shutil.copy2(backup, database)
            if downloads and manifest_path.is_file():
                manifest_path.unlink()
            for source, target in reversed(moved):
                target.rename(source)
            raise
    return dict(status='migrated', directories=previews, database_paths_updated=updated,
                evidence_bytes_preserved=True, manifest=str(manifest_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--apply', action='store_true', help='移动目录并备份、刷新数据库文件路径；默认仅预览')
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.root, args.apply), ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, '目录迁移失败: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
