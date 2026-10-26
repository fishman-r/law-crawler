# 跨工具兼容

## 连接唯一源码

维护完整的 `skill/law-crawler/` 为唯一源码。保留 SKILL.md、scripts、references、assets、countries 的相对结构；不要生成工具专属脚本、SKILL.md副本或模型专属提示词副本。将 SKILL_DIR 解析到真实目录，将 WORK_ROOT 独立设为本次数据工作区。

使用工程维护工具 `deploy/tools/install_skill.py --agent NAME` 建立目录符号链接。项目安装加 `--scope project --project PROJECT_PATH`；自定义技能集合目录加 `--dest SKILLS_PATH`。已有同源链接可重复执行，已有副本或不同链接会拒绝覆盖，不自动删除。链接要求源码持续存在且客户端能读取该真实路径。Windows无链接权限或客户端不发现链接时，配置客户端支持的自定义搜索目录；也可明确要求agent读取真实SKILL.md，并按其相对引用加载资源。不悄悄复制或修改客户端权限。

| 工具 | 安装器 NAME | 项目级技能集合目录 | 用户级 |
| --- | --- | --- | --- |
| Qoder CLI | qoder | .qoder/skills | ~/.qoder/skills |
| Claude Code | claude | .claude/skills | ~/.claude/skills |
| DeepSeek Harness | deepseek-harness | .dsh/skills | 显式 --dest 指向实际 dshHome/skills |
| Codex | codex | .agents/skills | 保留现有 CODEX_HOME/skills 配置；也可显式 --dest |
| OpenCode | opencode | .opencode/skills | XDG_CONFIG_HOME/opencode/skills |

区分Qoder CLI与QoderWork，后者文档使用 ~/.qoderwork/skills，可用 --dest 显式选择。DeepSeek Harness是独立工具，不把Claude Code配置DeepSeek模型等同于Harness；其自定义技能根目录可指向唯一源码的父目录，按该客户端版本的Config.customSkillDirs配置。

## 执行同一流程

先读取 SKILL.md，再读取被选流程的 references；使用标准Python CLI及结构化JSON结果。保持命令路径带引号，不依赖shell变量、当前目录、专用工具函数名或先前会话。

最低要求为可读写工作区的文件工具及完整Python 3.10+终端。仅具聊天能力时说明不能执行，不编造完成结果。浏览器、OCR、模型服务和独立复核按实际能力启用；没有浏览器时保留该路线受阻，不伪造browser_download。无法调度独立复核者时保留待复核，不用同一模型自评替代。

用当前工具的提问能力取得缺失法域或模式，没有专用提问API时直接自然语言询问。使用工具当前公开的浏览器接口，不引用Codex/CUA专属句柄。保留执行环境已有授权边界，不通过安装skill改变沙箱、网络或模型权限。agents/openai.yaml仅用于可选展示，不是核心依赖。

## 验收

在隔离临时目录验证链接解析、重复安装、已有文件保护、含空格路径及从非工程目录执行CLI。客户端实际验收时确认其技能列表发现law-crawler，要求只做工作区检查并核对真实入口，再用临时样例验证一次元数据流程。记录客户端版本、是否发现、实际命令和输出；未运行客户端就标为未实测，不能用Python测试代替客户端验收。

官方规则核对于2026-10-26：[Qoder CLI](https://docs.qoder.com/cli/Skills)、[QoderWork](https://docs.qoder.com/qoderwork/skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[DeepSeek Harness源码文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md)、[OpenAI技能文档](https://developers.openai.com/codex/skills)。目录与发现机制可能随客户端版本变化，优先核对已安装版本并用 --dest 覆盖路径。
