给 Codex 的需求说明书

请帮我开发一个 基于 Python + WindPy 的上证50ETF/上证50相关期权可视化监控程序，面向日内交易使用。程序需要具备实时刷新、期权链监控、Gamma/IV/持仓结构可视化、Pin/Break 状态识别等功能。整体要求不是 demo，而是一个可运行、可扩展、结构清晰的桌面级分析工具。

1. 项目目标

开发一个本地运行的 Python 可视化程序，用于：

接入 WindPy，实时获取上证50相关现货/指数/期权数据

每 5 秒刷新一次关键行情和期权链

实时展示：

标的价格

ATM IV

期权链（Call/Put）

各执行价持仓量、成交量、IV、Greeks

Max Pain / Gamma Center / OI Center

关键行权价磁吸区

Gamma Pin / Gamma Break 状态判断

支持临近到期交易观察，尤其是 T-5 到 T-0 的短期限结构

具备较好的工程结构，便于后续继续扩展

2. 主要使用场景

这是一个给日内期权交易者使用的实时监控面板，主要解决以下问题：

当前价格是否被某个 strike 吸附

哪个 strike 是最大持仓中心

Call/Put 在哪些执行价上高度集中

Gamma 主要集中在哪里

当前 ATM IV 是高是低

是否接近 gamma flip

当前更像 pin day 还是 trend day

到期前几天是否适合做均值回归还是方向突破

3. 技术栈要求

请优先使用以下技术栈：

Python 3.11+

WindPy

pandas

numpy

PySide6 或 PyQt6

pyqtgraph 或 matplotlib

threading / asyncio（二选一，但要稳定）

dataclasses / pydantic 均可

logging 模块

说明：

如果桌面 GUI 实现较复杂，可以先做一个 PySide6 主界面 + pyqtgraph 图表 版本

不接受只用 Jupyter notebook 的方案

不接受单文件脚本堆砌，必须模块化

4. 数据源要求

通过 WindPy 获取以下数据。

4.1 标的数据

需要支持以下标的中的一种或多种，尽量做成可配置：

上证50ETF

上证50指数

相关股指/ETF（后续可扩展）

标的数据需要包括：

最新价

涨跌额

涨跌幅

昨收

分时/K线数据

成交量

可能的话加买一卖一

4.2 期权链数据

针对指定到期月份，获取完整期权链：

合约代码

合约简称

Call / Put

执行价

到期日

剩余交易日/自然日

最新价

涨跌额

涨跌幅

成交量

持仓量

隐含波动率 IV

Delta

Gamma

Vega

Theta

行权价对应的买卖盘（如果 Wind 可取到）

乘数

优先关注最近月 / 当月合约，但要支持切换到次月。

4.3 刷新频率

默认每 5 秒刷新一次，但请设计成可配置：

行情刷新：5 秒

静态合约信息刷新：60 秒或更长

K线刷新：10 秒

界面刷新：与数据解耦

5. 核心功能模块
5.1 主界面布局

请实现一个主窗口，建议分为以下区域：

A. 顶部总览区

展示：

当前时间

Wind 连接状态

标的代码/名称

标的最新价

涨跌幅

到期日期

剩余天数

ATM strike

ATM IV

Max Pain

Gamma Center

OI Center

当前状态标签：Pin / Break / Neutral

B. 左中：期权链表格

显示当月完整期权链，按 strike 排序，中间是执行价，左边 Put，右边 Call。字段参考交易软件风格：

Put侧：

Theta

Delta

Gamma

Vega

IV变化

MIV/IV

持仓量

成交量

涨跌

最新价

中间：

Strike

Call侧：

最新价

涨跌

成交量

持仓量

IV

MIV/IV变化

Vega

Gamma

Delta

Theta

要求：

ATM 行高亮

最大 OI strike 高亮

最大 Gamma strike 高亮

价格接近的 strike 高亮

支持颜色区分 Call/Put

支持排序和筛选

C. 右中：结构指标区

显示：

Put OI 分布

Call OI 分布

净 OI 分布

Gamma Exposure 近似分布（如果无法精确做 dealer GEX，可先做简化版）

成交量分布

IV skew（按 strike）

D. 底部：图表区

至少包括以下图表：

标的分时图 / 5分钟K线

OI 柱状图（按 strike）

Gamma 柱状图（按 strike）

IV 曲线（按 strike）

Max Pain / Gamma Center / Spot 的相对位置图

6. 核心计算逻辑
6.1 ATM strike 识别

根据标的最新价，自动识别最近的 ATM strike。

6.2 Max Pain 计算

根据各执行价 Call/Put 持仓量，计算 max pain strike。

要求：

代码写成独立函数

可以切换是否按 OI 或 OI × 合约乘数处理

结果展示在界面顶部

6.3 OI Center

定义：

Call 最大持仓执行价

Put 最大持仓执行价

总 OI 最大执行价

6.4 Gamma Center

根据各 strike 的 gamma 和 OI 估算 gamma concentration。

简化方式允许如下：

对每个 strike，计算近似 gamma weight：

abs(gamma) * OI * multiplier

分别统计 Call / Put

汇总形成“Gamma Center”

如果 Wind 无法稳定提供全量 gamma，可用：

从 Wind 直接取 gamma

如缺失则做容错

不要求自己手写 Black-Scholes 反推 gamma，但请预留接口

6.5 IV 结构

需要计算和展示：

ATM IV

Call wing IV

Put wing IV

简单 skew 指标，例如：

25-delta put IV - 25-delta call IV

或者按固定 strike 距离近似

IV rank / intraday IV change 可先做简化版

6.6 Pin / Break 状态判定

这是策略层最重要的逻辑，请实现一个简单但可解释的判定器。

输入包括：

当前价格与 Max Pain / Gamma Center 的距离

最近若干分钟价格波动率

OI/Gamma 是否在单一 strike 高度集中

当前是否接近到期

突破时是否伴随量能放大

输出一个状态标签：

PIN

NEUTRAL

BREAK_UP

BREAK_DOWN

WATCH_BREAK

建议先用规则法，不要上机器学习。

可以参考以下逻辑：

若价格位于 gamma center 附近，且 OI 高度集中，且剩余期限 <= 5 天，则偏向 PIN

若价格显著偏离 gamma center，且成交放大，且短周期趋势增强，则偏向 BREAK_UP / BREAK_DOWN

若价格接近关键 strike 但尚未确认，则 WATCH_BREAK

请把规则写清楚并集中在一个独立模块中，方便我后续调整。

7. 工程结构要求

请按模块化设计，建议目录结构如下：

project/
  main.py
  config/
    settings.yaml
  data/
    wind_client.py
    option_loader.py
    quote_updater.py
  core/
    models.py
    calculations.py
    signal_engine.py
  ui/
    main_window.py
    option_chain_widget.py
    charts_widget.py
    summary_panel.py
  utils/
    logger.py
    helpers.py
    time_utils.py
  tests/
    test_calculations.py
    test_signal_engine.py

要求：

不要把所有逻辑写进 main.py

Wind API 调用与计算逻辑分离

计算逻辑与 UI 分离

UI 只负责展示，不要在 UI 里写复杂计算

8. Wind 接口设计要求

请封装一个 WindClient 类，负责：

启动 Wind 连接

检查连接状态

获取标的实时行情

获取指定到期月份的期权链

获取期权 Greeks / IV / OI / 成交量

获取分时或 K线数据

要求：

有异常处理

有重试机制

Wind 返回空值时不要让程序崩溃

对字段映射做统一处理

输出 pandas DataFrame

9. 配置要求

请把以下参数放进配置文件，而不是写死：

标的代码

期权市场代码

默认到期月份偏移（0=当月，1=次月）

刷新频率

图表时间窗口

Pin 判定阈值

Break 判定阈值

是否显示所有 strike

是否只显示 ATM 附近若干档

建议使用 settings.yaml。

10. UI 交互要求

需要支持以下交互：

切换标的

切换到期月份

手动刷新

自动刷新开关

切换图表时间粒度（分时 / 1m / 5m）

只显示 ATM 附近 ±N 档执行价

导出当前期权链到 CSV

导出图表截图

11. 性能要求

每 5 秒刷新一次时，界面不能明显卡顿

不要在每次刷新时全量重建所有控件

表格增量更新优先

图表局部更新优先

大量计算不要阻塞 UI 主线程

需要基本日志，方便排查 Wind 延迟或字段异常

12. 容错与异常处理

程序应处理以下问题：

Wind 未启动

Wind 登录失效

某些期权合约返回空值

某些 Greeks 暂时缺失

网络抖动导致单次请求失败

到期月份切换时合约链为空

刷新中用户切换月份

要求：

不崩溃

有明确状态提示

日志里能看到错误原因

13. 首版交付范围（MVP）

请先实现一个 MVP 版本，包括：

Wind 连接

标的实时价格获取

当月期权链获取

期权链表格展示

ATM、Max Pain、OI Center 计算

OI 柱状图

IV 曲线

每 5 秒自动刷新

Pin / Break 状态标签

简单日志和异常处理

完成 MVP 后，再继续扩展：

Gamma Center

更复杂的状态判定

分时图联动

多月份对比

数据缓存

本地历史存储

14. 代码风格要求

代码清晰，注释充分

函数尽量短小

关键公式写注释

增加类型注解

尽量写基础单元测试

避免魔法数字

不要只给我伪代码，要给可运行代码

如果某个 Wind 字段名不确定，请在代码中集中列出待确认字段并方便修改

15. 交付方式要求

请按以下顺序输出：

第一步

先给我项目整体方案：

架构说明

模块说明

数据流说明

UI 布局草图说明

第二步

给我完整项目代码，分文件输出

第三步

给我运行说明：

依赖安装

WindPy 环境要求

启动方法

常见错误排查

第四步

给我后续扩展建议：

dealer gamma exposure 更精确算法

历史 IV 存储

到期日预警

多标的支持

给 Codex 的额外强调

请不要把这个项目写成一个简单脚本。我需要的是一个：

可运行

可扩展

有清晰结构

有基础 UI

有实时刷新能力

可以直接继续迭代

如果某些 Wind 字段名不确定，请先把字段映射集中到一个文件里，便于我手工调整，而不是把字段名散落到代码各处。