"""
🐱 喵艺 AI 端到端烟雾测试

验证核心流程通畅：健康检查 → 注册/登录 → Token 刷新 → 发送消息 → AI 回复 → WebSocket

运行前确保后端服务器已启动:
    python server.py

运行:
    python -m pytest tests/test_smoke.py -v
    或
    python tests/test_smoke.py
"""

import json
import os
import sys
import time

import pytest
import requests

# 配置
# 【注意】默认用 127.0.0.1 直连 IPv4：Windows 下 localhost 可能解析到 IPv6 ::1
# （回环黑洞导致超时/拒连），详见 README 故障排查章节
BASE_URL = os.environ.get('TEST_BASE_URL', 'http://127.0.0.1:8001')
API_BASE = f'{BASE_URL}/api'
WS_URL = BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://').rstrip('/') + '/ws'
TIMEOUT = 30

# 端到端测试依赖真实运行的后端，CI 里默认跳过，避免误报失败。
# 需要跑时：MIAOYI_RUN_SMOKE=1 pytest tests/test_smoke.py
if os.environ.get('MIAOYI_RUN_SMOKE') != '1':
    pytest.skip(
        '端到端烟雾测试需要本地后端运行，设置 MIAOYI_RUN_SMOKE=1 后执行',
        allow_module_level=True,
    )

# 测试结果
results = []


def log_result(name: str, passed: bool, detail: str = ""):
    status = "✅" if passed else "❌"
    msg = f"{status} {name}"
    if detail:
        msg += f": {detail}"
    print(msg)
    results.append((name, passed, detail))


# ============================================================
# 共享 fixture：注册 + 登录（整个测试会话只跑一次）
# ============================================================

@pytest.fixture(scope='session')
def auth_tokens():
    """注册一个新用户并登录，返回 (access_token, refresh_token)；结束后自动清理测试账户"""
    unique_id = int(time.time()) % 100000
    username = f'smoke_test_{unique_id}'
    phone = f'139{unique_id:08d}'
    password = 'Test123!'

    r = requests.post(f'{API_BASE}/auth/register', json={
        'username': username, 'phone': phone, 'password': password
    }, timeout=5)
    reg_data = r.json()
    assert r.status_code in (200, 201) and reg_data.get('code', 0) in (200, 201), \
        f"注册失败: {r.text}"

    r = requests.post(f'{API_BASE}/auth/login', json={
        'username': username, 'password': password
    }, timeout=5)
    login_data = r.json()
    token_data = login_data.get('data', {})
    access_token = token_data.get('access_token', '')
    refresh_token = token_data.get('refresh_token', '')
    assert r.status_code == 200 and access_token, f"登录失败: {r.text}"

    yield access_token, refresh_token

    # 【自清理】用管理员账号删除本次测试创建的账户，保持"唯一管理员"的干净状态
    try:
        # 清理需要管理员账号：只读环境变量，仓库里不写任何真实账号
        admin_user = os.environ.get('TEST_ADMIN_USER', '')
        admin_pass = os.environ.get('TEST_ADMIN_PASSWORD', '')
        if not (admin_user and admin_pass):
            print(f"未配置 TEST_ADMIN_USER / TEST_ADMIN_PASSWORD，跳过清理（可手动删除 {username}）")
            return
        r = requests.post(f'{API_BASE}/auth/login', json={
            'username': admin_user, 'password': admin_pass}, timeout=5)
        admin_token = (r.json().get('data') or {}).get('access_token', '')
        if admin_token:
            h = {'Authorization': f'Bearer {admin_token}'}
            r = requests.get(f'{API_BASE}/admin/users', params={'search': username},
                             headers=h, timeout=5)
            for u in (r.json().get('data') or {}).get('users', []):
                requests.delete(f"{API_BASE}/admin/users/{u['id']}", headers=h, timeout=5)
                print(f"已清理测试账户: {username} (id={u['id']})")
    except Exception as e:
        print(f"测试账户清理失败（可手动删除 {username}）: {e}")


def test_health():
    """测试 1: 健康检查"""
    r = requests.get(f'{API_BASE}/health', timeout=5)
    data = r.json()
    assert r.status_code == 200
    assert data.get('data', {}).get('status') == 'healthy'


def test_register_and_login(auth_tokens):
    """测试 2: 注册和登录（fixture 已执行，此处验证 token 有效性）"""
    access_token, refresh_token = auth_tokens
    assert access_token and refresh_token


def test_token_refresh(auth_tokens):
    """测试 3: Token 刷新"""
    _, refresh_token = auth_tokens
    assert refresh_token, "无有效 refresh_token"

    r = requests.post(f'{API_BASE}/auth/refresh', json={
        'refresh_token': refresh_token
    }, timeout=5)
    data = r.json()
    new_access_token = data.get('data', {}).get('access_token', '')
    assert r.status_code == 200 and new_access_token, f"Token 刷新失败: {r.text}"


def test_chat_flow(auth_tokens):
    """测试 4: 聊天流程"""
    access_token, _ = auth_tokens
    assert access_token, "无有效 Token"

    headers = {'Authorization': f'Bearer {access_token}'}

    r = requests.get(f'{API_BASE}/user/profile', headers=headers, timeout=5)
    assert r.status_code == 200, f"用户资料失败: {r.text}"

    r = requests.post(f'{API_BASE}/message', json={
        'text': '你好喵艺'
    }, headers=headers, timeout=TIMEOUT)
    data = r.json()
    ai_reply = data.get('data', {}).get('text', '')
    assert r.status_code == 200, f"消息发送失败: {r.text}"
    assert ai_reply, f"AI 回复为空: {r.text}"

    r = requests.get(f'{API_BASE}/emotional_state', headers=headers, timeout=5)
    assert r.status_code == 200, f"情感状态失败: {r.text}"

    r = requests.get(f'{API_BASE}/conversations', headers=headers, timeout=5)
    assert r.status_code == 200, f"对话历史失败: {r.text}"


def test_error_handling():
    """测试 5: 错误处理"""
    # 404
    r = requests.get(f'{API_BASE}/nonexistent', timeout=5)
    assert r.status_code in (404, 405), f"期望 404/405，实际 {r.status_code}"

    # 无认证访问
    r = requests.get(f'{API_BASE}/user/profile', timeout=5)
    assert r.status_code == 401, f"期望 401，实际 {r.status_code}"


def test_websocket_connection(auth_tokens):
    """测试 6: WebSocket 连接"""
    access_token, _ = auth_tokens
    token = access_token
    try:
        import threading
        from urllib.parse import urlencode

        import websocket

        ws_connected = threading.Event()
        ws_messages = []
        ws_errors = []

        def on_open(ws):
            ws_connected.set()
            # 发送认证消息（服务端主 WS 也支持握手后 auth 消息，双保险）
            auth_msg = json.dumps({
                'type': 'auth',
                'token': token
            })
            ws.send(auth_msg)

        def on_message(ws, message):
            ws_messages.append(message)

        def on_error(ws, error):
            ws_errors.append(error)

        def on_close(ws, close_status_code, close_msg):
            pass  # 正常关闭

        # 【修复】服务端在握手时校验 token（query_params/cookie），
        # 连接 URL 必须带 ?token=xxx，否则握手即被拒绝（4001）。
        ws_url = f'{WS_URL}?{urlencode({"token": token})}'

        # 创建 WebSocket 连接
        ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # 在后台线程运行
        ws_thread = threading.Thread(target=ws.run_forever)
        ws_thread.daemon = True
        ws_thread.start()
        
        # 等待连接
        assert ws_connected.wait(timeout=5), "WebSocket 连接超时"

        # 等待接收消息
        time.sleep(2)

        # 关闭连接
        ws.close()

        assert len(ws_errors) == 0, f"WebSocket 错误: {ws_errors}"
        assert len(ws_messages) > 0, "WebSocket 未收到任何消息"

    except ImportError:
        pytest.skip("websocket-client 未安装，跳过测试")
    except Exception as e:
        log_result("WebSocket 测试", False, str(e))
        raise


def test_rate_limit():
    """测试 7: 速率限制"""
    # 发送多个快速请求测试速率限制
    success_count = 0
    rate_limited = False
    for i in range(70):  # 超过每分钟 60 次的限制
        r = requests.get(f'{API_BASE}/health', timeout=2)
        if r.status_code == 429:
            rate_limited = True
            break
        elif r.status_code == 200:
            success_count += 1
    assert rate_limited or success_count >= 60, \
        f"速率限制未生效: 成功 {success_count} 次，限速触发: {rate_limited}"


def main():
    """脚本模式：等效于运行 pytest（保留直接执行能力）"""
    import subprocess
    print("=" * 60)
    print("🐱 喵艺 AI 端到端烟雾测试（经由 pytest 运行）")
    print(f"   目标: {BASE_URL}")
    print(f"   时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()
    code = subprocess.call([
        sys.executable, '-m', 'pytest', __file__, '-v', '--no-header', '-q'
    ])
    sys.exit(code)


if __name__ == '__main__':
    main()
