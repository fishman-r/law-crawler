# 本地自动OCR与表格复核

## 先归档，再识别

使用[三阶段流程](staged-pipeline.md)先冻结来源并执行capture。将该批次收据中的原始PDF路径交给OCR，不覆盖官方原件，不把已有隐藏文字层冒充本轮OCR。为每个PDF指定新的输出目录作为一次识别任务，优先放在配置下载目录的国家子目录下。

```text
python "SKILL_DIR/scripts/local_ocr.py" --pdf "ARCHIVED_PDF" --output "COUNTRY_DIR/pipeline/ocr/OCR_RUN_ID" --engine apple-vision --language id-ID
```

在macOS选择已可用的Swift与Apple Vision；在其他系统选择已经安装程序及语言包的Tesseract：

```text
python "SKILL_DIR/scripts/local_ocr.py" --pdf "ARCHIVED_PDF" --output "COUNTRY_DIR/pipeline/ocr/OCR_RUN_ID" --engine tesseract --language ind
```

检查`pdfinfo`、`pdftoppm`和对应引擎是否可执行。使用实际支持的语言，多个语言重复传`--language`，不要静默降级为其他语言。默认200 DPI，可用`--dpi`在72至600之间调整；更改配置用新输出目录。缺依赖时保留原件并报告具体错误，不自动安装工具或模型。Apple Vision已在macOS实跑；Tesseract适配的单元测试不等于各系统真实引擎验收。

读取输出`receipt.json`确认完整成功，再将`transcription.json`路径写入新处理manifest的`transcription_path`。保留每页PNG、原始OCR文本框坐标/置信度、引擎及版本、语言、原件与配置哈希、逐页及总耗时。成功重跑验证缓存哈希并复用；失败目录保留诊断，改配置或重试时用新目录。空OCR页不自动当作真正空白页，不仅处理有文字的页面。

## 区分识别、排版和内容正确

将OCR输出视为待审核转录。逐页检查漏字、错字、条号、交叉引用、日期、签章遮挡、机构和姓名；保留可见图形的独立视觉说明。不要把引擎置信度当作准确率：实跑出现置信度1.0但错词的文本框。质量报告列出实际发现的错误和证据，没有经核验参考全文时不虚构CER/WER或百分比准确率。

保留OCR原始reading order及坐标。对表格检查每个单元格的行、列、单位和脚注；即使全部数字被识别，按列串联的文本也不能证明行对应正确。区分小数点和千位分隔符，保留源数值并说明语言转换；不能将印尼价格`9.450`变成9.45。

把页图确认的表格格线与OCR分开记录。显式区分人工确认格线、坐标归格算法、人工纠错与自动表格检测；下述自动检测仅针对清晰横纵格线，不宣称通用版式理解。缺失行号、空单元格和跨边界文本框不得凭位置猜补。将待核标记交给独立复核者，修订后建立新批次并保留原自动结果。

## 自动检测有格线表格与跨页候选

对于已生成完整逐页OCR的PDF，先用自动格线检测减少人工输入坐标。此入口使用本地已安装的`numpy`与`Pillow`，不需要模型API、不安装依赖；使用实际具备这两项依赖的Python解释器。当前已验证bundled Python环境，普通系统Python可能缺包。

```text
python "SKILL_DIR/scripts/automatic_tables.py" --sidecar "OCR_OUTPUT/transcription.json" --output "COUNTRY_DIR/pipeline/ocr/AUTO_TABLE_RUN_ID"
```

默认二值阈值170；`--threshold`接受1至254，调整后必须使用新目录。输入必须为`normalized_top_left_xywh`坐标。程序对完整PDF全部页扫描横纵线，按线段交点建立表格候选，对缺失内部边界提出合并单元格；部分边界不足以确认时保持分格并记录歧义。结构写入HTML表格的`rowspan`/`colspan`，同时保留单元格坐标、原始观察索引、缺失值与边界问题，不把合并格重复填入多个位置。表格区域外保留OCR顺序；全文sidecar仍声明全部原页。

输出包含`transcription.json`、`table-audit.json`、`cross-page-candidates.json`、`receipt.json`。相邻页候选根据列数、列边界接近程度、表头词重合、页面位置及续表文字评分；该分数不是概率。跨页片段不自动合并，sidecar明确标记候选及待视觉确认。原件、每页OCR与页图、实现、依赖版本、配置均绑定哈希；重跑只读验证并复用，失败诊断保留，修改输入或代码后用新目录。

实际验证：07/2020完整9页无需人工横纵坐标，检出第8、9页两片段。与未读取检测结果的独立视觉参考比较，物理数据行分别7与9、均4列，11个`rowspan`锚点全部一致，续表候选8→9一致；没有真实`colspan`，横向合并仅通过人工构造单元测试。43/2014另外验证完整10页中的6片段，共100数据行、5列，并生成5条相邻续表候选；它只作为结构检测样本，不代表已完整翻译或审核其正文。

上述匹配只证明特定样本的格线结构及候选关系，不证明OCR文字、价格、单位、脚注或译文准确。独立复核者须先从原页建立参考，再对照检测结果，避免由自动结果反向生成所谓标准答案。07/2020仍存在缺失编号、缺失横线值和脚注对应问题；不得自动补值或修改`90.000/ekor*`等原件可见内容。内含多条商品/价格的单一物理格保留多行，不虚构原表分隔线。逐项核对占位横线与垂直位置；缺失横线可能令正确数值关联到错误商品。需要改善换行后的可读性时，另附明确标为编辑整理的子项对照表，不替换原表结构，并将该对照表纳入三语复核。

边界：无格线、斜线、严重歪斜/透视、断裂细线及边界贴字可能漏检或错误合并；不能仅凭缺边判断法律上的单元格关系。保留全部歧义并请求独立视觉修订。检测不足时使用下面的人工网格入口处理适合矩形格线的简单表；合并格或复杂关系需另建可追溯派生稿，不能让旧人工网格工具冒充支持合并单元格。

## 按明确格线重建表格

查看原始页图后创建格线JSON，记录实际确认者；不要沿用其他PDF的坐标。横纵坐标均从左上角归一化到0–1，第一行作为表头。当前仅支持一页一个矩形表格、单表头行和不含合并单元格的格线；多行单元格可保留换行。以下仅是结构示例，不能直接用于真实法规：

```json
{
  "grid_source": "human_confirmed",
  "confirmed_by": "实际页图检查者",
  "tables": [{"page": 1, "column_edges": [0.1, 0.3, 0.8], "row_edges": [0.2, 0.3, 0.5]}]
}
```

```text
python "SKILL_DIR/scripts/reconstruct_ocr_tables.py" --sidecar "OCR_OUTPUT/transcription.json" --grid "GRID.json" --output "COUNTRY_DIR/pipeline/ocr/TABLE_RUN_ID"
```

将新输出的`transcription.json`作为处理manifest输入。工具按文本框中心归格，多行文字按y/x排序，以Markdown保留表头和行列，并将单元格坐标、原观察索引、输入哈希写入`cell-audit.json`。保留`MISSING`和`AMBIGUOUS BBOX`提示直到独立视觉修订；跨界框不会静默丢弃，表外文本保持原观察顺序。检查全部单元格，不把提示少当作准确率高。

当前格线工具只接受`normalized_top_left_xywh`坐标（本轮Apple Vision输出）；遇到Tesseract像素坐标应报告不支持，不把像素直接当归一化坐标。两种OCR引擎可用不表示两者的表格适配均已实测。保存旧sidecar、OCR收据与新格线产物，重复执行验证引用及产物哈希，不覆盖现有不同输入。

## 再翻译并独立复核

按[法律翻译文体](legal-translation.md)使用新的冻结处理批次将完整OCR转录交给低成本模型。将OCR识别错误、表格关联错误和翻译错误分别记录，不让译文流畅性掩盖原文错误。对照全部原页与三语全文，检查表头、各行地区/对象、数值、单位、适用条件、跨页续表、合并单元格及脚注。只验证普通矩形表格时，不宣称已覆盖合并单元格或复杂跨页表。

沿用[修订与重验](staged-pipeline.md)的哈希绑定与独立复核门控。人工修订必须保留页证据，不改自动OCR原稿或历史审核结论。分别统计归档、渲染/OCR、表格重建、低成本推理、编辑和首审/终审时间；OCR核心计时不代替含渲染与进程启动的总耗时。API账单为0不等于硬件、电力和订阅归属总成本为0。

## 三语HTML表格确定性检查

在模型输出或编辑修订后、独立复核之前，对完整三语文本执行只读检查：

```text
python "SKILL_DIR/scripts/translation_tables.py" --source "SOURCE.txt" --zh "ZH.txt" --en "EN.txt" --output "NEW_AUDIT.json"
```

检查HTML表格数量、行列、`rowspan`/`colspan`锚点、重叠/缺失格、嵌套或未闭合标签，以及同锚点单元格的数字标点、所附星号数量和币种标记。分块翻译造成的拆表、嵌套表或价格移格会留下明确flag；纯Markdown表格或没有HTML表格不被此工具判为通过。保持原件与文本只读，审计JSON绑定三语SHA及实现SHA，同输入仅验证复用，不覆盖不同输入或已篡改审计。

`pass_structure_only`只说明上述机械检查一致；`requires_review`表示发现待审问题，CLI退出码为2。数字改写、本地化币种标记也可能触发待核，应由复核者对原页解释，不能为消除flag擅改原文。工具不检查语义、法律效果、单位翻译、缺失横线占位或脚注正文，更不能把原文OCR错误传播成“准确”。保留初稿失败审计，修订冻结后另写新审计，再进行独立法律与风格复核。
