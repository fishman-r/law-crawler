# 准备独立工作区

## 选择解释器和路径

将`SKILL_DIR`替换为当前加载的SKILL.md所在目录，将`WORK_ROOT`替换为用户选定的工作区。选择Python 3.10+完整安装，包含ssl、sqlite3及本机锁模块；无需pip包、仓库检出或某个agent连接器。使用`python -X utf8`运行以下单行命令；macOS/Linux可选择`python3`，Windows可选择`py -3`，先通过`--version`确认实际版本。不将示例占位符作为字面目录创建。其他参考命令中的MANIFEST_PATH、AUDIT_PATH、COUNTRY_DIRECTORY替换为本次实际文件/目录，BATCH_ID与RUN_ID替换为本次批次/唯一标识。

按需使用当前agent公开的文件/终端/浏览器能力。仅本地Python和文件工具可用时，仍可执行CLI采集、计划与离线重建；浏览器不可用只影响浏览器辅助路线。CLI联网仍受实际网络和官方响应限制。

## 检查已有工作区

先运行只读检查，不复制模板、不创建下载目录或批次：

```text
python -X utf8 "SKILL_DIR/scripts/prepare_workspace.py" --root "WORK_ROOT" --jurisdiction Canada
```

读取返回JSON中的规范法域、实际索引、配置、下载基础目录、国家目录和逐平台`adapter_supported`。`false`表示当前包未适配，不能把它解释为官网故障；`true`也不证明官网当前可达。只读检查失败时按错误补充路径或配置，不直接改用技能安装目录。

优先读取`WORK_ROOT/countries/global-config.properties`中的`downdloadDir`（兼容`downloadDir`）。相对路径按WORK_ROOT解析，绝对路径直接作为基础目录；采集器再追加规范国家子目录。修改下载位置时先按用户要求编辑该配置；准备脚本不会覆盖已有配置。使用其他配置时给准备脚本和后续操作都传相同`--config`。

源码工程统一维护`skill/law-crawler/countries/`。工程根没有自有countries时，检查器和执行器读取这一内嵌目录；源码工程使用内置默认配置时，相对输出集中到WORK_ROOT/data/（默认data/downloads/）；独立安装包及显式用户配置仍以WORK_ROOT解析。已有工作区的countries即使只登记部分法域也优先使用，不能与包内索引或默认配置混合。显式`--config`按给定路径读取，不因缺失而回退。显式`--index`须指向实际索引；源码工程已迁移的旧根countries路径会提示新路径。

## 初始化首次工作区

只在工作区尚无索引且用户明确法域后执行：

```text
python -X utf8 "SKILL_DIR/scripts/prepare_workspace.py" --root "WORK_ROOT" --jurisdiction Canada --initialize
```

让脚本从`SKILL_DIR/countries/<Index>/index.md`选择该法域对应索引，将其写入`countries/<Index>/index.md`，并补齐缺失的`countries/global-config.properties`。EU成员国选取EU模板，但实际成员范围仍由模板真实章节校验。保留现有索引和配置；遇到已有索引未登记法域、无匹配模板、无效配置或符号链接时处理错误，不覆盖或扩展已有资料。

将`countries/`作为skill随附、直接维护的索引与默认配置；初始化时仅复制匹配索引和配置到新工作区，不再维护assets下的扁平副本。按用户任务读取实际选中的index.md；登记不表示其中所有链接都是已适配的正式法规来源。

## 在不同agent中使用

复制或安装整个skill目录，保留SKILL.md、scripts、references、countries的相对关系。不要只复制SKILL.md。将个人技能放到所用agent的用户级目录，将团队技能放到其项目级目录；不把某一个agent的目录作为所有agent的通用路径。安装位置和源码维护命令参见源码工程README（仅维护者需要）；运行已安装包不依赖该README或工程tools目录。

使用`agents/openai.yaml`只影响Codex展示；忽略该文件也能运行通用CLI。将`metadata.agent_created: "true"`作为生成来源标记；采用字符串映射以符合[Agent Skills规范](https://agentskills.io/specification)，不添加平台私有顶层字段。

在macOS/Linux上使用POSIX文件锁，在Windows上使用msvcrt锁；缺少标准锁模块或文件系统不支持锁时停止写入并显示原因，不能无锁继续。Windows分支已由离线模拟测试覆盖，尚无Windows实机验收；不宣称在每个agent的交互客户端均已实测。

## 用例与预期边界

| 用户请求 | 执行判断 |
| --- | --- |
| “下载法规” | 先询问国家/法域；尚不采集。 |
| “跑一轮加拿大验证” | 法域已明确；先询问是否只采集元数据。 |
| “加拿大，只采集目录” | 校验登记/配置后执行metadata；不请求正文附件。 |
| “印尼，下载法规原文” | 校验登记后执行content；报告本次有限发现范围。 |
| “重跑批次ABC”或“重建加拿大已有Markdown” | 前者沿用原计划；后者从本地raw离线重建，不重新下载原件。 |

读取具体批次、格式及来源规则时再加载相应references。没有超过10k词的通用参考文件；将历史记录留在history子目录，仅在追查历史行为时按文件及关键词检索，不在普通采集时全量加载。
