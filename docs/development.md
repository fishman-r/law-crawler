# Skill源码结构与开发流程

## 目录职责

```text
law-crawler/
├── skills/law-crawler/              # 唯一完整skill源码；可整体复制使用
│   ├── SKILL.md                    # 通用agent入口
│   ├── references/                 # 按需说明与历史记录
│   ├── agents/                     # 可选agent展示配置
│   ├── countries/                  # 国家索引与默认配置
│   └── scripts/
│       ├── *.py                    # 六个薄CLI入口
│       └── law_crawler/            # 唯一运行实现源码
│           ├── __main__.py         # python -m law_crawler 命令路由
│           ├── download_regulations.py
│           ├── regulation_pipeline.py
│           ├── crawl_store.py      # 元数据、原件/派生文件与持久去重
│           ├── text_documents.py   # Markdown转换
│           ├── prepare_workspace.py
│           ├── browser_healthcheck.py
│           └── platform_adapters/  # 各官方平台发现与解析
├── law_crawler/__init__.py          # 兼容导入桥，无业务实现
├── tools/                          # 只读检查、安装及服务器配置
├── tests/                          # 自动化测试与固定样本
├── observer/                       # 独立的只读看板与agent会话观察
├── deploy/                         # 服务器部署入口
├── docs/                           # 工程维护与部署说明
├── downloads/                      # 本地采集资料，不进入Git或skill包
├── reports/                        # 本地验证证据，不进入Git或skill包
└── tmp/                            # 本地临时资料，不进入Git或skill包
```

将运行逻辑直接修改在`skills/law-crawler/scripts/law_crawler/`。将SKILL.md、references、agents、countries及六个命令入口维护在同一个skill目录；不生成运行副本、索引副本或生成清单。复制整个`skills/law-crawler/`即可取得完整能力，不依赖工程根的tools、observer或测试。

通过`law_crawler.*`导入运行模块，测试mock也使用同一规范名称。工程根的导入桥将模块直接加载自skill目录，保持模块身份和包路径一致，不复制实现或依赖符号链接。

用户工作区自有countries仍优先；源码工程根缺少自有countries时读取内嵌skill中的countries，相对下载路径仍按工作区根解析。显式配置不回退；不要将外部工作区个人下载目录复制进skill默认配置。

## 工程执行入口

在工程根目录运行以下命令。保留原CLI参数和批次/目录规则：

```sh
python3 -m law_crawler --help
python3 -m law_crawler prepare --root /path/to/workspace --jurisdiction Canada
python3 -m law_crawler download --root /path/to/workspace --jurisdiction Canada --mode metadata --plan-only
python3 -m law_crawler rebuild-markdown --root /path/to/workspace --jurisdiction Canada
```

按需使用`import-file`、`review-body`和`browser-healthcheck`子命令。`download`、`import-file`的工程默认工作区仍为工程根；独立安装包应明确传`--root`，未传时使用当前工作目录，因此从skill目录执行时必须显式指定外部工作区。工程默认输入来自skill的countries目录；已有工作区countries覆盖整个输入目录，不混用两份索引/配置。

根目录四个文件`download_regulations.py`、`import_browser_download.py`、`import_body_review.py`、`rebuild_markdown.py`只保留旧CLI兼容入口，不作为内部Python模块API或新增实现的位置。独立skill使用自身`scripts/*.py`命令；也可在其scripts目录使用`python3 -m law_crawler`。

## 检查、测试和安装

```sh
python3 tools/check_skill.py
python3 -m unittest discover -s tests -q
python3 tools/install_skill.py --agent codex
```

直接检查、测试和安装，无须代码生成步骤。`check_skill.py`只读检查必需资源、Python语法、内部模块依赖及旧副本残留，失败返回非零，不写入或删除文件。独立skill不依赖这个工程检查工具。

通过`tools/install_skill.py --agent claude|opencode|workbuddy`选择其他agent，也可用`--scope project --project /path/to/project`或`--dest`。安装器只复制完整目录及相对结构；安装后的目录用于运行，所有修改仍回到工程的唯一源码。运行中的服务器和其他agent安装位置不会因源码移动自动发布；部署按[服务器说明](deployment-ecs.md)执行。

## 验证边界

验证工程导入实际指向skill中的源码、根兼容入口从其他cwd启动、标准包入口、完整skill复制到中文含空格路径后的初始化/计划/重跑，以及observer和部署配置工具的help。使用临时目录及离线样本；仅生成计划不能算原文下载成功。目录重构不应修改原件、元数据结构、去重、转换文本或既有批次报告。
