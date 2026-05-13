"""Prompt construction: build the per-paper input block and the full LLM prompt."""

from arxiv_report.pub_status import classify_pub_status


def _render_status_badge(paper: dict) -> str:
    """Pre-render the status <span> so the LLM can paste it verbatim into the <h3>."""
    status_class, label = classify_pub_status(
        paper.get('comment', ''),
        paper.get('journal_ref', ''),
        paper.get('doi', ''),
    )
    if status_class == 'preprint':
        return ''
    return f'<span class="status-tag status-{status_class}">{label}</span>'


def _build_input_text(papers: list[dict]) -> str:
    """Concatenate the per-paper input blocks fed into the prompt template."""
    blocks = []
    for i, p in enumerate(papers):
        badge = _render_status_badge(p)
        blocks.append(
            f'[{i + 1}] Entry ID: {p["url"]}\n'
            f'Title: {p["title"]}\n'
            f'Authors: {p["authors"]}\n'
            f'Categories: {", ".join(p["categories"])}\n'
            f'Status badge HTML: {badge or "(none)"}\n'
            f'Abstract: {p["summary"]}\n\n'
        )
    return ''.join(blocks)


_PROMPT_TEMPLATE = """\
你是一名资深高能天体物理学家，正在为同行整理 arXiv 日报。

任务：阅读下方 {paper_count} 篇论文摘要，输出一份纯 HTML 报告。

输出规范：
- 只输出 <body> 内部内容
- 不要包含 <html>/<head>/<body> 标签
- 不要使用 Markdown
- 不要使用 ```html``` 等代码围栏
- 物理符号默认使用 Unicode（α、β、γ、ν、ν̄、M⊙、erg s⁻¹、10⁻¹² 等），严禁 LaTeX（$\\alpha$、\\frac{{}}{{}} 等）
- 非对称误差棒不要用 Unicode 上下标拼接（Unicode 无法垂直堆叠且缺少小数点上下标），必须用 HTML：
    示例：H₀ = 71.4<span class="errbar"><sup>+13.8</sup><sub>-13.4</sub></span> km s⁻¹ Mpc⁻¹
- 上下标若含小数点、字母混排或多位数字组合（Unicode 难以干净表达），同样使用 <sup>/<sub>，例如 10<sup>2.5</sup>、χ<sub>eff</sub>
- 中文翻译保留 GRB、AGN、SN、PSR、FRB、TDE 等约定俗成的英文缩写

报告结构：

第一部分：领域索引
- 用 <div class="index-box"> 包裹，内部首行为 <h3>领域索引</h3>
- 仅从以下固定子领域中选择，类别名严格使用括号前的中文标签：
    1. 伽马射线暴（GRB / Afterglow）
    2. 快速射电暴（FRB）
    3. 超新星、千新星与瞬变源（SNe / Kilonova / TDE / FBOT）
    4. 活动星系核与耀变体（AGN / Blazar / Quasar）
    5. 中子星、脉冲星与磁星（NS / Pulsar / Magnetar）
    6. 致密双星合并与引力波多信使（GW / Compact mergers）
    7. X 射线双星与吸积物理（XRB / Accretion / Disk-Jet）
    8. 黑洞与相对论喷流（BH / Jets / SMBH）
    9. 宇宙射线与高能中微子（UHECR / Galactic CR / HE ν）
    10. 甚高能与超高能伽马射线天文（VHE / UHE γ-ray）
    11. 超新星遗迹、星际介质与星系团（SNR / ISM / ICM）
    12. 理论、数值方法与仪器（Theory / Numerical / Instrumentation）
- 每篇论文按主体研究对象归入 1-2 个最相关的类别，避免无原则地多归
- 若全部论文都不属于某类别，则省略该类别行
- 每个非空类别输出一行：<p><strong>类别名：</strong> <a href="#p1">[1]</a>, <a href="#p5">[5]</a></p>
- 类别按上表 1-12 顺序排列

第二部分：今日重点（可选）
- 用 <div class="highlight-box"> 包裹
- 从 {paper_count} 篇论文中挑选 0-3 篇真正具有非平凡科学贡献的，宁缺毋滥。判定标准（满足任一即可）：
    - 首次观测/探测某现象、新天体、或新能段
    - 对主流理论模型给出强约束或反例
    - 跨子领域方法迁移（如 ML 应用于物理参数推断）
    - 显著挑战既有结论或经典图像
- 若全部论文都是常规进展、纯数据释出、增量改进、综述类，则**整个 highlight-box 省略**，不要写"今日无亮点"、"暂无重点"等占位句
- 结构如下（最多 3 行 <p>）：
  <div class="highlight-box">
  <h3>今日重点</h3>
  <p><a href="#pN">[N]</a> 1 句中文，明确指出该论文的非平凡之处（如"首次给出 X 现象 PeV 段上限"、"挑战 Y 主流模型"），不要复述"研究问题"原话</p>
  </div>

第三部分：论文条目
按编号 1 到 {paper_count} 顺序排列。每篇严格按以下模板（N 替换为论文编号）：

<div class="paper-item" id="pN">
<h3>[N] <a href="论文的Entry ID URL">Entry ID</a> [此处粘贴 Status badge HTML 字段的内容；若该字段为 "(none)" 则整体省略含前导空格]</h3>
<p><strong>英文标题：</strong>原英文标题</p>
<p><strong>中文标题：</strong>专业学术翻译</p>
<p><strong>作者：</strong>作者列表（超过 10 位仅列前 10 位，末尾追加 "et al."）</p>
<p><strong>研究问题：</strong>1 句中文，点出这篇文章试图回答/质疑/检验的具体科学问题或切入点</p>
<p><strong>研究方法：</strong><span class="method-tag">[Observation|Simulation|Theory|Modeling 四选一]</span> 2-3 句中文，直接描述核心方法与数据/模型</p>
<p><strong>研究结果：</strong>2-4 句中文，直接陈述关键物理发现，至少含一个定量数值、能段、或显著性约束</p>
</div>

写作要求：
- 学术语气、信息密度高，不要写"本文研究了"、"研究人员发现"、"该工作探讨了"这类填充语
- "研究问题"必须具体：写明文章针对的具体科学争议、待检验假设、或观测/理论缺口
    - ❌ 泛背景："GRB 是宇宙中最剧烈的爆发现象"
    - ✅ 具体问题："用 NICER X 射线脉冲轮廓拟合约束中子星半径 R，区分软态与硬态核物态方程"
- 若论文确实没有非平凡的科学问题（如：纯数据释出、仪器/巡天介绍、综述类、纯方法论文），则**整行 <p><strong>研究问题：</strong>...</p> 省略**，不要写"无明确问题"、"旨在介绍 X"等占位句
- "研究方法"标签按论文主体方法判断：
    - 纯数据分析 → Observation
    - 数值/MHD/N-body → Simulation
    - 解析推导 → Theory
    - 含拟合/参数推断/半解析 → Modeling
- "研究结果"部分必须包含具体数值（如测量值±误差、置信度上下限、能段、显著性 σ），避免泛泛"显著相关"、"有较好一致性"等空话
- "Status badge HTML" 字段已是预渲染的 HTML 片段，**逐字符原样**插入 <h3> 中 Entry ID 链接之后，不要修改 class 名、不要重写文本、不要加注释；若值为 "(none)" 则不插入任何内容
- "Status badge HTML" 字段仅决定徽章显示，不影响"今日重点"挑选权重——纯预印本（无徽章）若有非平凡贡献仍可入选

待处理论文：
{input_text}
"""


def build_prompt(papers: list[dict]) -> str:
    """Render the full prompt sent to the LLM."""
    return _PROMPT_TEMPLATE.format(
        paper_count=len(papers),
        input_text=_build_input_text(papers),
    )
