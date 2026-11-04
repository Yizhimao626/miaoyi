"""工具管理器 - 为喵艺提供联网能力（搜索增强 + 工具调用）

实现"联网的猫耳朵"和"外挂的猫爪子"：
1. DuckDuckGo 即时搜索 - 完全免费，无需注册
2. DuckDuckGo 新闻搜索 - 获取最新资讯
3. 维基百科 REST API - 权威知识，完全免费
4. 萌娘百科 MediaWiki API - ACGN 文化百科，完全免费
5. 百度百科爬虫 - 中文世界最全知识库，完全免费
6. TMDB 影视查询 - 免费注册获取 API Key（可选）
7. 时间/日期查询 - 直接用 Python datetime
8. 搜索缓存 - 相同问题10分钟内不重复搜索
9. 限流控制 - 每日搜索次数限制
10. UA轮换 - 防止被反爬拦截

设计原则：
- 所有数据源均为免费且无需注册（TMDB除外）
- 并行获取多个数据源，优先使用高质量结果
- 所有外部调用有超时保护，确保聊天不卡顿
- 优雅降级：数据源失败不影响主流程
- 反爬策略：UA轮换、完整浏览器请求头、静默失败
"""
import concurrent.futures
import logging
import random
import re
import threading
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Optional

_log = logging.getLogger("ToolManager")

try:
    from ddgs import DDGS
    DDGS_OK = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_OK = True
    except ImportError:
        DDGS_OK = False

try:
    import httpx
    HTTPX_OK = True
except ImportError:
    HTTPX_OK = False

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False


class SearchCache:
    """搜索结果缓存 - 相同问题10分钟内不重复搜索"""
    
    def __init__(self, ttl_minutes: int = 10, max_size: int = 100):
        self._ttl = ttl_minutes * 60
        self._max_size = max_size
        self._cache = {}
        self._lock = threading.Lock()
    
    def get(self, query: str) -> str | None:
        with self._lock:
            item = self._cache.get(query)
            if item:
                if time.time() - item['ts'] < self._ttl:
                    _log.debug(f"[缓存命中] {query}")
                    return item['result']
                else:
                    del self._cache[query]
            return None
    
    def set(self, query: str, result: str):
        with self._lock:
            self._cache[query] = {
                'ts': time.time(),
                'result': result
            }
            if len(self._cache) > self._max_size:
                oldest = sorted(self._cache.keys(), key=lambda k: self._cache[k]['ts'])[:len(self._cache) // 3]
                for k in oldest:
                    del self._cache[k]
    
    def clear(self):
        with self._lock:
            self._cache.clear()


class RateLimiter:
    """限流控制器 - 每日搜索次数限制"""
    
    def __init__(self, daily_limit: int = 50):
        self._daily_limit = daily_limit
        self._counts = {}
        self._lock = threading.Lock()
        self._last_reset = time.time()
    
    def _reset_if_new_day(self):
        now = time.time()
        if now - self._last_reset > 24 * 3600:
            self._counts.clear()
            self._last_reset = now
    
    def can_search(self, user_id: str = "default") -> bool:
        self._reset_if_new_day()
        with self._lock:
            count = self._counts.get(user_id, 0)
            return count < self._daily_limit
    
    def increment(self, user_id: str = "default"):
        self._reset_if_new_day()
        with self._lock:
            self._counts[user_id] = self._counts.get(user_id, 0) + 1
    
    def get_remaining(self, user_id: str = "default") -> int:
        self._reset_if_new_day()
        with self._lock:
            count = self._counts.get(user_id, 0)
            return max(0, self._daily_limit - count)


class ToolManager:
    """工具管理器 - 提供各种外部工具调用能力"""
    
    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]
    
    def __init__(self):
        self._search_cache = SearchCache(ttl_minutes=10, max_size=100)
        self._rate_limiter = RateLimiter(daily_limit=100)
        self._ddgs = DDGS() if DDGS_OK else None
        
        from utils.config_manager import config_manager
        self._config = config_manager
        self._tmdb_api_key = self._config.get('tmdb.api_key', '')
    
    def _get_random_headers(self) -> dict[str, str]:
        """获取随机浏览器请求头"""
        ua = random.choice(self._USER_AGENTS)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
            "Sec-Ch-Ua": "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\"",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": "\"Windows\"",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
    
    def _is_time_query(self, text: str) -> bool:
        """判断是否是时间查询"""
        time_keywords = ["几点", "时间", "几号", "日期", "星期", "周几"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in time_keywords)
    
    def _is_wikipedia_question(self, text: str) -> bool:
        """判断是否适合用维基百科回答（"是什么"、"介绍"等）"""
        patterns = [
            r'^(什么是|什么叫|什么叫做)\s*',
            r'^(.+)\s*(是什么|叫什么|是什么东西)',
            r'^(介绍|简介|简述)\s*',
            r'^(.+)\s*(的|是)\s*(简介|介绍|定义|概念)',
            r'^(谁|哪个|哪部|哪部作品)\s*',
        ]
        text_lower = text.lower()
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return True
        return False
    
    def _is_meme_query(self, text: str) -> bool:
        """判断是否是网络热梗查询"""
        meme_keywords = ["梗", "空耳", "出处", "出自", "来源", "什么意思"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in meme_keywords)
    
    def _is_acgn_query(self, text: str) -> bool:
        """判断是否是ACGN相关查询（动漫、游戏、角色）"""
        acgn_keywords = [
            "动漫", "动画", "漫画", "游戏", "角色", "人物",
            "番剧", "二次元", "原神", "崩坏", "明日方舟",
            "角色名", "角色介绍", "游戏攻略", "游戏剧情",
            "轻小说", "小说", "漫画",
        ]
        text_lower = text.lower()
        return any(kw in text_lower for kw in acgn_keywords)
    
    def _is_news_query(self, text: str) -> bool:
        """判断是否需要新闻搜索"""
        news_keywords = ["最新", "今天", "实时", "新闻", "近日", "刚刚", "现在", "今天发生"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in news_keywords)
    
    def _is_movie_query(self, text: str) -> bool:
        """判断是否是影视查询"""
        movie_keywords = ["电影", "电视剧", "演员", "扮演", "主演", "导演", "上映", "票房", "评分"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in movie_keywords)
    
    def _classify_search_type(self, text: str) -> str:
        """分类搜索类型"""
        if self._is_time_query(text):
            return "time"
        if self._is_meme_query(text):
            return "meme"
        if self._is_acgn_query(text):
            return "acgn"
        if self._is_news_query(text):
            return "news"
        if self._is_movie_query(text):
            return "movie"
        if self._is_wikipedia_question(text):
            return "knowledge"
        return "general"
    
    def _get_search_timeout(self, search_type: str) -> int:
        """基于搜索类型动态调整超时时间"""
        timeout_map = {
            "time": 3,
            "meme": 5,
            "acgn": 6,
            "news": 5,
            "movie": 7,
            "knowledge": 8,
            "general": 6,
        }
        return timeout_map.get(search_type, 6)
    
    def get_time_response(self, text: str) -> str | None:
        """直接回答时间问题，无需搜索"""
        now = datetime.now()
        text_lower = text.lower()
        
        if '几点' in text_lower or '时间' in text_lower:
            return f"现在是 {now.strftime('%H:%M')} 哦～"
        
        if '今天' in text_lower and ('几号' in text_lower or '日期' in text_lower):
            # 【Bug 修复】同 miaoyi_core.py:548 的 week 映射问题
            weekday = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
            return f"今天是 {now.strftime('%Y年%m月%d日')}，星期{weekday}"
        
        if '星期' in text_lower or '周几' in text_lower:
            # 【Bug 修复】同 miaoyi_core.py:548 的 week 映射问题
            weekday = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
            return f"今天是星期{weekday}"
        
        return None
    
    def _search_web_sync(self, query: str, max_results: int = 3) -> str:
        """同步版本：使用 DuckDuckGo 进行网络搜索"""
        if not DDGS_OK or not self._ddgs:
            return ""
        
        cached = self._search_cache.get(query)
        if cached:
            return cached
        
        if not self._rate_limiter.can_search():
            return ""
        
        self._rate_limiter.increment()
        
        try:
            results = list(self._ddgs.text(query, max_results=max_results, timeout=15))
            
            if not results:
                return ""
            
            snippets = []
            for i, r in enumerate(results, 1):
                title = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                if title and body:
                    snippet = f"【搜索结果{i}】\n标题：{title}\n内容：{body}"
                    if href:
                        snippet += f"\n链接：{href[:80]}"
                    snippets.append(snippet)
            
            result_text = "\n\n".join(snippets)
            self._search_cache.set(query, result_text)
            
            _log.info(f"[DuckDuckGo搜索] 查询: {query}, 结果数: {len(results)}")
            return result_text
            
        except Exception as e:
            _log.error(f"[DuckDuckGo搜索失败] {query}: {e}")
            return ""
    
    def _search_news_sync(self, query: str, max_results: int = 3) -> str:
        """同步版本：使用 DuckDuckGo 新闻搜索"""
        if not DDGS_OK or not self._ddgs:
            return ""
        
        try:
            results = list(self._ddgs.news(query, max_results=max_results, timeout=15))
            
            if not results:
                return ""
            
            snippets = []
            for i, r in enumerate(results, 1):
                title = r.get('title', '')
                body = r.get('body', '')
                date = r.get('date', '')
                if title and body:
                    snippet = f"【新闻{i}】\n标题：{title}\n时间：{date}\n内容：{body}"
                    snippets.append(snippet)
            
            result_text = "\n\n".join(snippets)
            _log.info(f"[DuckDuckGo新闻] 查询: {query}, 结果数: {len(results)}")
            return result_text
            
        except Exception as e:
            _log.error(f"[DuckDuckGo新闻失败] {query}: {e}")
            return ""
    
    def _search_wikipedia(self, query: str) -> str:
        """使用维基百科 REST API 查询（完全免费，无需注册）
        
        API: https://zh.wikipedia.org/api/rest_v1/page/summary/{title}
        """
        if not HTTPX_OK:
            return ""
        
        try:
            url = f"https://zh.wikipedia.org/api/rest_v1/page/summary/{query}"
            headers = self._get_random_headers()
            
            with httpx.Client(timeout=10, headers=headers) as client:
                resp = client.get(url)
                
                if resp.status_code == 200:
                    data = resp.json()
                    title = data.get('title', '')
                    summary = data.get('extract', '')
                    
                    if title and summary:
                        result = f"【维基百科】\n标题：{title}\n摘要：{summary[:500]}"
                        _log.info(f"[维基百科] 查询: {query}, 标题: {title}")
                        return result
        
        except Exception as e:
            _log.debug(f"[维基百科查询失败] {query}: {e}")
        
        return ""
    
    def _search_moegirl(self, query: str) -> str:
        """使用萌娘百科 MediaWiki API 查询（完全免费，无需注册）
        
        API: https://zh.moegirl.org.cn/api.php?action=query&prop=extracts&exintro=true&titles={title}&format=json
        """
        if not HTTPX_OK:
            return ""
        
        try:
            url = "https://zh.moegirl.org.cn/api.php"
            params = {
                "action": "query",
                "prop": "extracts",
                "exintro": "true",
                "explaintext": "true",
                "titles": query,
                "format": "json",
            }
            headers = self._get_random_headers()
            
            with httpx.Client(timeout=10, headers=headers) as client:
                resp = client.get(url, params=params)
                
                if resp.status_code == 200:
                    data = resp.json()
                    pages = data.get('query', {}).get('pages', {})
                    
                    for page_id in pages:
                        page = pages[page_id]
                        title = page.get('title', '')
                        extract = page.get('extract', '')
                        
                        if title and extract:
                            result = f"【萌娘百科】\n标题：{title}\n摘要：{extract[:500]}"
                            _log.info(f"[萌娘百科] 查询: {query}, 标题: {title}")
                            return result
        
        except Exception as e:
            _log.debug(f"[萌娘百科查询失败] {query}: {e}")
        
        return ""
    
    def _search_movie(self, query: str) -> str:
        """使用 TMDB 查询影视信息"""
        if not HTTPX_OK:
            return ""
        
        if not self._tmdb_api_key:
            return ""
        
        try:
            url = "https://api.themoviedb.org/3/search/movie"
            params = {
                "api_key": self._tmdb_api_key,
                "query": query,
                "language": "zh-CN"
            }
            headers = self._get_random_headers()
            
            with httpx.Client(timeout=10, headers=headers) as client:
                resp = client.get(url, params=params)
                data = resp.json()
                
                if data.get("results"):
                    m = data["results"][0]
                    title = m.get("title", "")
                    release_date = m.get("release_date", "")[:4]
                    vote_average = m.get("vote_average", "?")
                    overview = m.get("overview", "暂无简介")
                    
                    result = f"【影视信息】\n《{title}》({release_date})\n评分：{vote_average}/10\n简介：{overview[:200]}"
                    _log.info(f"[TMDB查询] 电影: {title}, 评分: {vote_average}")
                    return result
        
        except Exception as e:
            _log.debug(f"[TMDB查询失败] {query}: {e}")
        
        return ""
    
    def _search_baidu_baike(self, query: str) -> str:
        """使用百度百科爬虫获取词条摘要（完全免费，无需注册）
        
        百度百科是中文世界最全的知识库，尤其适合解释"XX是什么""XX是谁"。
        
        反爬策略：
        - URL编码中文查询词
        - 完整浏览器请求头（UA轮换）
        - 403/安全验证静默处理，返回空字符串
        - 超时保护（8秒）
        - 优先取lemma-summary摘要，降级取para段落
        - 优雅降级：爬取失败时不影响主流程，由其他数据源补充
        """
        if not HTTPX_OK or not BS4_OK:
            return ""
        
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://baike.baidu.com/item/{encoded_query}"
            headers = self._get_random_headers()
            
            with httpx.Client(timeout=8, headers=headers, follow_redirects=True) as client:
                resp = client.get(url)
                
                if resp.status_code != 200:
                    _log.debug(f"[百度百科] 查询: {query}, 状态码: {resp.status_code}")
                    return ""
                
                soup = BeautifulSoup(resp.text, "lxml")
                
                summary = soup.find("div", class_="lemma-summary")
                if summary:
                    text = summary.get_text(strip=True)
                    if text and len(text) > 20:
                        result = f"【百度百科】\n标题：{query}\n摘要：{text[:500]}"
                        _log.info(f"[百度百科] 查询: {query}, 获取摘要成功")
                        return result
                
                para = soup.find("div", class_="para")
                if para:
                    text = para.get_text(strip=True)
                    if text and len(text) > 20:
                        result = f"【百度百科】\n标题：{query}\n摘要：{text[:500]}"
                        _log.info(f"[百度百科] 查询: {query}, 获取正文段落成功")
                        return result
        
        except Exception as e:
            _log.debug(f"[百度百科查询失败] {query}: {e}")
        
        return ""
    
    def _extract_query_title(self, text: str) -> str:
        """从问题中提取查询标题（去除疑问词）"""
        patterns = [
            r'^(什么是|什么叫|什么叫做)\s*(.+)$',
            r'^(.+)\s*(是什么|叫什么|是什么东西)$',
            r'^(介绍|简介|简述)\s*(.+)$',
            r'^(.+)\s*(的|是)\s*(简介|介绍|定义|概念)$',
        ]
        
        for pattern in patterns:
            match = re.match(pattern, text)
            if match:
                for group in match.groups():
                    if group and group not in ['是什么', '叫什么', '是什么东西', '的', '是', '简介', '介绍', '定义', '概念', '什么是', '什么叫', '什么叫做']:
                        return group.strip()
        
        return text.strip()
    
    def get_enhanced_results(self, text: str) -> dict[str, Any]:
        """获取增强搜索结果 - 自动选择最佳数据源（并行获取）
        
        数据源优先级路由：
        1. 时间查询 → 直接返回系统时间
        2. "是什么"类问题 → 优先维基百科
        3. 网络热梗 → 萌娘百科 + 通用搜索
        4. ACGN内容 → 萌娘百科 + 维基百科
        5. 新闻/最新 → DuckDuckGo 新闻搜索
        6. 影视 → TMDB + 通用搜索
        7. 默认 → DuckDuckGo 通用搜索
        
        使用 ThreadPoolExecutor 并行获取所有数据源，总超时 15 秒，
        避免串行调用导致的延迟累积（最坏情况从 35s 降至 15s）。
        
        返回格式：
        {
            'is_time': bool,
            'time_response': str,
            'need_search': bool,
            'total_results': str,  # 所有数据源结果的拼接
            'sources': list,       # 使用的数据源列表
        }
        """
        results = {
            'is_time': False,
            'time_response': None,
            'need_search': False,
            'total_results': "",
            'sources': [],
        }
        
        if self._is_time_query(text):
            time_response = self.get_time_response(text)
            if time_response:
                results['is_time'] = True
                results['time_response'] = time_response
                return results
        
        results['need_search'] = True
        all_results = []
        sources_used = []
        
        query_title = self._extract_query_title(text)
        
        search_type = self._classify_search_type(text)
        timeout = self._get_search_timeout(search_type)
        
        tasks = []
        
        if self._is_wikipedia_question(text):
            tasks.append(('wikipedia', lambda: self._search_wikipedia(query_title)))
            tasks.append(('baidu_baike', lambda: self._search_baidu_baike(query_title)))
        
        if self._is_acgn_query(text):
            tasks.append(('moegirl', lambda: self._search_moegirl(query_title)))
            tasks.append(('baidu_baike', lambda: self._search_baidu_baike(query_title)))
        
        if self._is_meme_query(text):
            tasks.append(('moegirl', lambda: self._search_moegirl(query_title)))
            tasks.append(('baidu_baike', lambda: self._search_baidu_baike(query_title)))
        
        if self._is_news_query(text):
            tasks.append(('news', lambda: self._search_news_sync(text)))
        
        if self._is_movie_query(text):
            tasks.append(('movie', lambda: self._search_movie(query_title)))
            tasks.append(('baidu_baike', lambda: self._search_baidu_baike(query_title)))
        
        tasks.append(('web', lambda: self._search_web_sync(text)))
        
        if not self._is_wikipedia_question(text) and not self._is_acgn_query(text) and not self._is_meme_query(text) and not self._is_movie_query(text):
            tasks.append(('baidu_baike', lambda: self._search_baidu_baike(query_title)))
        
        if not tasks:
            return results
        
        results_list = []
        _log.info(f"[工具调用] 查询类型: {search_type}, 超时: {timeout}秒")
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 5)) as executor:
            future_to_source = {executor.submit(func): source_name for source_name, func in tasks}
            
            try:
                for future in concurrent.futures.as_completed(future_to_source, timeout=timeout):
                    source_name = future_to_source[future]
                    try:
                        result = future.result(timeout=5)
                        if result:
                            results_list.append((source_name, result))
                    except concurrent.futures.TimeoutError:
                        _log.debug(f"[数据源超时] {source_name}")
                    except Exception as e:
                        _log.debug(f"[数据源失败] {source_name}: {e}")
            except concurrent.futures.TimeoutError:
                _log.warning(f"[工具调用] 查询超时: {text[:30]}")
        
        for source_name, result in results_list:
            all_results.append(result)
            sources_used.append(source_name)
        
        total_results = "\n\n".join(all_results)
        total_results = self._filter_sensitive_content(total_results)
        
        results['total_results'] = total_results
        results['sources'] = sources_used
        
        _log.info(f"[工具调用] 查询: {text}, 使用数据源: {sources_used}, 结果长度: {len(results['total_results'])}")
        
        return results
    
    def _filter_sensitive_content(self, text: str) -> str:
        """内容安全过滤器 - 扫描并过滤敏感内容
        
        过滤规则：
        1. 暴力血腥词汇替换为"喵～这个话题太可怕了，换一个吧～"
        2. 色情低俗词汇替换为"喵～这个话题不合适，我们聊点别的吧～"
        3. 政治敏感词汇替换为"喵～这个话题我不太懂，我们聊点别的吧～"
        4. 恶意攻击词汇替换为"喵～不要说这种话啦～"
        
        返回过滤后的文本，如果完全无法过滤则返回空字符串
        """
        if not text:
            return text
        
        violence_keywords = [
            "杀人", "自杀", "砍人", "捅人", "枪杀", "爆炸", "屠杀",
            "血腥", "暴力", "恐怖", "残忍", "酷刑", "凌迟", "斩首",
            "炸弹", "枪击", "袭击", "迫害", "虐待", "折磨",
        ]
        
        porn_keywords = [
            "色情", "色情片", "AV", "黄色", "裸体", "性", "性交",
            "强奸", "乱伦", "卖淫", "嫖娼", "淫荡", "下流",
        ]
        
        political_keywords = [
            "台独", "藏独", "疆独", "港独", "分裂", "反革命",
            "颠覆", "暴动", "叛乱", "政变",
        ]
        
        abuse_keywords = [
            "傻逼", "傻逼", "操你妈", "操", "妈的", "他妈",
            "去死", "滚", "垃圾", "废物", "脑残", "智障",
            "贱人", "婊子", "狗日", "狗娘养", "畜生",
        ]
        
        text_lower = text.lower()
        
        for kw in violence_keywords:
            if kw in text_lower:
                _log.warning(f"[内容安全] 检测到暴力内容: {kw}")
                return "喵～这个话题太可怕了，换一个吧～"
        
        for kw in porn_keywords:
            if kw in text_lower:
                _log.warning(f"[内容安全] 检测到色情内容: {kw}")
                return "喵～这个话题不合适，我们聊点别的吧～"
        
        for kw in political_keywords:
            if kw in text_lower:
                _log.warning(f"[内容安全] 检测到政治敏感内容: {kw}")
                return "喵～这个话题我不太懂，我们聊点别的吧～"
        
        for kw in abuse_keywords:
            if kw in text_lower:
                _log.warning(f"[内容安全] 检测到恶意攻击内容: {kw}")
                text = text.replace(kw, "*" * len(kw))
        
        return text
    
    def get_search_status_text(self, success: bool = True, searching: bool = True) -> str:
        """获取搜索状态文案"""
        if searching:
            return "*竖起耳朵，全网搜索中…喵*"
        else:
            if success:
                return "*搜索完成，正在整理信息…喵*"
            else:
                return "*耷拉耳朵，网络好像有点卡喵…*"
    
    def get_search_stats(self) -> dict[str, int]:
        """获取搜索统计信息"""
        return {
            'daily_remaining': self._rate_limiter.get_remaining(),
            'daily_limit': self._rate_limiter._daily_limit,
            'cache_size': len(self._search_cache._cache),
        }


_tool_manager = None
_tool_lock = threading.Lock()


def get_tool_manager() -> ToolManager:
    """获取工具管理器单例"""
    global _tool_manager
    with _tool_lock:
        if _tool_manager is None:
            _tool_manager = ToolManager()
    return _tool_manager
