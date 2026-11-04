# -*- coding: utf-8 -*-
"""【L8 插件 manifest 机制】轻量人设扩展包管理器

设计：
- 插件 = plugins/<id>/manifest.json 单文件（fragments 内联，免 entry 文件）
- manifest 字段：id/name/version/description/author/enabled/fragments[]
- fragment: {position: 'append', text: str} —— v1 仅支持 append 到 system prompt 动态区尾部
- 校验：必填字段、id 与目录名一致、fragment 必须含非空 text；坏 manifest 跳过并告警，不挂主链路
- 注入点（由 miaoyi_core 调用）：两条 system_prompt 链路的动态区尾部，
  严禁插入 PERSONA_STATIC_CORE 静态前缀中间（会打断智谱前缀缓存）
"""
import json
import logging
import os
import threading

_log = logging.getLogger("PluginManager")

# v1 支持的 fragment 注入位置（预留扩展）
_ALLOWED_POSITIONS = {'append'}

_REQUIRED_FIELDS = ('id', 'name', 'version')


class PluginManager:
    """扫描 plugins/ 目录，加载 manifest 并提供启用片段"""

    def __init__(self, plugins_dir: str | None = None):
        if plugins_dir is None:
            plugins_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugins')
        self._dir = plugins_dir
        self._lock = threading.Lock()
        # id -> {'manifest': dict, 'path': str}
        self._plugins: dict[str, dict] = {}
        self.scan()

    # ------------------------------------------------------------------
    def scan(self) -> int:
        """全量扫描插件目录，返回成功加载数"""
        loaded = 0
        result: dict[str, dict] = {}
        try:
            if not os.path.isdir(self._dir):
                os.makedirs(self._dir, exist_ok=True)
                _log.info(f"[插件] 目录不存在，已创建: {self._dir}")
            for name in sorted(os.listdir(self._dir)):
                mpath = os.path.join(self._dir, name, 'manifest.json')
                if not os.path.isfile(mpath):
                    continue
                try:
                    with open(mpath, encoding='utf-8') as f:
                        manifest = json.load(f)
                    ok, reason = self._validate(manifest, name)
                    if not ok:
                        _log.warning(f"[插件] 跳过无效 manifest {name}: {reason}")
                        continue
                    result[manifest['id']] = {'manifest': manifest, 'path': mpath}
                    loaded += 1
                except Exception as e:
                    _log.warning(f"[插件] 加载失败 {name}: {e}")
        except Exception as e:
            _log.error(f"[插件] 扫描目录失败: {e}")
        with self._lock:
            self._plugins = result
        _log.info(f"[插件] 已加载 {loaded} 个插件")
        return loaded

    @staticmethod
    def _validate(manifest: dict, dirname: str) -> tuple[bool, str]:
        if not isinstance(manifest, dict):
            return False, 'manifest 不是对象'
        for field in _REQUIRED_FIELDS:
            if not str(manifest.get(field, '')).strip():
                return False, f'缺少必填字段 {field}'
        if manifest['id'] != dirname:
            return False, f"id({manifest['id']}) 与目录名({dirname})不一致"
        fragments = manifest.get('fragments', [])
        if not isinstance(fragments, list):
            return False, 'fragments 必须是数组'
        for i, frag in enumerate(fragments):
            if not isinstance(frag, dict) or not str(frag.get('text', '')).strip():
                return False, f'fragments[{i}] 缺少非空 text'
            if frag.get('position', 'append') not in _ALLOWED_POSITIONS:
                return False, f"fragments[{i}] 不支持的 position: {frag.get('position')}"
        return True, ''

    # ------------------------------------------------------------------
    def list_plugins(self) -> list[dict]:
        """对外安全视图（不含内部路径）"""
        with self._lock:
            plugins = sorted(self._plugins.values(), key=lambda p: p['manifest']['id'])
        return [{
            'id': p['manifest']['id'],
            'name': p['manifest']['name'],
            'version': p['manifest']['version'],
            'description': p['manifest'].get('description', ''),
            'author': p['manifest'].get('author', ''),
            'enabled': bool(p['manifest'].get('enabled', False)),
            'fragment_count': len(p['manifest'].get('fragments', [])),
        } for p in plugins]

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        """切换启用状态并写回 manifest.json"""
        with self._lock:
            entry = self._plugins.get(plugin_id)
        if not entry:
            return False
        try:
            manifest = entry['manifest']
            manifest['enabled'] = bool(enabled)
            with open(entry['path'], 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            _log.error(f"[插件] 写回 manifest 失败 {plugin_id}: {e}")
            return False

    def get_active_fragments(self) -> list[str]:
        """启用插件的文本片段（按插件 id 排序保证 prompt 稳定）"""
        texts: list[tuple[str, str]] = []
        with self._lock:
            plugins = sorted(self._plugins.values(), key=lambda p: p['manifest']['id'])
        for p in plugins:
            m = p['manifest']
            if not m.get('enabled', False):
                continue
            for frag in m.get('fragments', []):
                text = str(frag.get('text', '')).strip()
                if text:
                    texts.append((m['id'], text))
        if not texts:
            return []
        return [t for _, t in texts]
