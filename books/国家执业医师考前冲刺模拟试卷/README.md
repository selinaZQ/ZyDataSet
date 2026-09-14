# 国家执业医师考前冲刺模拟试卷：文字提取与清洗

本目录按照“原始 PDF → 带坐标原始提取 → 双栏重排与清洗 → 结构化 JSONL → 合并 Markdown → QA”的 SOP，处理：

- 考前冲刺模拟试卷（一）至（四）
- 考前冲刺参考答案及解析

## 输出目录

- `01_raw/<document>/json/`：每页原始文字、坐标、置信度及来源模式。
- `01_raw/<document>/markdown/`：未经清洗的每页几何顺序文本。
- `02_clean/*.ordered.md`：去页眉页脚、恢复双栏阅读顺序并保留逐页标记的 Markdown。
- `02_clean/exam-*.md` 与 `answers.md`：进一步归并题干、选项或解析后的阅读版 Markdown。
- `02_clean/*.lines.jsonl`：带坐标、页码和阅读顺序的行级 JSONL。
- `03_structured/pages.jsonl`：统一的页级结构化数据。
- `03_structured/exam-*.questions.jsonl`：按题干、A-E 选项、单元、题型和来源页归并的题目记录。
- `03_structured/answers.answers.jsonl`：按题号、答案、解析和来源页归并的答案记录。
- `04_merged/全集.md`：五份资料按试卷一至四、答案解析顺序合并的 Markdown。
- `05_review/qa_report.json`：页数、来源哈希、低置信度行和噪声残留检查。
- `05_review/review.jsonl`：需要人工复核的 OCR 低置信度题目，保留原页、坐标和行号。

## 本次产出统计

- 共处理 5 份 PDF、286 页。
- 四套试卷各 600 题，共 2,400 题；每套每单元题号均完整覆盖 1～150。
- 共提取 2,400 条答案，与 2,400 道题逐条匹配。
- 所有题目均具有 A～E 五个选项。
- 答案原书中有 153 条只给答案、未附解析，结构化记录以 `analysis_available: false` 明确标识。
- 试卷一、二共有 273 道题因至少一行 OCR 置信度低于 0.80 而进入复核清单；试卷三、四及答案解析使用原生文字层，没有低置信度 OCR 记录。

## 提取策略

- 试卷一、二没有文字层：250 DPI 渲染后使用 PP-OCRv5 服务端检测与识别模型。
- 试卷三、四及答案解析具有文字层：使用 `pdfplumber` 提取文字与坐标，避免不必要的 OCR 误差。
- 两类输入最终使用同一坐标结构，根据全栏标题、左栏、右栏恢复阅读顺序。
- 对扫描符号异常造成的答案标记缺失、选项粘连等情况，人工对照渲染页确认后写入 `config/confirmed_*_corrections.json`，结构化记录中同时保留 `confirmed_correction` 审计字段。
- 临时页面 PNG 写入仓库根目录的 `tmp/`，识别完成后立即删除，不纳入版本控制。

## 运行

在仓库根目录执行：

```powershell
& ".\books\国家执业医师考前冲刺模拟试卷\run_all.ps1"
```

脚本默认断点续跑：已存在的原始页 JSON 不会重复提取。
