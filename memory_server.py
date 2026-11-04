# -*- coding: utf-8 -*-
"""【L9 记忆服务独立进程】三分离架构：聊天服务 / 记忆服务 / 存储

独立运行的 FastAPI 进程，内部持有 UnifiedMemorySystem（直连同一数据库），
向主服务（server.py）提供 HTTP 记忆读写 API。多个主服务实例 / 多端接入
（桌面端、手机端、未来其他客户端）共享同一份记忆，实现跨端记忆共享。

启动：
    python memory_server.py            # 端口取 config.yaml memory.remote.url 中的端口，默认 8002
    python memory_server.py --port 8002

鉴权：请求头 X-Memory-Token 需与 .env MEMORY_TOKEN（默认 miaoyi-memory-local）一致。
本服务设计为部署在受信任的内网/本机环境（127.0.0.1），token 为进程间预共享密钥。
"""
import argparse
import logging
import os
import sys

# 与 server.py 相同：加载 .env（MYSQL_PASSWORD / MEMORY_TOKEN）
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from core.memory_system import UnifiedMemorySystem
from db_manager import DBManager

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("MemoryServer")

app = FastAPI(title='Miaoyi Memory Server', version='1.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

MEMORY_TOKEN = os.environ.get('MEMORY_TOKEN', 'miaoyi-memory-local')

# 记忆服务进程独享 DB 连接与记忆实例（进程内单例）
DB = DBManager(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'miaoyi.db'))
MEMORY = UnifiedMemorySystem(DB)


def _auth(x_memory_token: str = Header(default='')):
    """进程间预共享密钥校验（内网部署场景）"""
    if MEMORY_TOKEN and x_memory_token != MEMORY_TOKEN:
        raise HTTPException(status_code=401, detail='memory token mismatch')
    return True


@app.get('/health')
def health():
    return {'code': 200, 'data': True}


@app.post('/remember')
def remember(body: dict, _: bool = Depends(_auth)):
    MEMORY.remember(str(body.get('key', '')), body.get('value'), user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': True}


@app.post('/forget')
def forget(body: dict, _: bool = Depends(_auth)):
    ok = MEMORY.forget(str(body.get('key', '')), user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': bool(ok) if ok is not None else True}


@app.post('/extract_from_text')
def extract_from_text(body: dict, _: bool = Depends(_auth)):
    got = MEMORY.extract_from_text(str(body.get('text', '')), user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': got}


@app.post('/add_conv_summary')
def add_conv_summary(body: dict, _: bool = Depends(_auth)):
    MEMORY.add_conv_summary(str(body.get('user_text', '')), str(body.get('bot_text', '')),
                            intent=str(body.get('intent', '')), emotion=str(body.get('emotion', '')),
                            user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': True}


@app.get('/facts')
def get_facts(user_id: str = '', _: bool = Depends(_auth)):
    return {'code': 200, 'data': MEMORY.get_facts(user_id=user_id)}


@app.get('/relevant')
def get_relevant(query: str = '', limit: int = 5, user_id: str = '', _: bool = Depends(_auth)):
    got = MEMORY.get_relevant_memories(query, limit=int(limit), user_id=user_id or None)
    return {'code': 200, 'data': got}


@app.get('/context_str')
def get_context_str(user_id: str = '', _: bool = Depends(_auth)):
    return {'code': 200, 'data': MEMORY.get_context_str(user_id=user_id)}


@app.get('/profile')
def get_profile(user_id: str = '', _: bool = Depends(_auth)):
    return {'code': 200, 'data': MEMORY.get_user_profile(user_id=user_id)}


@app.get('/bundle')
def get_bundle(user_id: str = '', current_text: str = '', _: bool = Depends(_auth)):
    memory_ctx, user_profile, facts = MEMORY.get_memory_context_bundle(
        user_id=user_id, current_text=current_text)
    return {'code': 200, 'data': {'memory_ctx': memory_ctx, 'user_profile': user_profile, 'facts': facts}}


@app.get('/stats')
def get_stats(_: bool = Depends(_auth)):
    return {'code': 200, 'data': MEMORY.get_stats()}


@app.post('/semantic/forget')
def semantic_forget(body: dict, _: bool = Depends(_auth)):
    ok = MEMORY.semantic.forget(str(body.get('key', '')), user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': bool(ok)}


@app.post('/semantic/update_value')
def semantic_update_value(body: dict, _: bool = Depends(_auth)):
    ok = MEMORY.semantic.update_value(str(body.get('key', '')), body.get('value'),
                                      user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': bool(ok)}


@app.post('/semantic/set_pinned')
def semantic_set_pinned(body: dict, _: bool = Depends(_auth)):
    ok = MEMORY.semantic.set_pinned(str(body.get('key', '')), bool(body.get('pinned', False)),
                                    user_id=str(body.get('user_id', '')))
    return {'code': 200, 'data': bool(ok)}


@app.post('/shutdown')
def shutdown(_: bool = Depends(_auth)):
    MEMORY.shutdown()
    return {'code': 200, 'data': True}


@app.on_event('shutdown')
def _on_shutdown():
    try:
        MEMORY.shutdown()
        DB.close()
    except Exception as e:
        _log.warning(f"记忆服务退出清理失败: {e}")


def _default_port() -> int:
    """从 config.yaml memory.remote.url 解析默认端口，缺省 8002"""
    try:
        import yaml
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml'), encoding='utf-8') as f:
            url = ((yaml.safe_load(f) or {}).get('memory', {}) or {}).get('remote', {}).get('url', '')
        return int(url.rsplit(':', 1)[1]) if url and url.rsplit(':', 1)[1].isdigit() else 8002
    except Exception:
        return 8002


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='喵艺记忆服务独立进程')
    parser.add_argument('--port', type=int, default=_default_port())
    args = parser.parse_args()
    import uvicorn
    _log.info(f"记忆服务启动: http://127.0.0.1:{args.port}（三分离架构：记忆服务独立进程）")
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='info')
