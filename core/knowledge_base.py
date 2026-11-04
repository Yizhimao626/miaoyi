#!/usr/bin/env python3
"""
  🐱 喵艺知识库系统 - 完整迁移自main.py的KnowledgeBase
  包含语义索引+知识整合+学习管道
"""
import logging
import re
import threading
import time
from collections import defaultdict

_log = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self):
        self._knowledge = {}
        self._index = defaultdict(list)
        self._learning_log = []
        self._last_update = 0
        self._dirty = False
        self._lock = threading.Lock()

    def learn(self, topic: str, content: str, source: str = ""):
        with self._lock:
            topic = topic.strip()
            if not topic or not content:
                return
            
            key = topic.lower()
            if key not in self._knowledge:
                self._knowledge[key] = {
                    'topic': topic,
                    'content': [],
                    'sources': [],
                    'created_at': time.time(),
                    'updated_at': time.time(),
                    'access_count': 0,
                }
            
            if content not in [c['text'] for c in self._knowledge[key]['content']]:
                self._knowledge[key]['content'].append({
                    'text': content,
                    'source': source,
                    'timestamp': time.time(),
                })
            
            if source and source not in self._knowledge[key]['sources']:
                self._knowledge[key]['sources'].append(source)
            
            self._knowledge[key]['updated_at'] = time.time()
            
            keywords = self._extract_keywords(content)
            for kw in keywords[:10]:
                kw_key = kw.lower()
                if key not in self._index[kw_key]:
                    self._index[kw_key].append(key)
            
            self._learning_log.append({
                'topic': topic,
                'source': source,
                'content_length': len(content),
                'ts': time.time(),
            })
            if len(self._learning_log) > 100:
                self._learning_log.pop(0)
            
            self._dirty = True

    def recall(self, query: str) -> dict or None:
        with self._lock:
            query_lower = query.lower()
            if query_lower in self._knowledge:
                self._knowledge[query_lower]['access_count'] += 1
                return self._knowledge[query_lower]
            
            for kw in self._extract_keywords(query):
                kw_key = kw.lower()
                if kw_key in self._index:
                    for topic_key in self._index[kw_key]:
                        if topic_key in self._knowledge:
                            self._knowledge[topic_key]['access_count'] += 1
                            return self._knowledge[topic_key]
            
            return None

    def get_all_topics(self) -> list:
        with self._lock:
            return [k['topic'] for k in self._knowledge.values()]

    def consolidate(self):
        with self._lock:
            merged = {}
            for key, data in self._knowledge.items():
                topic = data['topic']
                if topic not in merged:
                    merged[topic] = {
                        'topic': topic,
                        'content': [],
                        'sources': [],
                        'created_at': data['created_at'],
                        'updated_at': data['updated_at'],
                        'access_count': data['access_count'],
                    }
                
                for content_item in data['content']:
                    if content_item['text'] not in [c['text'] for c in merged[topic]['content']]:
                        merged[topic]['content'].append(content_item)
                
                for source in data['sources']:
                    if source not in merged[topic]['sources']:
                        merged[topic]['sources'].append(source)
            
            self._knowledge = merged
            self._dirty = True

    def _extract_keywords(self, text: str) -> list:
        if not text:
            return []
        words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', text.lower())
        stop_words = {
            '什么', '怎么', '为什么', '这个', '那个', '一个', '可以', '应该', '但是', '因为',
            '所以', '就是', '还是', '已经', '然后', '吗', '呢', '啊', '哦', '的', '了', '是',
            '我', '你', '他', '她', '没有', '不是', '知道', '觉得', '可能', '需要', '那么',
            '这样', '那样', '哪里', '哪些', '自己', '他们', '我们', '你们', '它们', '如果',
            '虽然', '不过', '而且', '或者', '以及', '一些', '一直', '一样', '起来', '出来',
            '出去', '过来', '之后', '之前', '还有', '能不能', '会不会', '是不是', '其实',
            '时候', '地方', '东西', '问题', '办法', '今天', '明天', '昨天', '现在', '刚才',
        }
        return list(dict.fromkeys([w for w in words if w not in stop_words and len(w) >= 2]))[:10]

    def get_knowledge_summary(self) -> dict:
        with self._lock:
            return {
                'total_topics': len(self._knowledge),
                'total_entries': sum(len(k['content']) for k in self._knowledge.values()),
                'last_update': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._last_update)),
                'learning_count': len(self._learning_log),
                'topics': [k['topic'] for k in self._knowledge.values()][:20],
            }

    def save(self):
        with self._lock:
            if self._dirty:
                from core.miaoyi_core import save_json
                save_json("miaoyi_knowledge.json", {
                    'knowledge': self._knowledge,
                    'index': {k: list(v) for k, v in self._index.items()},
                    'learning_log': self._learning_log[-50:],
                })
                self._dirty = False

    def load(self):
        try:
            from core.miaoyi_core import load_json
            data = load_json("miaoyi_knowledge.json")
            if data:
                with self._lock:
                    self._knowledge = data.get('knowledge', {})
                    self._index = defaultdict(list, data.get('index', {}))
                    self._learning_log = data.get('learning_log', [])
                    self._last_update = time.time()
        except Exception as e:
            _log.debug(f"加载知识库失败: {e}")