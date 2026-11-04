#!/usr/bin/env python3
"""
  🐱 喵艺情绪记忆联想系统 - 基于本地轻量向量的语义级记忆触发
"""
import logging
import os
import threading
from typing import Dict, List

import numpy as np

_log = logging.getLogger(__name__)

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    _log.warning("[情绪记忆] jieba未安装，将使用简单分词")


class EmotionalMemory:
    MODEL_NAME = 'paraphrase-MiniLM-L6-v2'
    
    def __init__(self, db_manager, model_cache_dir: str = None, auto_download: bool = True, enabled: bool = True):
        self._db = db_manager
        self._model = None
        self._model_available = False
        self._model_lock = threading.Lock()  # 惰性加载线程锁，防止并发重复加载
        self._memory_cache = {}
        self._vector_dir = None
        self._model_cache_dir = model_cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models'
        )
        self._auto_download = auto_download
        self._enabled = enabled

        # 【启动提速】不再在 __init__ 同步加载向量模型（SentenceTransformer + torch
        # 加载需 1-2 分钟）。改为首次真正使用向量时才惰性加载（_ensure_model），
        # 让后端 15 秒内完成启动；未加载时降级为关键词搜索，功能不受影响。
        if not enabled:
            _log.info("[情绪记忆] 功能已禁用")

    def _ensure_model(self) -> bool:
        """惰性加载向量模型（线程安全）。返回是否可用；失败时降级关键词搜索。"""
        if self._model_available:
            return True
        if not self._enabled:
            return False
        with self._model_lock:
            if self._model_available:
                return True
            try:
                from sentence_transformers import SentenceTransformer
                model_path = os.path.join(self._model_cache_dir, self.MODEL_NAME)
                if os.path.exists(model_path):
                    _log.info(f"[情绪记忆] 首次使用，加载本地模型: {model_path}")
                    self._model = SentenceTransformer(model_path, device='cpu')
                    self._model_available = True
                    _log.info("[情绪记忆] 向量模型加载成功")
                elif self._auto_download:
                    self._download_model(model_path)
                    return self._model_available
                else:
                    _log.warning("[情绪记忆] 模型不存在且未开启自动下载，降级关键词搜索")
            except Exception as e:
                _log.warning(f"[情绪记忆] 向量模型加载失败（降级关键词搜索）: {e}")
            return self._model_available

    def _init_model(self):
        """保留兼容旧接口：显式预加载（等价于 _ensure_model）"""
        self._ensure_model()
    
    def _download_model(self, model_path: str):
        """下载模型，超时60秒"""
        import concurrent.futures
        
        _log.info(f"[情绪记忆] 开始下载模型: {self.MODEL_NAME}")
        _log.info(f"[情绪记忆] 模型将保存到: {model_path}")
        
        os.makedirs(self._model_cache_dir, exist_ok=True)
        
        def download():
            from sentence_transformers import SentenceTransformer
            try:
                _log.info("[情绪记忆] 正在连接模型服务器...")
                model = SentenceTransformer(
                    self.MODEL_NAME, 
                    cache_folder=self._model_cache_dir,
                    device='cpu'
                )
                self._model = model
                self._model_available = True
                _log.info("[情绪记忆] 模型下载完成")
            except Exception as e:
                raise RuntimeError(f"下载失败: {e}")
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(download)
                _log.info("[情绪记忆] 下载中（超时60秒）...")
                result = future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(
                f"[情绪记忆] 模型下载超时（60秒）！\n"
                "请检查网络连接，或手动下载模型:\n"
                f"  python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('{self.MODEL_NAME}', cache_folder='{self._model_cache_dir}')\"\n"
                "离线安装包下载地址: https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L6-v2\n"
                "下载后将文件夹复制到: {self._model_cache_dir}"
            )
    
    def add_memory(self, content: str, emotion_type: str = '', intensity: float = 0.5):
        keywords = self._extract_keywords(content)
        
        memory_id = self._db.add_emotional_memory(
            content=content,
            emotion_type=emotion_type,
            intensity=intensity,
            keywords=','.join(keywords)
        )
        
        # 【启动提速】首次添加记忆时才加载向量模型
        if self._ensure_model():
            self._async_update_vector(memory_id, content)
        
        self._memory_cache[memory_id] = {
            'content': content,
            'emotion_type': emotion_type,
            'intensity': intensity,
            'keywords': keywords
        }
        
        _log.debug(f"[情绪记忆] 添加记忆: {content[:30]}")
    
    def _extract_keywords(self, text: str) -> list[str]:
        if JIEBA_AVAILABLE:
            try:
                words = jieba.lcut(text)
                stop_words = {'的', '了', '和', '是', '就', '都', '而', '及', '与', '着', '或', '一个', '我们', '你们', '他们', '它', '这', '那', '在', '有', '我', '你', '他', '她', '也', '很', '太', '非常', '不', '没有', '可以', '能', '会', '应该', '需要', '已经', '正在', '将', '要'}
                keywords = [w for w in words if len(w) >= 2 and w not in stop_words]
                return keywords[:10]
            except Exception:
                pass
        
        words = text.split()
        return [w for w in words if len(w) >= 2][:10]
    
    def _async_update_vector(self, memory_id: int, content: str):
        def update():
            try:
                if self._model:
                    vector = self._model.encode(content)
                    vector_blob = vector.tobytes()
                    self._db.update_emotional_memory_vector(memory_id, vector_blob)
                    _log.debug(f"[情绪记忆] 向量已更新: {memory_id}")
            except Exception as e:
                _log.error(f"[情绪记忆] 向量更新失败: {e}")
        
        threading.Thread(target=update, daemon=True).start()
    
    def search_memories(self, query: str, threshold: float = 0.7, top_k: int = 3) -> list[dict]:
        # 【启动提速】首次搜索时才加载向量模型
        if self._ensure_model():
            return self._search_by_vector(query, threshold, top_k)
        else:
            return self._search_by_keyword(query, top_k)
    
    def _search_by_vector(self, query: str, threshold: float = 0.7, top_k: int = 3) -> list[dict]:
        try:
            query_vector = self._model.encode(query)
            
            memories = self._db.get_all_emotional_memories(limit=100)
            if not memories:
                return []
            
            scored_memories = []
            for memory in memories:
                if memory['vector_blob']:
                    try:
                        vector = np.frombuffer(memory['vector_blob'], dtype=np.float32)
                        similarity = self._cosine_similarity(query_vector, vector)
                        if similarity >= threshold:
                            scored_memories.append({
                                'memory': memory,
                                'similarity': similarity
                            })
                    except Exception:
                        continue
            
            scored_memories.sort(key=lambda x: x['similarity'], reverse=True)
            
            result = []
            for item in scored_memories[:top_k]:
                memory = item['memory']
                self._db.increment_memory_access(memory['id'])
                result.append({
                    'id': memory['id'],
                    'content': memory['content'],
                    'emotion_type': memory['emotion_type'],
                    'intensity': memory['intensity'],
                    'similarity': item['similarity'],
                    'keywords': memory['keywords'].split(',') if memory['keywords'] else []
                })
            
            return result
        except Exception as e:
            _log.error(f"[情绪记忆] 向量搜索失败: {e}")
            return self._search_by_keyword(query, top_k)
    
    def _search_by_keyword(self, query: str, top_k: int = 3) -> list[dict]:
        keywords = self._extract_keywords(query)
        if not keywords:
            return []
        
        memories = self._db.get_emotional_memories_by_keyword(keywords, limit=top_k)
        
        result = []
        for memory in memories:
            self._db.increment_memory_access(memory['id'])
            result.append({
                'id': memory['id'],
                'content': memory['content'],
                'emotion_type': memory['emotion_type'],
                'intensity': memory['intensity'],
                'similarity': 0.0,
                'keywords': memory['keywords'].split(',') if memory['keywords'] else []
            })
        
        return result
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        if vec1.shape != vec2.shape:
            return 0.0
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(dot_product / (norm1 * norm2))
    
    def generate_trigger_response(self, query: str) -> str:
        triggered_memories = self.search_memories(query)
        
        if not triggered_memories:
            return ""
        
        memory = triggered_memories[0]
        content = memory['content']
        emotion_type = memory['emotion_type']
        
        if emotion_type == 'joy' or memory['intensity'] > 0.5:
            response = f"你让我想起了之前你提到的「{content[:30]}」，那时候感觉你很开心呢～"
        elif emotion_type == 'sadness':
            response = f"突然想到你之前说过「{content[:30]}」，当时你好像不太开心...抱抱你～"
        else:
            response = f"你让我想起了一件事，之前你提到过「{content[:30]}」"
        
        return response
    
    def get_all_memories(self) -> list[dict]:
        return self._db.get_all_emotional_memories(limit=100)
    
    def is_model_available(self) -> bool:
        return self._model_available
    
    def save(self):
        pass
    
    def record(self, period, emotion):
        pass
