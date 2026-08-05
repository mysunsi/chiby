# 模型设置

入口：右上角菜单 → **模型设置**（或右侧模型齿轮按钮）。

## 可配置项

- **显示名称**：状态栏/界面上的友好名称  
- **模式**：内置链 或 自定义 OpenAI 兼容 API  
- **API Base URL**：如 `https://api.deepseek.com`、`http://127.0.0.1:11434/v1`  
- **API Key**：可留空以保留已保存密钥；可勾选清除  
- **模型名 model**：如 `gpt-4o-mini`、`llama3.2`  
- **Temperature / Max tokens**：采样与长度  
- **高级**：部分推理模型可开关 thinking 相关参数  

预设芯片（OpenAI / DeepSeek / MiniMax / Ollama / vLLM）可一键填充常用 URL。

## 保存后

点击 **保存并重载**。成功后右侧模型状态应变为已配置；自然语言能力依赖 Key 与网络可达。

## 文件位置

配置写入工作目录 `data/llm_config.json`（及模型列表相关文件）。也可用环境变量（如 `OPENAI_API_KEY`）辅助，以实际界面与服务端合并逻辑为准。
