# 欧盟索引验证

> 按本文流程处理该法域；将带日期、“本轮/本机”及库存数字的段落视为2026年10月历史观察，重新核实当前响应。工程reports路径仅供维护者追溯，独立包不包含且不依赖这些历史文件。

当前 `countries/EU/index.md` 是跨国索引：N-Lex 导航入口，加上德国、法国、荷兰、意大利国家平台；不能据此宣称覆盖欧盟自身法规或全部 27 国。

以下命令仅用于已明确要求法规原文的任务；否则先询问是否仅采集目录元数据，再明确选择模式。

```text
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction EU --mode content --index countries/EU/index.md --platform all --limit 4 --per-platform-limit 1 --max-details 2
```

文件依据平台归属写入 `<downdloadDir>/Germany/`、`France/`、`Netherlands/`、`Italy/`，各国独立 JSON、SQLite、原件与去重。现代入口在 `<downdloadDir>/.batches/<batch-id>/attempts/<编号>/` 保存 JSON、Markdown、HTML 汇总，包含每个平台国家、结果及实际保存路径；历史 `<downdloadDir>/EU/last-run.json` 是旧入口的报告。EU 不作为成员国文件的国家归属。沿用配置，无需为各成员国生成或修改输入索引。

- N-Lex 标为 `navigation_only`；导航页面不当作法规原件下载。
- Rechtsprechung、Rechtspraak 标为 `out_of_scope_case_law`，不采判决；Légifrance 等混合来源仅选择法规。
- 未适配来源标为 `unsupported`；访问错误保留实际请求异常；无结果不等于网络受阻。
- 非 PDF 优先按详情中实际提供的原件链接选择。XML 原响应保存于 `raw/` 并转换为 Markdown；ZIP 整体保留原字节；不能把网页改名为 XML，不能将目录、验证码或错误页保存成法规。
- 日期使用来源明确字段，不从 URL 日期、下载时间或条目顺序猜测公布日期。合并版本日期另行记录，不替代公布日期；国家归属不代表全国适用。

这是每个平台的少量样本验证，不是全量下载或英文覆盖承诺。来源使用当地官方语言；是否具有英文版本需单独核实。每轮报告实际成功、失败、范围外和未扫描项，随后复跑成功来源验证持久去重。

## 首轮已验证的德国路径

Gesetze im Internet 使用 `aktuell.html → Teilliste_<字母>.html → 法规 index.html → xml.zip`，每次最多检查前三个字母目录，不是全站翻页。页面声明 ISO-8859-1，按声明解码以保留德语标题。XML ZIP 优先，PDF 备选；压缩包保持官网原字节。首份样本 `BJNR507500943` 已核对内部 XML 标题与详情一致，属于历史法规，不作现行有效保证。

本轮报告维护在工程 `reports/eu-verification-2026-10-14.json` 和 `.md`，德国首次下载记录另存 `eu-germany-first-run-2026-10-14.json`。运行时以当前响应为准。

## 本轮未完成的平台

法国 JORF 入口实际返回 403，法规类型和下载链路尚未验收；适配器仅保留受限入口诊断，不承诺网络恢复后即可下载。

荷兰已通过公开搜索表单的默认日期构造经实际验证的 GET 结果路径，再读取 BWBR 详情。XML/RTF 导出涉及 POST 表单，当前下载接口未适配；GET 导出参数返回 HTML，不能入库。报告明确为未完成下载，不归因于 TLS/403。

意大利从首页正式法规 URN 读取 ELI 标题、类型、本地标识和公布日期，只选择身份匹配的 AKN 导出链接；本次导出响应是 HTML 错误页，因此拒收。上述为历史验收结果。现代转换器支持带适配器正文结构证据的 XML，未来 AKN 接口实际返回法规文本后仍须确认正文根节点及来源、增加回归测试，不能直接放开任意 XML。
