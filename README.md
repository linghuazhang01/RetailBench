# RetailBench

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**RetailBench** 是一个零售经营仿真与智能体评测基准，用于评估AI智能体在复杂零售环境中的决策能力。

**RetailBench** is a retail operation simulation and agent evaluation benchmark for assessing AI agent decision-making in complex retail environments.

---

## 项目简介 / Overview

RetailBench 模拟真实的零售门店经营场景，智能体需要根据历史数据、市场动态、客户评价和新闻事件，做出补货、定价和库存管理决策，最终以净利润和资产增长率为评价指标。

RetailBench simulates real-world retail store operations where agents must make ordering, pricing, and inventory management decisions based on historical data, market dynamics, customer reviews, and news events, evaluated by net profit and asset growth rate.

### 核心特性 / Key Features

- 🏪 **真实仿真环境** - 包含库存管理、供应商关系、客户评价、市场新闻等多维度因素
- 🤖 **多种智能体架构** - 支持Strategy-Execution、Plan-and-Act、Reflection等多种Agent模式
- 📊 **完整的评估体系** - 净利润、资产增长率、过期率、退货率等多指标评估
- 🔧 **可配置难度** - 支持easy/middle/hard三种难度配置
- 📈 **丰富的分析工具** - 内置数据分析和可视化脚本

- 🏪 **Realistic Simulation** - Multi-dimensional factors including inventory, suppliers, reviews, and news
- 🤖 **Multiple Agent Architectures** - Support for Strategy-Execution, Plan-and-Act, Reflection, etc.
- 📊 **Comprehensive Evaluation** - Multi-metric assessment: net profit, growth rate, expiration rate, return rate
- 🔧 **Configurable Difficulty** - Three difficulty levels: easy/middle/hard
- 📈 **Rich Analysis Tools** - Built-in data analysis and visualization scripts

---

## 快速开始 / Quick Start

### 安装 / Installation

```bash
# 克隆仓库
git clone https://github.com/Ice-Moon-28/RetailBench.git
cd RetailBench

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 基础使用 / Basic Usage

```bash
# 运行非LLM基线（验证环境）
python3 retail_environment.py --mode logic --days 7 --config-type still_middle

# 运行LLM智能体
python3 run_env.py \
  --model qwen-plus \
  --config_type still_middle \
  --max_days 30 \
  --api_key YOUR_API_KEY \
  --base_url YOUR_BASE_URL
```

---

## 项目结构 / Project Structure

```
RetailBench/
├── retail_environment.py      # 核心仿真环境
├── run_env.py                 # 主要Agent入口
├── run_plan_and_act.py        # Plan-and-Act Agent
├── run_reflection.py          # Reflection Agent
├── inventory.py               # 库存管理
├── sku.py                     # SKU模型
├── module/                    # 业务模块
├── model/                     # 评分模型
├── util/                      # 工具函数
├── data/                      # 仿真数据
├── analysis/                  # 分析脚本
└── paper/                     # 论文相关
```

---

## 配置选项 / Configuration Options

| 配置类型 | 说明 | Description |
|---------|------|-------------|
| `still_middle` | 静态中等难度 | Static medium difficulty |
| `still_hard` | 静态高难度 | Static hard difficulty |
| `dynamic_hard` | 动态高难度 | Dynamic hard difficulty |

---

## 评估指标 / Evaluation Metrics

- 💰 **净利润** (Net Profit)
- 📈 **资产增长率** (Asset Growth Rate)
- 📦 **库存周转率** (Inventory Turnover)
- ⏰ **过期率** (Expiration Rate)
- 🔄 **退货率** (Return Rate)

---

## 文档 / Documentation

- [中文文档](README.md) | [English Documentation](README_En.md)
- [完整使用指南](README.md) | [Full Usage Guide](README_En.md)
- [论文](paper/) | [Paper](paper/)

---

## 贡献 / Contributing

欢迎提交 Issue 和 Pull Request！

Issues and Pull Requests are welcome!

---

## 许可证 / License

本项目采用 [MIT License](LICENSE) 开源协议。

This project is licensed under the [MIT License](LICENSE).

---

## 联系方式 / Contact

- **项目地址**: [https://github.com/Ice-Moon-28/RetailBench](https://github.com/Ice-Moon-28/RetailBench)
- **问题反馈**: [GitHub Issues](https://github.com/Ice-Moon-28/RetailBench/issues)

---

## 致谢 / Acknowledgments

感谢所有为本项目做出贡献的开发者和研究人员。

Thanks to all developers and researchers who have contributed to this project.
