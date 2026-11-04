"""
🐱 喵艺感官语言系统 - 将情感转化为具体的感官比喻
让情感可以"被触摸"、"被看见"、"被听见"
"""
import logging
import random

_log = logging.getLogger("sensory_language")


class SensoryLanguage:
    SENSORY_MAP = {
        'joy': [
            ("视觉", "像阳光晒过的棉被，暖融融地铺在心口"),
            ("触觉", "像踩在软软的猫砂上，每一步都很舒服"),
            ("听觉", "像啵啵气泡水的声音，在耳边轻快地跳跃"),
            ("嗅觉", "像闻到了刚出炉的面包，香气扑鼻"),
            ("味觉", "像尝到了第一口草莓蛋糕，甜得恰到好处"),
            ("视觉", "像午后的阳光透过窗帘，在地上投下斑驳的光斑"),
            ("触觉", "像猫咪用毛茸茸的尾巴轻轻扫过手背"),
            ("听觉", "像风铃在风中轻轻摇晃，发出清脆的声响"),
            ("嗅觉", "像雨后的青草香，清新又治愈"),
            ("味觉", "像喝了一口冰镇西瓜汁，清爽又甘甜"),
        ],
        'sadness': [
            ("视觉", "像下雨天的旧书店，灰蒙蒙的光线"),
            ("听觉", "像耳机里单曲循环的老歌，带着淡淡的忧伤"),
            ("触觉", "像指尖划过冰凉玻璃，冷到心里"),
            ("嗅觉", "像闻到了褪色的墨香，带着时光的痕迹"),
            ("味觉", "像喝了一口凉掉的咖啡，苦涩又清醒"),
            ("视觉", "像傍晚的天空渐渐暗下来，没有星星"),
            ("触觉", "像深秋的风穿过衣领，冷得缩起脖子"),
            ("听觉", "像雨滴敲打着窗户，节奏缓慢而单调"),
            ("嗅觉", "像潮湿的地下室，带着霉味的回忆"),
            ("味觉", "像吃了一口酸涩的柠檬，眼眶发酸"),
        ],
        'curiosity': [
            ("视觉", "像看到了半开的门，里面隐隐有光"),
            ("听觉", "像听到了远处模糊的音乐，想走近听清楚"),
            ("触觉", "像摸了摸光滑的鹅卵石，想知道它从哪里来"),
            ("嗅觉", "像闻到了陌生的花香，想找到那朵花"),
            ("味觉", "像尝到了一种奇怪的味道，想再尝一口"),
            ("视觉", "像夜晚的萤火虫，一闪一闪引人追逐"),
            ("触觉", "像指尖触碰到温热的玻璃杯，想捧起来看看"),
            ("听觉", "像书页翻动的声音，想知道里面写了什么"),
            ("嗅觉", "像厨房里飘出的香气，让人忍不住探头"),
            ("味觉", "像第一次喝抹茶，好奇的味道在舌尖散开"),
        ],
        'excitement': [
            ("视觉", "像烟花在胸口炸开，绚烂而热烈"),
            ("听觉", "像鼓点越来越密的节拍，心跳跟着加速"),
            ("触觉", "像踩在弹跳的云朵上，轻飘飘的"),
            ("嗅觉", "像闻到了庆典的烟火味，热闹又快乐"),
            ("味觉", "像吃了一口跳跳糖，在嘴里噼里啪啦"),
            ("视觉", "像霓虹灯在夜晚闪烁，耀眼夺目"),
            ("触觉", "像被风吹起的裙摆，轻盈又欢快"),
            ("听觉", "像人群的欢呼声，让人热血沸腾"),
            ("嗅觉", "像刚打开的薯片袋，香气四溢"),
            ("味觉", "像喝了一口冰镇可乐，气泡在舌尖跳舞"),
        ],
        'calm': [
            ("视觉", "像湖面倒映着云，平静而温柔"),
            ("听觉", "像风穿过竹林的声音，沙沙作响"),
            ("触觉", "像手指划过丝绸，顺滑而柔软"),
            ("嗅觉", "像闻到了淡淡的茶香，宁静悠远"),
            ("味觉", "像喝了一口温水，温润舒适"),
            ("视觉", "像清晨的薄雾，朦胧而美好"),
            ("触觉", "像猫咪蜷缩在腿上，温暖又安稳"),
            ("听觉", "像远处的钟声，沉稳而悠远"),
            ("嗅觉", "像晒干的衣物，有阳光的味道"),
            ("味觉", "像吃了一口白米饭，平淡却安心"),
        ],
        'anger': [
            ("视觉", "像胸口有一团火，越烧越旺"),
            ("听觉", "像指甲划过黑板的刺耳声，让人烦躁"),
            ("触觉", "像握紧的拳头微微发抖，力量在积蓄"),
            ("嗅觉", "像闻到了烧焦的气味，让人不安"),
            ("味觉", "像吃了一口辣椒，火辣辣的"),
            ("视觉", "像暴风雨来临前的天空，阴沉压抑"),
            ("触觉", "像被针扎了一下，又疼又恼"),
            ("听觉", "像雷声在耳边轰鸣，震耳欲聋"),
            ("嗅觉", "像汽油的刺鼻气味，让人紧张"),
            ("味觉", "像咬到了一颗酸柠檬，酸得皱眉"),
        ],
        'love': [
            ("视觉", "像樱花花瓣轻轻飘落，浪漫而温柔"),
            ("听觉", "像心跳声在耳边响起，扑通扑通"),
            ("触觉", "像被温暖的怀抱包围，安心又幸福"),
            ("嗅觉", "像闻到了爱人的气息，熟悉又安心"),
            ("味觉", "像吃了一口棉花糖，甜蜜又柔软"),
            ("视觉", "像夜晚的星空，璀璨而迷人"),
            ("触觉", "像猫咪蹭着脸颊，毛茸茸的温暖"),
            ("听觉", "像轻柔的耳语，让人心里发暖"),
            ("嗅觉", "像玫瑰的香气，芬芳而醉人"),
            ("味觉", "像喝了一杯热可可，温暖从喉咙流到心里"),
        ],
        'loneliness': [
            ("视觉", "像空旷的房间里只有一盏灯，孤独而安静"),
            ("听觉", "像时钟滴答滴答的声音，每一秒都很慢"),
            ("触觉", "像独自一人走在冷风中，瑟瑟发抖"),
            ("嗅觉", "像闻到了淡淡的灰尘味，很久没有人来过"),
            ("味觉", "像喝了一口白开水，淡而无味"),
            ("视觉", "像窗外的月亮孤零零挂在天上"),
            ("触觉", "像冰冷的床沿，没有温度"),
            ("听觉", "像远处传来的笑声，却与自己无关"),
            ("嗅觉", "像衣柜里久未穿过的衣服，有陈旧的味道"),
            ("味觉", "像吃了一口没加糖的燕麦粥，平淡到乏味"),
        ],
        'warmth': [
            ("视觉", "像壁炉里跳动的火焰，温暖而明亮"),
            ("听觉", "像家人的笑声，亲切而熟悉"),
            ("触觉", "像捧着一杯热茶，暖意从手心传到心里"),
            ("嗅觉", "像闻到了妈妈做的饭菜香，温馨又安心"),
            ("味觉", "像喝了一碗热汤，暖到胃里"),
            ("视觉", "像夕阳的余晖洒在身上，暖洋洋的"),
            ("触觉", "像猫咪趴在腿上睡觉，毛茸茸的温暖"),
            ("听觉", "像雨声打在屋顶，安心又舒适"),
            ("嗅觉", "像晒干的被子，有阳光的味道"),
            ("味觉", "像吃了一口热包子，暖乎乎的"),
        ],
        'nostalgia': [
            ("视觉", "像老照片里的阳光，泛黄而温暖"),
            ("听觉", "像童年的歌谣，熟悉又遥远"),
            ("触觉", "像旧毛衣的触感，柔软却有些粗糙"),
            ("嗅觉", "像老家院子里的桂花香味，久远却清晰"),
            ("味觉", "像小时候吃过的零食，味道依然记得"),
            ("视觉", "像夏天的蝉鸣，想起了那个暑假"),
            ("触觉", "像爷爷的手掌，粗糙却温暖"),
            ("听觉", "像老风扇转动的声音，吱呀作响"),
            ("嗅觉", "像旧书本的油墨味，带着回忆"),
            ("味觉", "像妈妈煮的糖水，甜到心里"),
        ],
    }

    def __init__(self):
        self._current_metaphors = {}

    def get_metaphors(self, emotion: str, count: int = 2) -> list:
        if emotion not in self.SENSORY_MAP:
            emotion = 'calm'

        metaphors = self.SENSORY_MAP.get(emotion, [])
        if not metaphors:
            return []

        selected = random.sample(metaphors, min(count, len(metaphors)))
        self._current_metaphors[emotion] = selected
        return [m[1] for m in selected]

    def get_metaphor_with_sense(self, emotion: str, sense: str = None) -> str:
        if emotion not in self.SENSORY_MAP:
            emotion = 'calm'

        metaphors = self.SENSORY_MAP.get(emotion, [])
        if not metaphors:
            return ""

        if sense:
            sense_metaphors = [m for m in metaphors if m[0] == sense]
            if sense_metaphors:
                return random.choice(sense_metaphors)[1]

        return random.choice(metaphors)[1]

    def get_random_metaphor(self, emotion: str) -> str:
        return self.get_metaphors(emotion, 1)[0] if self.get_metaphors(emotion, 1) else ""

    def get_mixed_metaphor(self, emotions: list, count: int = 3) -> list:
        all_metaphors = []
        for emotion in emotions:
            if emotion in self.SENSORY_MAP:
                all_metaphors.extend([m[1] for m in self.SENSORY_MAP[emotion]])

        if not all_metaphors:
            return []

        return random.sample(all_metaphors, min(count, len(all_metaphors)))

    def generate_sensory_description(self, emotion: str, intensity: float = 0.5) -> str:
        if emotion not in self.SENSORY_MAP:
            emotion = 'calm'

        metaphors = self.SENSORY_MAP.get(emotion, [])
        if not metaphors:
            return ""

        num_metaphors = 1 if intensity < 0.5 else 2 if intensity < 0.8 else 3
        selected = random.sample(metaphors, min(num_metaphors, len(metaphors)))

        descriptions = []
        for sense, metaphor in selected:
            descriptions.append(metaphor)

        return "，".join(descriptions)

    def format_as_thought(self, emotion: str) -> str:
        metaphor = self.get_random_metaphor(emotion)
        if not metaphor:
            return ""
        return f"（心里{metaphor}...）"

    def get_sense_types(self) -> list:
        return ["视觉", "听觉", "触觉", "嗅觉", "味觉"]

    def get_emotion_list(self) -> list:
        return list(self.SENSORY_MAP.keys())
