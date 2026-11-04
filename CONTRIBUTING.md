# 贡献指南 / Contributing

感谢你愿意参与喵艺。请先读一遍本文档，能让 review 快很多。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env             # 至少填一个 LLM API Key

python server.py                 # 后端 :8001

cd frontend && npm install && npm run dev   # 前端 :5173
```

## 提交前自检

- [ ] `python -m compileall -q server.py db_manager.py core services utils` 通过
- [ ] `python tests/test_llm_pool.py` → `LLM POOL TESTS: 6/6 PASS`
- [ ] `python -m pytest tests/test_llm_call_sites.py tests/test_reminders.py -q` 全绿
- [ ] `cd frontend && npm run build && npm run test` 通过
- [ ] 没有提交 `.env` / `config.yaml` / `data/` / `assets/` / 任何密钥
- [ ] 新增逻辑有对应的测试或说明无法测试的原因
- [ ] 改动范围聚焦，没有顺手重构无关文件

## Commit 规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(voice): add CosyVoice2 fallback for call TTS
fix(ws): require token on call websocket
docs(readme): add architecture diagram
refactor(core): extract emotion state persistence
```

类型：`feat` `fix` `docs` `refactor` `test` `chore` `perf`。

## Pull Request

1. 从 `main` 拉分支，命名 `feat/xxx` 或 `fix/xxx`
2. 描述里写清 **动机**（为什么改）与 **验证方式**（怎么测的）
3. 关联相关 Issue（`Closes #12`）
4. 保持 PR 小而可 review；大改动请先开 Issue 讨论方案

## 代码风格

- Python：遵循 PEP 8，中文注释可以，但公共 API 建议中英双语
- TypeScript：沿用现有 ESLint 配置，不要引入新的全局状态管理方案
- CSS：样式集中在 `frontend/src/styles/`，避免在组件里写大段内联样式

## 行为准则

参与本项目即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
