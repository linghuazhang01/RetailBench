# RetailBench

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**RetailBench** is a retail operation simulation and agent evaluation benchmark for assessing AI agent decision-making in complex retail environments.

---

## Overview

RetailBench simulates real-world retail store operations where agents must make ordering, pricing, and inventory management decisions based on historical data, market dynamics, customer reviews, and news events, evaluated by net profit and asset growth rate.

### Key Features

- 🏪 **Realistic Simulation** - Multi-dimensional factors including inventory management, supplier relationships, customer reviews, and market news
- 🤖 **Multiple Agent Architectures** - Support for Strategy-Execution, Plan-and-Act, Reflection, and other agent patterns
- 📊 **Comprehensive Evaluation** - Multi-metric assessment: net profit, growth rate, expiration rate, return rate
- 🔧 **Configurable Difficulty** - Three difficulty levels: easy/middle/hard
- 📈 **Rich Analysis Tools** - Built-in data analysis and visualization scripts

---

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/Ice-Moon-28/RetailBench.git
cd RetailBench

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

```bash
# Run non-LLM baseline (verify environment)
python3 retail_environment.py --mode logic --days 7 --config-type still_middle

# Run LLM agent
python3 run_env.py \
  --model qwen-plus \
  --config_type still_middle \
  --max_days 30 \
  --api_key YOUR_API_KEY \
  --base_url YOUR_BASE_URL
```

---

## Project Structure

```
RetailBench/
├── retail_environment.py      # Core simulation environment
├── run_env.py                 # Main agent entry point
├── run_plan_and_act.py        # Plan-and-Act agent
├── run_reflection.py          # Reflection agent
├── inventory.py               # Inventory management
├── sku.py                     # SKU modeling
├── module/                    # Business modules
├── model/                     # Rating models
├── util/                      # Utility functions
├── data/                      # Simulation data
├── analysis/                  # Analysis scripts
└── paper/                     # Paper-related files
```

---

## Configuration Options

| Config Type | Description |
|-------------|-------------|
| `still_middle` | Static medium difficulty |
| `still_hard` | Static hard difficulty |
| `dynamic_hard` | Dynamic hard difficulty |

---

## Evaluation Metrics

- 💰 **Net Profit** - Total profit generated
- 📈 **Asset Growth Rate** - Percentage increase in net worth
- 📦 **Inventory Turnover** - How quickly inventory is sold
- ⏰ **Expiration Rate** - Percentage of expired items
- 🔄 **Return Rate** - Percentage of returned items

---

## Documentation

- [中文文档](README.md) | [English Documentation](README_En.md)
- [Full Usage Guide](README.md) | [完整使用指南](README.md)
- [Paper](paper/)

---

## Contributing

Issues and Pull Requests are welcome!

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Contact

- **Repository**: [https://github.com/Ice-Moon-28/RetailBench](https://github.com/Ice-Moon-28/RetailBench)
- **Issue Tracker**: [GitHub Issues](https://github.com/Ice-Moon-28/RetailBench/issues)

---

## Acknowledgments

Thanks to all developers and researchers who have contributed to this project.
