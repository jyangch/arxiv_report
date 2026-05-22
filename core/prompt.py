"""Prompt construction: build the per-paper input block and the full LLM prompt."""

import re

from core.pub_status import classify_pub_status

_ARXIV_DISPLAY_ID_RE = re.compile(r'/abs/([^/?#]+)')


def _arxiv_display_id(url: str) -> str:
    """Extract arxiv id WITH version suffix for display, e.g. '2605.13799v1'."""
    m = _ARXIV_DISPLAY_ID_RE.search(url or '')
    return m.group(1) if m else url or ''


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
            f'[{i + 1}] arXiv ID: {_arxiv_display_id(p["url"])}\n'
            f'URL: {p["url"]}\n'
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
- **首字符必须是 `<`**；末尾最后一个 `</div>` 之后不得有任何字符
- 不要写"以下是报告"、"This is the report"、"Here is..." 等前言或总结
- 不要包含 <html>/<head>/<body> 标签
- 不要使用 Markdown
- 不要使用 ```html``` 等代码围栏
- 物理符号默认使用 Unicode（α、β、γ、ν、ν̄、erg s⁻¹、10⁻¹² 等），严禁 LaTeX（$\\alpha$、\\frac{{}}{{}} 等）
- 非对称误差棒不要用 Unicode 上下标拼接（Unicode 无法垂直堆叠且缺少小数点上下标），必须用 HTML：
    示例：H₀ = 71.4<span class="errbar"><sup>+13.8</sup><sub>-13.4</sub></span> km s⁻¹ Mpc⁻¹
- 上下标若含小数点、字母混排或多位数字组合（Unicode 难以干净表达），同样使用 <sup>/<sub>，例如 10<sup>2.5</sup>、χ<sub>eff</sub>
- 天体符号 ⊙（太阳）、⊕（地球）、♃（木星）作为下标使用时**必须**用 <sub>，绝不能直接接在主符号后（Unicode 无下标版本）：
    - ❌ M⊙、R⊙、L⊙（圆点在基线位）
    - ✅ M<sub>⊙</sub>、R<sub>⊙</sub>、L<sub>⊙</sub>
- 中文翻译保留 GRB、AGN、SN、PSR、FRB、TDE 等约定俗成的英文缩写

报告结构：

第一部分：领域索引
- 用 <div class="index-box"> 包裹，内部首行为 <h3>领域索引</h3>
- 仅从以下固定子领域中选择，类别名严格使用中文标签和括号中的英文缩写：
    1. 伽马射线暴（GRB）
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
- 类别 12（理论、数值方法与仪器）仅用于**方法本身是主要贡献**的论文（如新数值格式、新探测器、新统计方法）；论文若是针对具体天体的数值模拟（如 GRB 喷流 MHD、AGN GRMHD），归入该天体所在类别而非类别 12
- 若全部论文都不属于某类别，则省略该类别行
- 每个非空类别输出一行，类别名**完整复制**上表中"中文（英文缩写）"原文（包括括号与斜杠），示例：
    <p><strong>伽马射线暴（GRB）：</strong> <a href="#p1">[1]</a>, <a href="#p5">[5]</a></p>
    <p><strong>活动星系核与耀变体（AGN / Blazar / Quasar）：</strong> <a href="#p3">[3]</a></p>
- 类别按上表 1-12 顺序排列

第二部分：今日重点（可选）
- 用 <div class="highlight-box"> 包裹
- 从 {paper_count} 篇论文中挑选 0-3 篇真正具有非平凡科学贡献的，宁缺毋滥。判定标准（满足任一即可）：
    - 首次观测/探测某现象、新天体、或新能段
    - 对主流理论模型给出强约束或反例
    - 跨子领域方法迁移（如 ML 应用于物理参数推断）
    - 显著挑战既有结论或经典图像
- **不要把以下当作"非平凡贡献"**：
    - 增量改进既有方法/模型（如"更新拟合流程"、"加入新一年数据"）
    - 增加样本/扩大目录（除非首次跨越关键阈值或新天体类型）
    - 重复验证既有结论而无新约束
    - 综述、仪器/巡天介绍、数据释出（DR）类
- **顶刊状态作为次要权重**：在上述科学贡献判断基础上，下列顶刊的"已发表/已接收/已投稿"状态可推升入选优先级（信息来自 Status badge HTML 字段中的期刊名）：
    - 一档：Nature、Science 主刊
    - 二档：Nature Astronomy / Nature Physics / Nature Communications / Science Advances、Physical Review Letters (PRL)
    - 其余期刊（ApJ、MNRAS、A&A、PRD 等）视为常规权重
- 状态权重的边界：
    - 顶刊背书**不能替代**科学贡献判断：仅有顶刊状态但属于增量改进/综述/数据释出类的**不入选**
    - 反之，纯预印本（无徽章）若有非平凡科学贡献仍可入选，不要因没徽章而漏选
    - 同等贡献情况下，顶刊已发表 > 已接收 > 已投稿 > 其他期刊 > 预印本
- 若全部论文都是常规进展、纯数据释出、增量改进、综述类，则**整个 highlight-box 省略**，不要写"今日无亮点"、"暂无重点"等占位句
- 结构如下（最多 3 行 <p>）：
  <div class="highlight-box">
  <h3>今日重点</h3>
  <p><a href="#pN">[N]</a> 1 句中文，明确指出该论文的非平凡之处（如"首次给出 X 现象 PeV 段上限"、"挑战 Y 主流模型"），不要复述"研究问题"原话</p>
  </div>

第三部分：论文条目
按编号 1 到 {paper_count} 顺序排列。每篇严格按以下模板（N 替换为论文编号）：

<div class="paper-item" id="pN">
<h3>[N] <a href="URL 字段值">arXiv ID 字段值</a> [粘贴 Status badge HTML 字段（处理规则见写作要求）]</h3>
<p><strong>英文标题：</strong>原英文标题</p>
<p><strong>中文标题：</strong>专业学术翻译</p>
<p><strong>作者：</strong>作者列表（超过 5 位仅列前 5 位，末尾追加 "et al."）</p>
<p><strong>研究问题：</strong>1 句中文，点出这篇文章试图回答/质疑/检验的具体科学问题或切入点</p>
<p><strong>研究方法：</strong><span class="method-tag">[Observation]</span> 2-3 句中文，直接描述核心方法与数据/模型</p>
<p><strong>研究结果：</strong>2-4 句中文，直接陈述关键物理发现，至少含一个定量数值、能段、或显著性约束</p>
<p><strong>潜在不足与延伸方向：</strong>1 句中文指出这篇文章可见的局限性；1 句中文给出具体可执行的延伸方向</p>
</div>

写作要求：
- 学术语气、信息密度高，不要写"本文研究了"、"研究人员发现"、"该工作探讨了"这类填充语
- "研究问题"必须具体：写明文章针对的具体科学争议、待检验假设、或观测/理论缺口
    - ❌ 泛背景："GRB 是宇宙中最剧烈的爆发现象"
    - ✅ 具体问题："用 NICER X 射线脉冲轮廓拟合约束中子星半径 R，区分软态与硬态核物态方程"
- 若论文确实没有非平凡的科学问题（如：纯数据释出、仪器/巡天介绍、综述类、纯方法论文），则**整行 <p><strong>研究问题：</strong>...</p> 省略**，不要写"无明确问题"、"旨在介绍 X"等占位句
- "研究方法"标签按论文主体方法判断，<span class="method-tag"> 内的标签**必须**保留方括号：
    - 纯数据分析 → [Observation]
    - 数值/MHD/N-body → [Simulation]
    - 解析推导 → [Theory]
    - 含拟合/参数推断/半解析 → [Modeling]
- "研究结果"部分必须包含具体数值（如测量值±误差、置信度上下限、能段、显著性 σ），避免泛泛"显著相关"、"有较好一致性"等空话
    - ❌ 空话："观测与模型预测一致，验证了 X 理论"
    - ✅ 具体："观测 σ_v = 250 ± 30 km/s 与 ΛCDM 预测的 240 km/s 在 1σ 内一致，将 f_NL 上限收紧至 |f_NL| < 8 (95% CL)"
- "潜在不足与延伸方向"严格遵守：
    - 只针对 abstract 明确出现的方法、样本、假设、数据来源做评论，不脑补正文细节，不批评 abstract 未提及的环节
    - 用"潜在"、"可能"、"有待"等不确定语气；禁止使用"the paper fails to / 该工作未能"等断定式负面表述
    - 不足部分要具体到 abstract 中暴露的点（如样本量 N=3、单一波段、依赖某假设、未做 follow-up 等），禁止"can be extended"、"样本可更大"这类适用于任何论文的套话
    - 延伸方向必须**可执行**：写明具体数据集/波段/方法/约束（如"用 XRISM 高分辨 Fe Kα 线轮廓检验吸积盘几何"），禁止"未来可进一步探索"这种空话
    - ❌ 套话："样本量较小，未来可扩大；可结合多波段观测"
    - ✅ 具体："仅 3 个 BL Lac 源且全部为高态阶段，对 jet-disk 关联推断存在样本偏差；用 Fermi-LAT 4FGL 中 ~50 个 LSP BL Lac 子样本可减弱该偏差"
    - 若 abstract 信息密度不足以做出**有信息量**的判断（如仅给出常规巡天数据释出、纯综述、仪器介绍），则**整行 <p><strong>潜在不足与延伸方向：</strong>...</p> 省略**，不要凑数
- "Status badge HTML" 字段已是预渲染的 HTML 片段，**逐字符原样**插入 <h3> 中 Entry ID 链接之后，不要修改 class 名、不要重写文本、不要加注释；若值为 "(none)" 则不插入任何内容
- "Status badge HTML" 字段除作为徽章渲染外，其期刊名信息还作为"今日重点"的次要权重输入（具体优先级见第二部分顶刊状态规则）

待处理论文：
{input_text}
"""


def build_prompt(papers: list[dict]) -> str:
    """Render the full prompt sent to the LLM."""
    return _PROMPT_TEMPLATE.format(
        paper_count=len(papers),
        input_text=_build_input_text(papers),
    )
