#!/usr/bin/env python3
"""
  🐱 喵艺风格模仿系统 - 在线学习用户说话风格并模仿
"""
import json
import logging
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)


class StyleMimicry:
    def __init__(self, llm_system):
        self.llm = llm_system
        # LLM 限流
        self._llm_lock = threading.Lock()
        self._last_llm_call_time = 0.0
        self._llm_min_interval = 1.0
        self.style_fingerprint = {
            'avg_sentence_length': 15,
            'emoji_density': 0.1,
            'formality': 0.5,
            'warmth': 0.6,
            'humor': 0.3,
            'directness': 0.5,
            'use_exclamation': 0.2,
            'use_ellipsis': 0.3,
            'preferred_emojis': ['😊', '😂', '🥰', '🤔'],
            'common_phrases': [],
            'sentence_patterns': [],
            'pacing': 'medium',
        }
        self._sample_size = 0
        self._max_samples = 100
        self._style_history = []
        self._intimacy_level = 0.0
        self._last_update_time = 0
        self._update_interval = 3600
        self._semantic_similarity_threshold = 0.9
    
    def analyze_message(self, text: str):
        self._sample_size = min(self._sample_size + 1, self._max_samples)
        
        length_features = self._analyze_length(text)
        emoji_features = self._analyze_emoji(text)
        tone_features = self._analyze_tone(text)
        pattern_features = self._analyze_patterns(text)
        
        learning_rate = 1.0 / self._sample_size
        
        for key, value in length_features.items():
            if key in self.style_fingerprint and isinstance(value, (int, float)):
                self.style_fingerprint[key] = (
                    self.style_fingerprint[key] * (1 - learning_rate) +
                    value * learning_rate
                )
        
        for key, value in emoji_features.items():
            if key in self.style_fingerprint and isinstance(value, (int, float)):
                self.style_fingerprint[key] = (
                    self.style_fingerprint[key] * (1 - learning_rate) +
                    value * learning_rate
                )
        
        for key, value in tone_features.items():
            if key in self.style_fingerprint and isinstance(value, (int, float)):
                self.style_fingerprint[key] = (
                    self.style_fingerprint[key] * (1 - learning_rate) +
                    value * learning_rate
                )
        
        for phrase in pattern_features.get('common_phrases', []):
            if phrase not in self.style_fingerprint['common_phrases']:
                self.style_fingerprint['common_phrases'].append(phrase)
                if len(self.style_fingerprint['common_phrases']) > 10:
                    self.style_fingerprint['common_phrases'] = self.style_fingerprint['common_phrases'][-10:]
        
        self._record_style_history()
        _log.debug(f"风格指纹更新: {self.style_fingerprint['formality']:.2f}, {self.style_fingerprint['warmth']:.2f}")
    
    def _analyze_length(self, text: str) -> dict[str, float]:
        sentences = re.split(r'[。！？.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if sentences:
            avg_len = sum(len(s) for s in sentences) / len(sentences)
        else:
            avg_len = 0
        
        if avg_len < 10:
            pacing = 'short'
        elif avg_len > 30:
            pacing = 'long'
        else:
            pacing = 'medium'
        
        self.style_fingerprint['pacing'] = pacing
        
        return {
            'avg_sentence_length': avg_len,
            'directness': 1.0 if avg_len < 15 else 0.5
        }
    
    def _analyze_emoji(self, text: str) -> dict[str, float]:
        emoji_pattern = re.compile(r'[\U00010000-\U0010ffff]')
        emojis = emoji_pattern.findall(text)
        
        density = len(emojis) / max(len(text), 1)
        
        if emojis:
            for emoji in emojis[:5]:
                if emoji not in self.style_fingerprint['preferred_emojis']:
                    self.style_fingerprint['preferred_emojis'].append(emoji)
                    if len(self.style_fingerprint['preferred_emojis']) > 10:
                        self.style_fingerprint['preferred_emojis'] = self.style_fingerprint['preferred_emojis'][-10:]
        
        return {
            'emoji_density': density,
            'warmth': min(1.0, density * 2 + 0.5)
        }
    
    def _analyze_tone(self, text: str) -> dict[str, float]:
        formal_indicators = ['请', '您', '麻烦', '不好意思', '感谢', '抱歉']
        informal_indicators = ['哈哈', '嘿嘿', '嗯嗯', '呀', '呢', '哦', '嘛', '哒']
        exclamation_count = text.count('！') + text.count('!')
        ellipsis_count = text.count('...') + text.count('。。。')
        
        formality = sum(1 for indicator in formal_indicators if indicator in text) / max(len(formal_indicators), 1)
        warmth = sum(1 for indicator in informal_indicators if indicator in text) / max(len(informal_indicators), 1)
        
        return {
            'formality': formality,
            'warmth': min(1.0, warmth * 0.5 + 0.5),
            'use_exclamation': min(1.0, exclamation_count / 5),
            'use_ellipsis': min(1.0, ellipsis_count / 5)
        }
    
    def _analyze_patterns(self, text: str) -> dict[str, list]:
        phrases = []
        
        pattern_list = [
            r'(我觉得|我认为|我觉得吧)',
            r'(你知道吗|你懂吧|对吧)',
            r'(其实|说实话|老实说)',
            r'(真的|真的是|真的很)',
            r'(超级|特别|非常)',
            r'(感觉|觉得|好像)',
        ]
        
        for pattern in pattern_list:
            matches = re.findall(pattern, text)
            phrases.extend(matches)
        
        return {'common_phrases': phrases}
    
    def _record_style_history(self):
        record = {
            'timestamp': time.time(),
            'fingerprint': self.style_fingerprint.copy()
        }
        self._style_history.append(record)
        if len(self._style_history) > 50:
            self._style_history = self._style_history[-50:]
    
    def generate_style_prompt(self) -> str:
        fp = self.style_fingerprint
        mimicry_strength = self._get_mimicry_strength()
        
        if mimicry_strength < 0.3:
            return "保持礼貌、友好的通用风格"
        
        style_elements = []
        
        if fp['formality'] < 0.3:
            style_elements.append("非常口语化，像朋友聊天")
        elif fp['formality'] > 0.7:
            style_elements.append("比较正式礼貌")
        else:
            style_elements.append("自然亲切")
        
        if fp['warmth'] > 0.7:
            style_elements.append("温暖热情")
        elif fp['warmth'] < 0.3:
            style_elements.append("冷静理性")
        
        if fp['emoji_density'] > 0.15:
            style_elements.append("多用表情符号")
        elif fp['emoji_density'] < 0.05:
            style_elements.append("少用表情符号")
        
        if fp['pacing'] == 'short':
            style_elements.append("回答简短精炼")
        elif fp['pacing'] == 'long':
            style_elements.append("回答详细丰富")
        
        if fp['common_phrases'] and mimicry_strength > 0.5:
            style_elements.append(f"使用这些口头禅：{', '.join(fp['common_phrases'][:3])}")
        
        if fp['preferred_emojis'] and mimicry_strength > 0.3:
            style_elements.append(f"优先使用这些表情：{''.join(fp['preferred_emojis'][:5])}")
        
        if mimicry_strength < 0.5:
            style_elements.append("保持适度的距离感，不要过于亲密")
        
        return "，".join(style_elements)
    
    def two_phase_generate(self, query: str, context: str = "") -> str:
        if not self.llm:
            return self._simple_adapt(query)
        
        first_response = self._generate_accurate_response(query, context)
        
        if not first_response:
            return query
        
        polished_response = self._polish_response(first_response)
        
        similarity = self._calculate_semantic_similarity(first_response, polished_response)
        
        if similarity >= self._semantic_similarity_threshold:
            _log.debug(f"[风格模仿] 润色成功，相似度: {similarity:.2f}")
            return polished_response
        else:
            _log.warning(f"[风格模仿] 润色后偏差过大({similarity:.2f})，回退到轻度润色")
            return self._light_polish(first_response)
    
    def _rate_limited_llm_call(self, messages: list, model: str = "glm-4-flash",
                                 temperature: float = 0.3, max_tokens: int = 300,
                                 timeout: float = 30, max_retries: int = 2) -> str | None:
        """带限流和重试的 LLM 调用"""
        if not self.llm:
            return None

        with self._llm_lock:
            elapsed = time.time() - self._last_llm_call_time
            if elapsed < self._llm_min_interval:
                wait = self._llm_min_interval - elapsed
                time.sleep(wait)

            for attempt in range(max_retries + 1):
                try:
                    self._last_llm_call_time = time.time()
                    # 【provider 适配层】self.llm 已是 llm_provider 适配器，统一接口 chat() -> str
                    content = self.llm.chat(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                    )
                    return (content or '').strip() or None
                except Exception as e:
                    err_str = str(e).lower()
                    is_retryable = any(x in err_str for x in ['429', 'too many', 'rate limit', '500', '502', '503', '504'])
                    if is_retryable and attempt < max_retries:
                        delay = 2.0 * (2 ** attempt)
                        _log.warning(f"[风格模仿] LLM调用失败(第{attempt + 1}次)，{delay:.1f}s后重试: {e}")
                        time.sleep(delay)
                    else:
                        _log.error(f"[风格模仿] LLM调用失败: {e}")
                        return None
            return None

    def _generate_accurate_response(self, query: str, context: str = "") -> str:
        prompt = f"""
请用标准、正式的语言回答以下问题，确保信息准确完整：

问题: {query}
上下文: {context}

直接给出准确回答：
"""
        result = self._rate_limited_llm_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=300,
        )
        return result or ""
    
    def _polish_response(self, original: str) -> str:
        style_prompt = self.generate_style_prompt()
        
        prompt = f"""
请将以下回答按照指定风格进行语言润色，仅修改措辞、语气、表情符号，**绝对不要改变事实含义**：

原文: {original}

风格要求: {style_prompt}

润色后的回答（保持原意不变）：
"""
        result = self._rate_limited_llm_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=300,
        )
        return result or original
    
    def _light_polish(self, original: str) -> str:
        fp = self.style_fingerprint
        
        response = original
        
        if fp['emoji_density'] > 0.15 and '😊' not in response:
            response += ' 😊'
        
        if fp['pacing'] == 'short' and len(response) > 80:
            response = response[:80] + '...'
        
        if fp['use_exclamation'] > 0.3 and '！' not in response and '!' not in response:
            response = response.replace('。', '！')
        
        return response
    
    def _calculate_semantic_similarity(self, text1: str, text2: str) -> float:
        cleaned1 = re.sub(r'[\W_]+', ' ', text1.lower())
        cleaned2 = re.sub(r'[\W_]+', ' ', text2.lower())
        
        matcher = SequenceMatcher(None, cleaned1, cleaned2)
        return matcher.ratio()
    
    def _simple_adapt(self, response: str) -> str:
        fp = self.style_fingerprint
        
        if fp['emoji_density'] > 0.15 and '😊' not in response:
            response += ' 😊'
        
        if fp['pacing'] == 'short' and len(response) > 50:
            response = response[:50] + '...'
        
        if fp['use_exclamation'] > 0.3 and '！' not in response:
            response = response.replace('。', '！')
        
        return response
    
    def adapt_response(self, response: str) -> str:
        return self._simple_adapt(response)
    
    def get_style_fingerprint(self) -> dict[str, Any]:
        return self.style_fingerprint.copy()
    
    def load_style_fingerprint(self, fingerprint: dict[str, Any]):
        self.style_fingerprint = fingerprint.copy()
    
    def set_intimacy_level(self, level: float):
        self._intimacy_level = max(0.0, min(1.0, level))
    
    def _get_mimicry_strength(self) -> float:
        if self._intimacy_level < 0.2:
            return 0.1
        elif self._intimacy_level < 0.4:
            return 0.3
        elif self._intimacy_level < 0.6:
            return 0.5
        elif self._intimacy_level < 0.8:
            return 0.7
        else:
            return 0.9
    
    def async_update_fingerprint(self):
        now = time.time()
        if now - self._last_update_time < self._update_interval:
            return
        
        self._last_update_time = now
        
        def update():
            try:
                _log.info("[风格模仿] 异步更新风格指纹")
                if self._sample_size > 10 and self.llm:
                    prompt = f"""
分析以下风格指纹数据，生成更准确的风格描述：

风格指纹: {json.dumps(self.style_fingerprint, ensure_ascii=False)}

请输出JSON格式，包含优化后的风格参数：
{{
  "avg_sentence_length": 数字,
  "emoji_density": 0.0-1.0,
  "formality": 0.0-1.0,
  "warmth": 0.0-1.0,
  "humor": 0.0-1.0,
  "directness": 0.0-1.0,
  "use_exclamation": 0.0-1.0,
  "use_ellipsis": 0.0-1.0,
  "pacing": "short/medium/long",
  "style_description": "风格描述"
}}
"""
                    result_text = self._rate_limited_llm_call(
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3, max_tokens=200,
                    )
                    if not result_text:
                        return
                    result = result_text
                    data = json.loads(result)
                    for key in ['avg_sentence_length', 'emoji_density', 'formality', 
                                'warmth', 'humor', 'directness', 'use_exclamation', 
                                'use_ellipsis', 'pacing']:
                        if key in data:
                            self.style_fingerprint[key] = data[key]
                    _log.info("[风格模仿] 风格指纹更新完成")
            except Exception as e:
                _log.error(f"[风格模仿] 异步更新失败: {e}")
        
        import threading
        threading.Thread(target=update, daemon=True).start()
