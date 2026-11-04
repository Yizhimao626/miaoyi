# -*- coding: utf-8 -*-
"""【L8 角色卡导出】SillyTavern Character Card V2 兼容构建器

- 纯函数组装：server.py 负责收集数据，本模块只负责拼出符合 spec 的 dict
- spec: chara_card_v2 / spec_version 2.0（SillyTavern 可直接导入 .json 角色卡）
- character_book（世界书）由喵艺的语义记忆生成：key 作触发关键词、key:value 作内容，
  导入 ST 后可按关键词自动注入，还原"喵艺记得你"的体验
"""
from datetime import datetime

SPEC = 'chara_card_v2'
SPEC_VERSION = '2.0'

# SillyTavern 角色书条目默认参数
_BOOK_ENTRY_DEFAULTS = {
    'enabled': True,
    'insertion_order': 100,
    'case_sensitive': False,
    'priority': 10,
    'position': 'before_char',
    'constant': False,
    'selective': True,
    'extensions': {},
}


def _book_entry(idx: int, key: str, value: str) -> dict:
    """语义记忆 → 世界书条目"""
    entry = {
        'id': idx,
        'keys': [key],
        'name': key,
        'content': f'{key}：{value}',
    }
    entry.update(_BOOK_ENTRY_DEFAULTS)
    return entry


def build_character_card(
    *,
    name: str,
    description: str,
    personality: str,
    scenario: str,
    first_mes: str,
    system_prompt: str,
    memories: list[dict] | None = None,
    creator_notes: str = '',
    tags: list[str] | None = None,
    miaoyi_extensions: dict | None = None,
) -> dict:
    """组装 chara_card_v2 角色卡

    memories: [{'key': str, 'value': str}] —— 喵艺对用户的语义记忆
    miaoyi_extensions: 附加到 data.extensions.miaoyi 的元数据（关系阶段等）
    """
    memories = memories or []
    book_entries = [
        _book_entry(i, str(m.get('key', '')), str(m.get('value', '')))
        for i, m in enumerate(memories) if m.get('key') and m.get('value')
    ]

    data = {
        'name': name,
        'description': description,
        'personality': personality,
        'scenario': scenario,
        'first_mes': first_mes,
        'mes_example': '',
        'creator_notes': creator_notes,
        'system_prompt': system_prompt,
        'post_history_instructions': '',
        'alternate_greetings': [],
        'character_book': {
            'name': f'{name}的记忆',
            'entries': book_entries,
        },
        'tags': tags or [],
        'creator': 'Miaoyi',
        'character_version': '1.0',
        'extensions': {
            'miaoyi': miaoyi_extensions or {},
        },
    }
    return {
        'spec': SPEC,
        'spec_version': SPEC_VERSION,
        'data': data,
    }


def export_file_name(user_id: str) -> str:
    """导出文件名：character_card_{user}_{时间戳}.json"""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_user = ''.join(ch for ch in str(user_id) if ch.isalnum())[:20] or 'user'
    return f'character_card_{safe_user}_{ts}.json'
