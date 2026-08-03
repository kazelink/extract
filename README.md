# 表格批量提取

读取表格数据，按自定义提示词逐行调用 LLM 提取字段，并把结果写回表格。

## 功能

- 读取 CSV、Excel（.xlsx / .xlsm / .xls，保留多工作表）及文本表格
- 自定义提取提示词，支持流式输出、中途停止
- 内置模型配置：GPT-OSS-120B（NVIDIA）、DeepSeek V4 Pro / Flash
- 行间可设置间隔秒数，避免请求过于频繁
- 运行时进度条、结果筛选、批量处理自动保存

## 安装与运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 配置

复制 `config.example.json` 为 `config.local.json`，填入自己的 API Key：

- `nvidia`：NVIDIA 集成端点，用于 GPT-OSS-120B
- `deepseek`：DeepSeek API，用于 V4 Pro / Flash

`MODELS` 列表可增删模型，`DEFAULT_MODEL_ID` 指定默认模型，`LLM_TIMEOUT` 控制请求超时（秒）。
