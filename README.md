这是一个自然语言驱动的全流程软件开发Agent平台，用户用自然语言描述需求，系统会自动完成：需求分析 → PRD → 概要设计 → 数据库设计 → 后端代码 → 前端代码 → 代码审查 → 部署配置的完整流水线。

技术栈：
- LLM: DeepSeek API (Chat)
- Embedding: 智谱 Zhipu API (embedding-3)
- 后端: FastAPI + LangGraph + SQLAlchemy
- 向量库: ChromaDB (持久化)
- 前端: Streamlit
- 数据库: SQLite

run.py 通过 subprocess 同时启动两个服务：
1. FastAPI 后端 (uvicorn backend.main:app) — 端口 8000
2. Streamlit 前端 (streamlit run frontend/app.py) — 端口 8501
