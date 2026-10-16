"""定位工程源码或独立skill包的资源，不依赖进程工作目录。"""
from pathlib import Path


def skill_directory():
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / 'SKILL.md').is_file() and (candidate / 'countries').is_dir():
        return candidate
    raise ValueError('未找到完整skill资源；保留SKILL.md、scripts和countries的相对目录结构')


def default_workspace():
    """工程兼容入口保留工程根默认值；独立包不将安装目录当工作区。"""
    skill = skill_directory()
    project = skill.parent.parent
    if skill.parent.name == 'skills' and (project / 'law_crawler/__init__.py').is_file() and (project / 'tools/install_skill.py').is_file():
        return project
    return Path.cwd().resolve()


def country_directory(root):
    """工作区自有目录优先；源码工程可读取内嵌skill，空工作区不借安装包资源。"""
    root = Path(root).expanduser().resolve()
    local = root / 'countries'
    if local.exists() or local.is_symlink():
        return local
    project_skill = root / 'skills/law-crawler'
    if (project_skill / 'SKILL.md').is_file() and (project_skill / 'countries').is_dir():
        return project_skill / 'countries'
    return local


def configuration_path(root, requested=None):
    if requested is not None:
        path = Path(requested).expanduser()
        return path if path.is_absolute() else Path(root).expanduser().resolve() / path
    return country_directory(root) / 'global-config.properties'
