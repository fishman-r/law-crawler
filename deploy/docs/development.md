# 四目录结构与开发流程

```text
law-crawler/
├── skill/                         # 法规采集 Skill
│   ├── law-crawler/               # 唯一完整技能包，可整体复制安装
│   │   ├── SKILL.md               # 智能体执行说明
│   │   ├── references/            # 领域规范、操作说明和历史记录
│   │   ├── agents/                # 客户端展示配置
│   │   ├── countries/             # 唯一随包国家索引与默认配置
│   │   └── scripts/
│   │       ├── *.py               # 六个独立命令入口
│   │       └── law_crawler/       # 唯一采集实现，含平台适配器
│   ├── law_crawler/__init__.py    # 工程导入桥，不复制运行实现
│   ├── *.py                      # 原四个兼容命令
│   └── tests/                    # 采集测试与 fixtures
├── web/                           # Web 看板
│   ├── observer/                 # 服务、资源库、会话观测、static前端
│   └── tests/                    # 看板与文件访问测试
├── deploy/                        # 部署与工程工具
│   ├── install.sh                # Linux/Claude Code/systemd安装
│   ├── run.py                    # 工程统一启动、测试入口
│   ├── tools/                    # 检查、安装、服务器配置、旧数据迁移
│   ├── tests/                    # 工具、布局、安装与独立包测试
│   └── docs/                     # 本文、部署及历史验证说明
├── data/                          # 本地资料，除README外不进入Git
│   ├── downloads/                # 法规原件、元数据、SQLite、.batches
│   ├── reports/                  # 历史采集、审计、诊断和测试证据
│   ├── tmp/                      # 临时文件
│   └── runtime/                  # 按启动参数创建的运行状态
├── README.md
└── LICENSE
```

将采集逻辑维护在 `skill/law-crawler/scripts/law_crawler/`，看板逻辑维护在 `web/observer/`。完整技能包不依赖 `web/`、`deploy/`、测试或本地资料，复制 `skill/law-crawler/` 即可使用。`skill/law_crawler/__init__.py` 只负责将工程导入指向同一套模块。

## 工程命令

在工程根目录运行；从其他目录调用时使用 `deploy/run.py` 的绝对路径。统一入口配置本次进程的导入路径，不要求设置全局 PYTHONPATH。

```sh
python3 deploy/run.py skill --help
python3 deploy/run.py skill prepare --root "$PWD" --jurisdiction Canada
python3 deploy/run.py skill download --root "$PWD" --jurisdiction Canada --mode metadata --plan-only
python3 deploy/run.py skill rebuild-markdown --root "$PWD" --jurisdiction Canada
python3 deploy/run.py web --workspace "$PWD" --download-base "$PWD/data/downloads" --runtime-dir "$PWD/data/runtime"
```

`skill` 转交给标准包入口，保留 download、import-file、review-body、rebuild-markdown、prepare、browser-healthcheck 子命令。`web`、`claude`、`events` 分别转交看板服务、Claude启动器和事件接收器；所需参数可用各自的 `--help` 查看。

原四个兼容命令已归入 `skill/`，例如 `python3 skill/download_regulations.py --help`。独立安装包继续使用自身 `scripts/*.py`；在其 scripts 目录也可运行 `python3 -m law_crawler`。

## 配置与数据

工程内默认工作区仍为工程根。已有工作区自有 `countries/` 优先；源码工程没有该目录时，读取 `skill/law-crawler/countries/`，不维护第二份索引。

工程使用随包默认配置时，相对下载路径归入 `data/`，默认输出 `data/downloads/`。显式用户配置与独立安装包仍按指定工作区解析；绝对下载路径不变。所有本地资料都保留在 Git 忽略范围，测试 fixtures 属于源码。

旧的根 downloads、reports、tmp 可通过以下工具预览与迁移：

```sh
python3 deploy/tools/migrate_local_data.py
python3 deploy/tools/migrate_local_data.py --apply
```

工具检查目标冲突、持有执行锁、核对迁移前后文件哈希，并临时备份数据库后只更新文件位置索引；操作结束后自动清理临时备份。法规原件、sidecar、历史计划、候选、快照和报告保留原始字节。新下载根的 `.relocation.json` 绑定旧计划 SHA，并记录三类目录的旧新映射，供重跑和Web批次导出读取。

迁移许可不绕过既有法域、限量、快照、文件完整性或转换版本校验。历史缺失临时文件仍可能缺失，旧转换目标仍可能需要新建批次；历史证据内的旧路径保留，按迁移记录追溯。

## 检查、测试和安装

```sh
python3 deploy/tools/check_skill.py
python3 deploy/run.py test -q
python3 deploy/run.py test --group skill -q
python3 deploy/run.py test --group web -q
python3 deploy/run.py test --group deploy -q
python3 deploy/tools/install_skill.py --agent codex
```

全套测试入口分别发现并执行三组测试，明确输出每组数量，避免同名 tests 目录冲突或漏跑。检查器只读核对资源、语法、内部依赖和重复实现；不生成源码副本。

安装器还支持 `--agent claude|opencode|workbuddy`，以及 `--scope project --project /path/to/project` 或 `--dest`。安装后的目录仅用于运行，修改仍回到工程源码。

运行中的服务器和其他智能体安装副本不会因本地重排自动发布。服务器更新按[部署说明](deployment-ecs.md)执行，持久数据使用原先明确配置的绝对路径。
