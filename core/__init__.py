"""喵艺核心模块

【Phase 2 整理说明】原 __init__.py 导出了一批符号（KnowledgePipeline/IntelligentRouter 等），
但经全项目搜索确认：没有任何外部代码通过 `from core import xxx` 使用这些导出。
所有外部调用都是直接 `from core.miaoyi_core import ...` 等具名导入。
因此 __init__.py 的导出语句是死代码，且会阻塞归档 knowledge_pipeline.py / message_router.py
（因为 Python 导入 core 任何子模块时会先执行 __init__.py）。
故清空为空包，保留 docstring 说明。
"""
