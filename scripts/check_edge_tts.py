#!/usr/bin/env python3
"""晓晓在线语音（edge-tts）连通性检查

用法：
  1. 设置代理环境变量（任选其一）：
     - PowerShell:  $env:HTTPS_PROXY = "http://代理地址:端口"
     - CMD:         set HTTPS_PROXY=http://代理地址:端口
     - 或在项目 .env 中添加：HTTPS_PROXY=http://代理地址:端口
  2. 运行本脚本：python scripts/check_edge_tts.py
  3. 显示 ✅ 后，启动 server.py 即可使用晓晓在线女声。
"""
import asyncio
import os
import sys
import tempfile


def main() -> int:
    print("=" * 52)
    print("  晓晓在线语音（edge-tts）连通性检查")
    print("=" * 52)

    proxy = (
        os.environ.get('HTTPS_PROXY')
        or os.environ.get('HTTP_PROXY')
        or os.environ.get('https_proxy')
        or os.environ.get('http_proxy')
    )

    if not proxy:
        print("未检测到代理环境变量（HTTPS_PROXY/HTTP_PROXY）。")
        print("请先配置代理（微软语音服务对中国大陆 IP 有区域限制）：")
        print("  PowerShell:  $env:HTTPS_PROXY = \"http://代理地址:端口\"")
        print("  或 .env 添加: HTTPS_PROXY=http://代理地址:端口")
        return 1

    print(f"检测到代理: {proxy}")

    try:
        import edge_tts
    except ImportError:
        print("edge-tts 未安装，请执行: pip install edge-tts")
        return 1

    async def _test() -> bool:
        try:
            communicate = edge_tts.Communicate(
                "你好，我是晓晓，很高兴认识你。",
                "zh-CN-XiaoxiaoNeural",
                proxy=proxy,
            )
            out = os.path.join(tempfile.gettempdir(), "miaoyi_edge_check.mp3")
            await asyncio.wait_for(communicate.save(out), timeout=15)
            size = os.path.getsize(out)
            try:
                os.unlink(out)
            except OSError:
                pass
            return size > 500
        except Exception as e:
            print(f"合成失败: {type(e).__name__}: {str(e)[:120]}")
            return False

    ok = asyncio.run(_test())
    if ok:
        print("✅ 晓晓在线语音可用！启动 server.py 后即可使用。")
        print("   日志出现「edge-tts 文件合成成功」即生效。")
        return 0
    print("❌ 代理不可用或无法访问微软服务，请更换代理后重试。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
