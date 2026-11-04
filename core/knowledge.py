"""喵艺知识库模块"""
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List

from utils.config_manager import config_manager
from utils.helpers import calculate_text_similarity, extract_topics

_log = logging.getLogger("KnowledgeBase")


class KnowledgeBase:
    """知识库系统"""

    def __init__(self):
        self.entries: list[dict] = []
        self.semantic_index = defaultdict(set)
        self.topic_index = defaultdict(set)
        self.temporal_index = defaultdict(list)
        self.tag_index = defaultdict(set)
        self.emotion_index = defaultdict(set)
        self.access_patterns = defaultdict(int)
        self.load_knowledge()

    def load_knowledge(self):
        """加载知识库"""
        knowledge_file = os.path.join(
            config_manager.get('app.data_dir', '.'),
            'miaoyi_knowledge_db.json'
        )
        if os.path.exists(knowledge_file):
            try:
                with open(knowledge_file, encoding='utf-8') as f:
                    data = json.load(f)
                    self.entries = data.get('entries', [])
                    self._rebuild_indices()
                _log.info(f"知识库加载完成，共{len(self.entries)}条知识")
            except Exception as e:
                _log.warning(f"加载知识库失败: {e}")

    def save_knowledge(self):
        """保存知识库"""
        try:
            knowledge_file = os.path.join(
                config_manager.get('app.data_dir', '.'),
                'miaoyi_knowledge_db.json'
            )
            data = {
                'entries': self.entries[-5000:],
                'last_updated': time.time()
            }
            with open(knowledge_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log.warning(f"保存知识库失败: {e}")

    def _rebuild_indices(self):
        """重建索引"""
        self.semantic_index.clear()
        self.topic_index.clear()
        self.tag_index.clear()
        for i, entry in enumerate(self.entries):
            topics = entry.get('topics', [])
            for topic in topics:
                self.topic_index[topic].add(i)
            tags = entry.get('tags', [])
            for tag in tags:
                self.tag_index[tag].add(i)
            content = entry.get('content', '')
            keywords = extract_topics(content)
            for keyword in keywords:
                self.semantic_index[keyword].add(i)

    def add_entry(self, topic: str, content: str, source: str = "user", metadata: dict = None):
        """添加知识条目"""
        entry = {
            'topic': topic,
            'content': content,
            'source': source,
            'timestamp': time.time(),
            'topics': extract_topics(content),
            'tags': [topic],
            'metadata': metadata or {},
            'access_count': 0
        }
        self.entries.append(entry)
        self._update_indices_for_entry(len(self.entries) - 1, entry)
        _log.debug(f"添加知识: {topic}")

    def _update_indices_for_entry(self, index: int, entry: dict):
        """更新索引"""
        for topic in entry.get('topics', []):
            self.topic_index[topic].add(index)
        for tag in entry.get('tags', []):
            self.tag_index[tag].add(index)
        content = entry.get('content', '')
        keywords = extract_topics(content)
        for keyword in keywords:
            self.semantic_index[keyword].add(index)

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """搜索知识"""
        query_topics = extract_topics(query)
        candidate_indices = set()
        for topic in query_topics:
            if topic in self.topic_index:
                candidate_indices.update(self.topic_index[topic])
            if topic in self.semantic_index:
                candidate_indices.update(self.semantic_index[topic])
        candidates = [self.entries[i] for i in candidate_indices if i < len(self.entries)]
        # 计算相似度并排序
        scored_candidates = []
        for candidate in candidates:
            similarity = calculate_text_similarity(query, candidate.get('content', ''))
            scored_candidates.append((similarity, candidate))
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in scored_candidates[:max_results]]

    def get_entry(self, index: int) -> dict:
        """获取指定条目"""
        if 0 <= index < len(self.entries):
            self.entries[index]['access_count'] += 1
            return self.entries[index]
        return {}

    def consolidate(self):
        """整合知识库"""
        if len(self.entries) > 5000:
            # 保留最近和最常访问的知识
            self.entries.sort(key=lambda x: (x.get('access_count', 0), x.get('timestamp', 0)), reverse=True)
            self.entries = self.entries[:5000]
            self._rebuild_indices()

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            'total_entries': len(self.entries),
            'topics_count': len(self.topic_index),
            'tags_count': len(self.tag_index),
            'semantic_index_size': len(self.semantic_index)
        }