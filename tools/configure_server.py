#!/usr/bin/env python3
"""为独立 Linux 用户配置 Claude/DeepSeek 与运行观测；不打印凭据。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observer.events import install_hooks
from tools.install_skill import install

DEFAULTS = {
    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
    'ANTHROPIC_MODEL': 'deepseek-flash[1m]',
    'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-flash[1m]',
    'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-flash[1m]',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-flash',
    'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-flash',
    'CLAUDE_CODE_EFFORT_LEVEL': 'max',
    'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '786432',
}


def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('配置文件不允许使用符号链接')
    descriptor, temporary = tempfile.mkstemp(prefix='.law-crawler-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(workspace, download_base, runtime_dir, provider_input=None):
    workspace, download_base, runtime_dir = map(lambda p: Path(p).resolve(), (workspace,download_base,runtime_dir))
    for directory in (workspace, download_base, runtime_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 本机原有下载与批次记录不迁移；新服务器使用自己的明确目录。
    for country in (ROOT/'skills/law-crawler/countries').iterdir():
        if country.is_dir():
            output = workspace/'countries'/country.name/'index.md'
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                shutil.copy2(country/'index.md', output)
    config = workspace/'countries/global-config.properties'
    if not config.exists():
        config.write_text('downdloadDir='+str(download_base)+'\n', encoding='utf-8')

    settings_path = Path.home()/'.claude/settings.json'
    if settings_path.is_symlink():
        raise ValueError('Claude 配置文件不能为符号链接')
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    if not isinstance(settings, dict) or not isinstance(settings.get('env', {}), dict):
        raise ValueError('已有 Claude 配置格式错误；请先核对，不自动覆盖')
    environment = {**settings.get('env', {}), **DEFAULTS}
    if provider_input is not None:
        source = provider_input.get('env', provider_input)
        if not isinstance(source, dict) or urlsplit(source.get('ANTHROPIC_BASE_URL', '')).hostname != 'api.deepseek.com':
            raise ValueError('仅接受明确指向 DeepSeek 的提供方配置')
        key = source.get('ANTHROPIC_AUTH_TOKEN') or source.get('ANTHROPIC_API_KEY')
        if not isinstance(key, str) or not key.strip():
            raise ValueError('DeepSeek 凭据为空')
        environment['ANTHROPIC_AUTH_TOKEN'] = key.strip()
    # 不复制来源机器的权限、MCP、会话历史或其他供应商的认证。
    environment.pop('ANTHROPIC_API_KEY', None)
    for name in ('CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY','ANTHROPIC_PROFILE','CLAUDE_CODE_OAUTH_TOKEN'):
        environment.pop(name, None)
    settings['env'] = environment
    private_json(settings_path, settings)
    install('claude', source=ROOT/'skills/law-crawler')
    install_hooks(settings_path, runtime_dir, workspace, sys.executable)

    binary = Path.home()/'.local/bin/claude'
    version = None
    if binary.is_file():
        result = subprocess.run([str(binary),'--version'], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            version = result.stdout.strip()[:120]
    private_json(runtime_dir/'deployment.json', dict(deployed_at=datetime.now(timezone.utc).isoformat(),
        claude_version=version, observer_schema=1))
    print(json.dumps(dict(workspace=str(workspace),download_base=str(download_base),runtime_dir=str(runtime_dir),
        claude_version=version,skill_installed=True,provider='DeepSeek',
        api_key_configured=bool(environment.get('ANTHROPIC_AUTH_TOKEN'))),ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--download-base', type=Path, required=True)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--provider-config-stdin', action='store_true')
    args = parser.parse_args()
    try:
        provider_input = None
        if args.provider_config_stdin:
            text = sys.stdin.read(16385)
            if len(text) > 16384:
                raise ValueError('提供方配置过大')
            provider_input = json.loads(text)
        configure(args.workspace,args.download_base,args.runtime_dir,provider_input)
    except Exception as exc:
        # 不把带凭据的输入或异常值写入部署日志。
        print('配置失败：'+type(exc).__name__+'；请检查目录权限及配置格式。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
