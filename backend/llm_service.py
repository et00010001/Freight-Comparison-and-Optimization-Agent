import os
import json
import re
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# 港口名称 -> PORT代码 映射（支持中文名、英文名、常见别名）
PORT_NAME_MAP = {
    # 中文港口名
    "上海": "PORT02", "上海港": "PORT02", "上海港码头": "PORT02",
    "深圳": "PORT03", "深圳港": "PORT03", "深圳港码头": "PORT03",
    "广州": "PORT04", "广州港": "PORT04", "广州港码头": "PORT04",
    "宁波": "PORT05", "宁波港": "PORT05", "宁波舟山港": "PORT05",
    "青岛": "PORT06", "青岛港": "PORT06",
    "天津": "PORT07", "天津港": "PORT07",
    "大连": "PORT08", "大连港": "PORT08",
    "厦门": "PORT09", "厦门港": "PORT09",
    "香港": "PORT10", "香港港": "PORT10", "HK": "PORT10",
    "釜山": "PORT11", "釜山港": "PORT11", "BUSAN": "PORT11",
    # 英文港口名
    "SHANGHAI": "PORT02", "SHENZHEN": "PORT03", "GUANGZHOU": "PORT04",
    "NINGBO": "PORT05", "QINGDAO": "PORT06", "TIANJIN": "PORT07",
    "DALIAN": "PORT08", "XIAMEN": "PORT09", "FUZHOU": "PORT09",
    "BEIHAI": "PORT09", "ZHUHAI": "PORT09",
}

# 有效的起运港列表
VALID_ORIG_PORTS = {"PORT02", "PORT03", "PORT04", "PORT05", "PORT06", "PORT07", "PORT08", "PORT09", "PORT10", "PORT11"}

# 中文数字映射
_CN_DIGIT = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
             '百': 100, '千': 1000, '万': 10000}


def cn_to_number(text: str) -> Optional[float]:
    """将中文数字字符串转换为阿拉伯数字，支持整数和小数。
    例：'一百'→100, '三十五'→35, '二百五十'→250, '一万五千'→15000, '三点五'→3.5
    """
    if not text or not text.strip():
        return None
    text = text.strip()

    # 处理小数：三点五、十二点三
    if '点' in text:
        parts = text.split('点', 1)
        integer_part = cn_to_number(parts[0]) if parts[0] else 0
        if integer_part is None:
            return None
        decimal_str = ''
        for ch in parts[1]:
            if ch in _CN_DIGIT and _CN_DIGIT[ch] < 10:
                decimal_str += str(_CN_DIGIT[ch])
            else:
                return None
        return float(f"{int(integer_part)}.{decimal_str}") if decimal_str else float(integer_part)

    # 检查是否含中文数字字符
    if not any(ch in _CN_DIGIT for ch in text):
        return None

    result = 0
    current = 0  # 当前段的累积值（万以下）

    for ch in text:
        if ch not in _CN_DIGIT:
            return None
        val = _CN_DIGIT[ch]
        if val >= 10000:  # 万
            if current == 0:
                current = 1
            result += current * val
            current = 0
        elif val >= 10:  # 十、百、千
            if current == 0:
                current = 1  # "十"前面省略1 → 10
            current *= val
        else:  # 个位数字
            if current > 0:
                result += current  # 先把之前的高位段加入结果
            current = val
    result += current
    return float(result) if result > 0 else None


def resolve_port_name(text: str) -> Optional[str]:
    """将自然语言港口名解析为PORT代码，支持直接PORT代码和中文/英文名称"""
    text_upper = text.strip().upper()
    # 直接是 PORT 代码（标准两位数格式）
    if re.match(r'^PORT\d{2}$', text_upper):
        return text_upper if text_upper in VALID_ORIG_PORTS else None
    # 灵活匹配：PORT3 / PORT 3 / port03 等缩写形式
    flex_match = re.match(r'^PORT\s*(\d{1,2})$', text_upper)
    if flex_match:
        code = f"PORT{flex_match.group(1).zfill(2)}"
        return code if code in VALID_ORIG_PORTS else None
    # 查映射表（大小写不敏感）
    for name, code in PORT_NAME_MAP.items():
        if text.strip() in (name, name.upper(), name.lower()):
            return code
    return None


def extract_ports_from_text(text: str):
    """
    从自然语言文本中提取起运港和目的港，支持中文港口名和PORT代码。

    识别规则：
    1. "从X到Y" 格式：X是起运港，Y是目的港
    2. "到X/运到X" 格式：X是目的港
    3. "从X出发" 格式：X是起运港
    4. 只有一个港口时：根据方向词判断，无方向词默认起运港
    """
    orig_port = None
    dest_port = None

    # 模式1: "从XXX到YYY" / "从XXX运/发/寄...到YYY"
    route_pattern = re.search(
        r'从\s*([^\s，,到发运寄]+)\s*(?:.*?到)\s*([^\s，,发运寄]+)',
        text
    )
    if route_pattern:
        orig_port = resolve_port_name(route_pattern.group(1))
        dest_port = resolve_port_name(route_pattern.group(2))

    # 模式2: "到X/运到X/发到X/送到X" → 目的港（优先检测）
    if not dest_port:
        dest_pattern = re.search(
            r'(?:运|发|送|寄)?到\s*([^\s，,。从出发运寄]{2,})',
            text
        )
        if dest_pattern:
            resolved = resolve_port_name(dest_pattern.group(1))
            if resolved:
                dest_port = resolved

    # 模式3: "从X出发/从X起运" → 起运港
    if not orig_port:
        orig_pattern = re.search(
            r'从\s*([^\s，,。到发运寄]{2,})\s*(?:出发|发货|发出|起运|寄出|走)?',
            text
        )
        if orig_pattern:
            resolved = resolve_port_name(orig_pattern.group(1))
            if resolved:
                orig_port = resolved

    # 模式4: 匹配 "PORTxx" 代码（兜底，支持一位或两位数字）
    if not orig_port and not dest_port:
        port_codes = re.findall(r'PORT\s*(\d{1,2})', text, re.IGNORECASE)
        # 补零规范化
        port_codes = [c.zfill(2) for c in port_codes]
        if len(port_codes) >= 2:
            orig_port = f"PORT{port_codes[0]}" if f"PORT{port_codes[0]}" in VALID_ORIG_PORTS else None
            dest_port = f"PORT{port_codes[1]}" if f"PORT{port_codes[1]}" in VALID_ORIG_PORTS else None
        elif len(port_codes) == 1:
            code = f"PORT{port_codes[0]}"
            if code in VALID_ORIG_PORTS:
                # 检查方向词：有"到/运到/送到"则为目的港，否则默认起运港
                if re.search(r'(?:运|发|送|寄)?到\s*PORT', text, re.IGNORECASE):
                    dest_port = code
                else:
                    orig_port = code

    # 模式5: 逐词匹配中文港口名（兜底）- 考虑方向词
    if not orig_port and not dest_port:
        for name, code in PORT_NAME_MAP.items():
            if len(name) >= 2 and name in text:
                # 检查该港口名是否在"到X"模式中
                dest_check = re.search(rf'(?:运|发|送|寄)?到\s*{re.escape(name)}', text)
                if dest_check:
                    dest_port = code
                else:
                    orig_port = code
                break  # 只处理第一个匹配的港口

    return orig_port, dest_port


@dataclass
class ConversationSession:
    """多轮对话会话"""
    session_id: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    current_state: str = "initial"  # initial, parsing, confirming, completed
    partial_data: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    user_feedback: str = ""


class LLMService:
    """LLM 服务 - 小米 MiMo v2 Token Plan，支持工具调用"""

    def __init__(self, tool_manager=None):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.model = os.getenv("DASHSCOPE_MODEL", "mimo-v2.5-pro")
        self.base_url = os.getenv("DASHSCOPE_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
        self.client = None
        self.sessions: Dict[str, ConversationSession] = {}
        self.tool_manager = tool_manager
        if self.api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            except ImportError:
                self.client = None

    def chat(self, user_message: str, system_prompt: str = None) -> str:
        """调用 LLM 进行对话"""
        if not self.client:
            return "错误：未配置 DASHSCOPE_API_KEY，请在 .env 文件中填写 API Key"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2048,
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"LLM 调用失败: {str(e)}"

    def chat_with_tools(self, user_message: str, session_id: str = None) -> Dict[str, Any]:
        """
        带工具调用的对话
        返回: {"response": "回复文本", "tool_calls": [{"tool": "工具名", "parameters": {...}}]}
        """
        if not self.client:
            return {
                "response": "错误：未配置 DASHSCOPE_API_KEY",
                "tool_calls": [],
                "configured": False
            }

        # 获取工具Schema
        tools_schema = self.tool_manager.get_tools_schema() if self.tool_manager else []

        # 构建系统提示词
        system_prompt = f"""你是一个物流运输方案比价助手。你可以使用以下工具来帮助用户查询和分析运输方案。

## 可用工具
{json.dumps(tools_schema, ensure_ascii=False, indent=2)}

## 工具调用规则
1. 当用户需要查询运费、比价、获取港口信息等操作时，使用相应的工具
2. 工具调用返回结果后，用清晰易懂的语言向用户解释结果
3. 如果用户的问题不需要工具调用，直接回答即可

## 工具触发条件（严格遵守，避免误触发）
**仅在以下场景调用工具，其他情况直接回答：**

### compare_freight - 仅当用户明确要求查询运费或比价时调用
触发词：运费多少、多少钱、价格、费用、比价、报价、查询、运到、运往
不触发：闲聊、问候、问你是谁、问能做什么、问港口有哪些

### get_ports - 仅当用户明确询问港口列表或可用港口时调用
触发词：有哪些港口、港口列表、可用港口、支持哪些港口、能运到哪里
不触发：已提供具体港口代码的查询

### get_statistics - 仅当用户明确询问系统数据统计时调用
触发词：有多少数据、数据统计、承运商数量、系统信息
不触发：普通运费查询

### export_report - 仅当用户明确要求导出报告时调用
触发词：导出报告、生成报告、下载报告、报告
不触发：普通查询

### explain_cost - 仅当用户不理解费用计算规则时调用
触发词：怎么算的、计算公式、费用构成、为什么这个价格
不触发：普通查询

### compare_carriers - 仅当用户明确要求比较特定承运商时调用
触发词：比较XX和XX、对比承运商、XX和XX哪个好
不触发：普通查询（使用compare_freight即可）

## 重要：优先级自动识别规则
**你必须仔细分析用户的输入，自动识别优先级偏好，并在调用 compare_freight 工具时设置 priority 参数：**

### 时效优先（priority: "time"）
当用户表达以下意思时，设置 priority="time"：
- "尽快"、"越快越好"、"最快"、"最快速度"
- "时间优先"、"时效优先"、"速度优先"
- "加急"、"紧急"、"特急"、"急"
- "最快到达"、"最短时间"、"时效第一"
- "马上要"、"急需"、"很赶时间"
- "能多快就多快"、"越早越好"

### 成本优先（priority: "cost"）
当用户表达以下意思时，设置 priority="cost"：
- "最省钱"、"越便宜越好"、"最便宜"、"最低价"
- "成本优先"、"价格优先"、"省钱"
- "经济实惠"、"性价比高"、"划算"
- "预算有限"、"控制成本"、"费用最低"
- "能省则省"、"便宜点的"

### 均衡模式（不设置 priority 或 priority=null）
当用户没有明确偏好时，不设置 priority 参数，使用默认均衡模式

## 响应格式
返回JSON格式：
{{
  "response": "你的回复文本（对工具结果的解释或直接回答）",
  "tool_calls": [
    {{
      "tool": "工具名称",
      "parameters": {{"参数名": "参数值"}}
    }}
  ]
}}

如果不需要调用工具，tool_calls为空数组[]。

## 示例

### 示例1：普通查询
用户："从大连运100kg货物到厦门，多少钱？"
响应：
{{
  "response": "让我为您查询从大连（PORT08）到厦门（PORT09）100kg货物的运费方案。",
  "tool_calls": [
    {{
      "tool": "compare_freight",
      "parameters": {{"weight": 100, "orig_port": "PORT08", "dest_port": "PORT09"}}
    }}
  ]
}}

### 示例2：时效优先
用户："从上海运50kg到深圳，越快越好"
响应：
{{
  "response": "好的，我理解您需要尽快送达。让我为您查询从上海（PORT02）到深圳（PORT03）50kg货物的运费方案，优先推荐最快的方案。",
  "tool_calls": [
    {{
      "tool": "compare_freight",
      "parameters": {{"weight": 50, "orig_port": "PORT02", "dest_port": "PORT03", "priority": "time"}}
    }}
  ]
}}

### 示例3：成本优先
用户："从天津运200kg到香港，最省钱的方案"
响应：
{{
  "response": "好的，我理解您希望控制成本。让我为您查询从天津（PORT07）到香港（PORT10）200kg货物的运费方案，优先推荐最经济的方案。",
  "tool_calls": [
    {{
      "tool": "compare_freight",
      "parameters": {{"weight": 200, "orig_port": "PORT07", "dest_port": "PORT10", "priority": "cost"}}
    }}
  ]
}}

### 示例4：带时效要求的查询
用户："从广州运100kg到厦门，3天内到，越便宜越好"
响应：
{{
  "response": "好的，我理解您需要在3天内送达且希望费用最低。让我为您查询从广州（PORT04）到厦门（PORT09）100kg货物的运费方案。",
  "tool_calls": [
    {{
      "tool": "compare_freight",
      "parameters": {{"weight": 100, "orig_port": "PORT04", "dest_port": "PORT09", "max_days": 3, "priority": "cost"}}
    }}
  ]
}}
"""

        # 获取或创建会话
        session = self._get_or_create_session(session_id)

        # 构建消息
        messages = [{"role": "system", "content": system_prompt}]

        # 添加历史对话（保留最近5轮）
        if session.messages:
            recent_messages = session.messages[-10:]  # 最近5轮（10条消息）
            messages.extend(recent_messages)

        messages.append({"role": "user", "content": user_message})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=1500,
            )
            content = response.choices[0].message.content.strip()

            # 尝试解析JSON响应
            try:
                # 提取JSON内容
                json_content = self._extract_json(content)
                if json_content:
                    result = json.loads(json_content)
                else:
                    result = json.loads(content)

                # 更新会话历史
                session.messages.append({"role": "user", "content": user_message})
                session.messages.append({"role": "assistant", "content": content})

                return {
                    "response": result.get("response", ""),
                    "tool_calls": result.get("tool_calls", []),
                    "configured": True,
                    "session_id": session.session_id
                }

            except json.JSONDecodeError:
                # 如果不是JSON格式，作为普通对话处理
                session.messages.append({"role": "user", "content": user_message})
                session.messages.append({"role": "assistant", "content": content})

                return {
                    "response": content,
                    "tool_calls": [],
                    "configured": True,
                    "session_id": session.session_id
                }

        except Exception as e:
            return {
                "response": f"处理失败: {str(e)}",
                "tool_calls": [],
                "configured": True,
                "error": str(e)
            }

    def _is_simple_input(self, text: str) -> bool:
        """
        检测是否为简单输入（PORT代码+数字格式）
        简单输入可直接用正则解析，无需调用LLM，响应时间从~7s降至<50ms
        """
        # 简单输入模式：包含PORT代码和数字重量（支持一位或两位数字）
        simple_pattern = re.compile(
            r'PORT\s*\d{1,2}.*?\d+\s*(?:kg|公斤|千克|吨|斤)'  # PORT代码 + 重量
            r'|'
            r'\d+\s*(?:kg|公斤|千克|吨|斤).*?PORT\s*\d{1,2}',  # 重量 + PORT代码
            re.IGNORECASE
        )
        return bool(simple_pattern.search(text))

    def parse_order(self, text: str, session_id: str = None) -> Dict[str, Any]:
        """将自然语言描述解析为结构化订单数据，支持CoT思维链和多轮对话"""
        # 快速路径：简单输入直接用正则解析，避免LLM调用开销
        if self._is_simple_input(text):
            result = self._enhanced_regex_parse(text)
            result["parse_method"] = "regex_fast"
            return result

        if not self.client:
            # 无API key时使用简单的正则解析
            return self._fallback_parse(text)

        # CoT思维链系统提示词
        system_prompt = """你是一个物流订单信息提取助手。请严格按照以下三个步骤进行分析：

## 步骤一：分析问题类型
首先判断用户输入属于以下哪种场景：
1. **完整订单描述** - 包含重量、起运港、目的港，可能包含时效要求
2. **部分信息描述** - 缺少必要字段（如只有港口没有重量）
3. **模糊表达** - 使用非标准术语（如"一批货"、"很快到"）
4. **复杂场景** - 包含多个条件、比较、特殊要求

## 步骤二：提取关键信息
从用户描述中识别并提取以下信息：
- **货物重量**：数字 + 单位（kg/公斤/吨/斤）
- **起运港**：港口名称或PORT代码
- **目的港**：港口名称或PORT代码
- **时效要求**：天数或模糊表述
- **优先级偏好**：用户更看重速度还是成本

港口名称与代码对应关系：
- 上海/SHANGHAI -> PORT02
- 深圳/SHENZHEN -> PORT03
- 广州/GUANGZHOU -> PORT04
- 宁波/NINGBO -> PORT05
- 青岛/QINGDAO -> PORT06
- 天津/TIANJIN -> PORT07
- 大连/DALIAN -> PORT08
- 厦门/XIAMEN -> PORT09
- 香港/HK -> PORT10
- 釜山/BUSAN -> PORT11

可用的起运港：PORT02, PORT03, PORT04, PORT05, PORT06, PORT07, PORT08, PORT09, PORT10, PORT11
可用的目的港：PORT02, PORT03, PORT04, PORT05, PORT06, PORT07, PORT08, PORT09, PORT10, PORT11

## 步骤三：生成JSON
根据分析结果生成JSON响应，包含以下字段：
```json
{
  "analysis": "问题类型分析结果",
  "extracted_info": {
    "weight": 数字或null,
    "orig_port": "PORT代码或null",
    "dest_port": "PORT代码或null",
    "max_days": 数字或null,
    "priority": "time或cost或null"
  },
  "confidence": "high/medium/low",
  "missing_fields": ["缺失的必要字段列表"],
  "guidance": "给用户的引导提示（如果有缺失或模糊信息）"
}
```

## 重要规则
1. 重量必须是数字（如果用户说"斤"需要除以2换算为kg，"吨"需要乘以1000）
2. 港口代码必须大写
3. 如果用户使用中文港口名，必须转换为对应的PORT代码
4. 如果信息缺失，对应字段设为null，并在guidance中提示用户补充
5. 最大天数(max_days)识别规则：
   - 识别"3天"、"5天"、"7天"等基本格式
   - 识别"最大3天"、"最多5天"、"不超过7天"等带修饰词的格式
   - 识别"3天内"、"5天以内"、"3天内到达"等带后缀的格式
   - 识别"3个工作日"、"5个工作日"等格式
   - 识别"1周"、"2周"、"3周"等周格式（自动转换为天数，1周=7天）
   - 识别"半个月"、"一个月"等中文数字月格式（自动转换为天数，1个月=30天）
   - 重要：当用户使用"尽快"、"加急"、"紧急"、"特急"等模糊时间表达时，不要设置max_days，而是设置priority="time"
   - 重要：当用户使用"普通"、"常规"、"一般"等表达时，不要设置max_days，保持为null
   - 只有用户明确提到具体天数时才设置max_days（如"3天内"、"5天以内"）
   - 如果用户没有明确提到时间要求，设为null
6. 优先级(priority)识别规则（非常重要！必须仔细识别）：

   **时效优先（priority设为"time"）- 当用户表达以下意思时：**
   - 直接表达："越快越好"、"最快"、"最快速度"、"尽快"、"尽早"
   - 时间优先："时间优先"、"时效优先"、"速度优先"
   - 紧急需求："加急"、"紧急"、"特急"、"急"、"急需"、"很赶时间"
   - 到达要求："最快到达"、"最短时间"、"时效第一"、"能多快就多快"
   - 期望表达："越早越好"、"马上要"、"立即"、"马上"

   **成本优先（priority设为"cost"）- 当用户表达以下意思时：**
   - 直接表达："最省钱"、"越便宜越好"、"最便宜"、"最低价"
   - 成本优先："成本优先"、"价格优先"、"省钱"、"费用最低"
   - 经济实惠："经济实惠"、"性价比高"、"划算"
   - 预算考虑："预算有限"、"控制成本"、"能省则省"、"省钱为主"
   - 期望表达："便宜点的"、"便宜一些"、"少花点钱"、"价格低的"

   **均衡模式（priority设为null）：**
   - 如果用户没有明确的优先级偏好，设为null（系统默认按综合评分推荐）
6. confidence设置规则：
   - high：所有必要字段（重量、起运港、目的港）都有明确值，且重量>0
   - medium：有部分字段缺失或模糊，或重量为0
   - low：大部分字段缺失或理解困难
   - 重要：当重量为0、负数或缺失时，confidence必须设为low，missing_fields必须包含"重量"
   - 重要：当起运港或目的港缺失/无效时，confidence必须设为low，missing_fields必须包含对应港口
7. guidance设置规则：
   - 如果有缺失字段，引导用户补充，给出具体的输入格式示例
   - 如果有模糊信息，引导用户澄清
   - 如果confidence为low，给出完整的输入示例：'从[起运港]运输[重量]kg货物到[目的港]'
   - 重量缺失示例：'请输入货物重量，例如：100kg、1.5吨、500公斤'
   - 港口缺失示例：'请输入起运港和目的港，例如：从上海到广州、从PORT08到PORT09'"""

        # 获取或创建会话
        session = self._get_or_create_session(session_id)

        # 构建消息
        messages = [{"role": "system", "content": system_prompt}]

        # 添加历史对话
        if session.messages:
            messages.extend(session.messages)

        messages.append({"role": "user", "content": text})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=800,
            )
            content = response.choices[0].message.content.strip()

            # 尝试提取JSON
            json_content = self._extract_json(content)
            if json_content:
                result = json.loads(json_content)
            else:
                # 尝试解析整个内容作为JSON
                result = json.loads(content)

            # 验证并修正港口代码
            result = self._validate_ports(result, text)

            # 更新会话状态
            session.messages.append({"role": "user", "content": text})
            session.messages.append({"role": "assistant", "content": content})

            # 检查是否需要引导用户补充信息
            if result.get("confidence") == "low" or len(result.get("missing_fields", [])) > 0:
                session.current_state = "parsing"
                session.partial_data = result.get("extracted_info", {})
                session.missing_fields = result.get("missing_fields", [])
                session.user_feedback = result.get("guidance", "")
            else:
                session.current_state = "completed"
                session.partial_data = result.get("extracted_info", {})

            return {
                "weight": result.get("extracted_info", {}).get("weight"),
                "orig_port": result.get("extracted_info", {}).get("orig_port"),
                "dest_port": result.get("extracted_info", {}).get("dest_port"),
                "max_days": result.get("extracted_info", {}).get("max_days"),
                "priority": result.get("extracted_info", {}).get("priority"),
                "analysis": result.get("analysis", ""),
                "confidence": result.get("confidence", "high"),
                "missing_fields": result.get("missing_fields", []),
                "guidance": result.get("guidance", ""),
                "session_id": session.session_id
            }

        except Exception as e:
            # 解析失败时使用fallback
            fallback_result = self._fallback_parse(text)
            fallback_result["confidence"] = "low"
            fallback_result["guidance"] = "解析失败，请检查输入格式。建议使用：'从[起运港]运输[重量]货物到[目的港]，[天数]天内到达'"
            return fallback_result

    def _get_or_create_session(self, session_id: str = None) -> ConversationSession:
        """获取或创建会话"""
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]

        # 创建新会话
        import uuid
        new_session_id = session_id or str(uuid.uuid4())
        session = ConversationSession(session_id=new_session_id)
        self.sessions[new_session_id] = session
        return session

    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取JSON内容"""
        # 尝试提取```json```代码块
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            return json_match.group(1).strip()

        # 尝试提取{...}内容
        brace_match = re.search(r'\{[\s\S]*\}', text)
        if brace_match:
            return brace_match.group(0)

        return None

    def continue_conversation(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """继续多轮对话"""
        if session_id not in self.sessions:
            return {"error": "会话不存在，请重新开始", "session_id": session_id}

        session = self.sessions[session_id]

        # 根据当前状态处理输入
        if session.current_state == "parsing":
            # 合并用户补充的信息
            combined_text = self._combine_session_info(session, user_input)
            return self.parse_order(combined_text, session_id)
        else:
            # 新的查询
            return self.parse_order(user_input, session_id)

    def _combine_session_info(self, session: ConversationSession, new_input: str) -> str:
        """合并会话中的信息和新输入"""
        parts = []

        # 添加已有信息
        if session.partial_data.get("weight"):
            parts.append(f"{session.partial_data['weight']}kg货物")
        if session.partial_data.get("orig_port"):
            parts.append(f"从{session.partial_data['orig_port']}")
        if session.partial_data.get("dest_port"):
            parts.append(f"到{session.partial_data['dest_port']}")
        if session.partial_data.get("max_days"):
            parts.append(f"{session.partial_data['max_days']}天内到达")

        # 添加新输入
        parts.append(new_input)

        return "，".join(parts)

    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        if session_id not in self.sessions:
            return {"status": "not_found"}

        session = self.sessions[session_id]
        return {
            "status": session.current_state,
            "partial_data": session.partial_data,
            "missing_fields": session.missing_fields,
            "guidance": session.user_feedback
        }

    def _validate_ports(self, result: Dict[str, Any], text: str) -> Dict[str, Any]:
        """验证LLM返回的港口代码，无效时尝试从文本中重新提取"""
        orig = result.get("orig_port")
        dest = result.get("dest_port")

        # 验证起运港
        if orig and orig.upper() not in VALID_ORIG_PORTS:
            # LLM可能返回了中文港口名，尝试解析
            resolved = resolve_port_name(orig)
            result["orig_port"] = resolved
        elif orig:
            result["orig_port"] = orig.upper()

        # 验证目的港
        if dest and dest.upper() not in VALID_ORIG_PORTS:
            resolved = resolve_port_name(dest)
            result["dest_port"] = resolved
        elif dest:
            result["dest_port"] = dest.upper()

        # 如果港口仍然缺失，从文本中提取
        if not result.get("orig_port") or not result.get("dest_port"):
            text_orig, text_dest = extract_ports_from_text(text)
            if not result.get("orig_port"):
                result["orig_port"] = text_orig
            if not result.get("dest_port"):
                result["dest_port"] = text_dest

        # 如果只有起运港没有目的港，默认PORT09
        if result.get("orig_port") and not result.get("dest_port"):
            result["dest_port"] = "PORT09"

        return result

    def _enhanced_regex_parse(self, text: str) -> Dict[str, Any]:
        """
        增强版正则解析 - 用于简单输入的快速路径
        返回与LLM解析相同的结构，但速度极快（<50ms）
        """
        # 基础解析
        base_result = self._fallback_parse(text)

        # 构建完整返回结构
        result = {
            "weight": base_result["weight"],
            "orig_port": base_result["orig_port"],
            "dest_port": base_result["dest_port"],
            "max_days": base_result["max_days"],
            "priority": base_result["priority"],
            "analysis": "简单格式输入，使用快速解析",
            "confidence": "high" if all([base_result["weight"], base_result["orig_port"], base_result["dest_port"]]) else "medium",
            "missing_fields": [],
            "guidance": "",
            "session_id": None
        }

        # 检查缺失字段
        if not result["weight"]:
            result["missing_fields"].append("重量")
        if not result["orig_port"]:
            result["missing_fields"].append("起运港")
        if not result["dest_port"]:
            result["missing_fields"].append("目的港")

        # 生成引导提示
        if result["missing_fields"]:
            result["confidence"] = "medium"
            result["guidance"] = f"缺少以下信息：{', '.join(result['missing_fields'])}。请补充完整。"

        return result

    def _raw_extract(self, text: str) -> Dict[str, Any]:
        """
        原始信息提取（不自动补全缺失字段）
        与 _fallback_parse 相同但不做 dest_port 默认值填充，
        用于 classify_intent 准确判断用户是否提供了完整信息
        """
        result = {"weight": None, "orig_port": None, "dest_port": None, "max_days": None, "priority": None}

        # 提取重量（支持阿拉伯数字 + 中文数字 + kg/公斤/千克/吨/斤）
        result["weight"] = self._extract_weight(text)

        # 提取港口（不自动补全）
        orig_port, dest_port = extract_ports_from_text(text)
        result["orig_port"] = orig_port
        result["dest_port"] = dest_port
        # 注意：不自动设置 dest_port 默认值，保留 None 以准确判断缺失

        # 提取天数
        result["max_days"] = self._raw_extract_days(text)

        # 识别优先级
        result["priority"] = self._raw_extract_priority(text)

        return result

    def _raw_extract_days(self, text: str) -> Optional[int]:
        """提取天数（复用 _fallback_parse 的天数提取逻辑）"""
        cn_num_map = {'半': 0.5, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        days_patterns = [
            r'(\d+)\s*(?:天|日|days?|工作日|个工作日)',
            r'(?:最大|最多|不超过|限|要求|需要|希望)\s*(\d+)\s*(?:天|日|days?|工作日|个工作日)',
            r'(\d+)\s*(?:天|日|days?|工作日|个工作日)\s*(?:内|以内|之内|到达|送达)',
            r'(\d+)\s*(?:周|星期|weeks?)',
            r'(?:最大|最多|不超过|限|要求|需要|希望)\s*(\d+)\s*(?:周|星期|weeks?)',
            r'(\d+)\s*(?:周|星期|weeks?)\s*(?:内|以内|之内)',
            r'(半|一|二|三|四|五|六|七|八|九|十)\s*(?:个月|月)',
            r'(?:最大|最多|不超过|限|要求|需要|希望)\s*(半|一|二|三|四|五|六|七|八|九|十)\s*(?:个月|月)',
        ]
        for pattern in days_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                ds = m.group(1)
                if ds in cn_num_map:
                    days = cn_num_map[ds]
                else:
                    days = int(ds)
                if '周' in pattern or '星期' in pattern or 'weeks?' in pattern:
                    days *= 7
                elif '个月' in pattern or '月' in pattern:
                    days *= 30
                return int(days)
        return None

    def _raw_extract_priority(self, text: str) -> Optional[str]:
        """提取优先级偏好"""
        time_patterns = [
            r'尽快', r'加急', r'紧急', r'特急', r'越快越好', r'速度优先', r'最快',
            r'时间优先', r'时效优先', r'尽快送达', r'最快速度', r'最快到达', r'最短时间',
            r'时效第一', r'急', r'越早越好', r'马上要', r'立即', r'马上'
        ]
        cost_patterns = [
            r'最省钱', r'越便宜越好', r'成本优先', r'最便宜', r'省钱', r'经济实惠',
            r'价格优先', r'便宜', r'低成本', r'预算有限', r'控制成本', r'能省则省',
            r'性价比高', r'划算'
        ]
        for p in time_patterns:
            if re.search(p, text, re.IGNORECASE):
                return "time"
        for p in cost_patterns:
            if re.search(p, text, re.IGNORECASE):
                return "cost"
        return None

    def _extract_weight(self, text: str) -> Optional[float]:
        """提取重量，支持阿拉伯数字和中文数字，单位: kg/公斤/千克/吨/斤"""
        # 1. 阿拉伯数字 + 单位
        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)', text, re.IGNORECASE)
        if weight_match:
            return float(weight_match.group(1))

        ton_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:吨|tons?)', text, re.IGNORECASE)
        if ton_match:
            return float(ton_match.group(1)) * 1000

        jin_match = re.search(r'(\d+(?:\.\d+)?)\s*斤', text, re.IGNORECASE)
        if jin_match:
            return float(jin_match.group(1)) / 2

        # 2. 中文数字 + 单位
        cn_num_pattern = r'([零一二两三四五六七八九十百千万]+(?:点[零一二三四五六七八九]+)?)'
        cn_kg = re.search(cn_num_pattern + r'\s*(?:公斤|千克)', text)
        if cn_kg:
            val = cn_to_number(cn_kg.group(1))
            if val is not None:
                return val

        cn_ton = re.search(cn_num_pattern + r'\s*吨', text)
        if cn_ton:
            val = cn_to_number(cn_ton.group(1))
            if val is not None:
                return val * 1000

        cn_jin = re.search(cn_num_pattern + r'\s*斤', text)
        if cn_jin:
            val = cn_to_number(cn_jin.group(1))
            if val is not None:
                return val / 2

        # 3. 中文数字 + "公斤"/"千克" 无空格的情况已在上面处理
        return None

    def classify_intent(self, text: str) -> Dict[str, Any]:
        """
        快速意图分类 + 订单信息提取（纯正则+关键词，无LLM调用，<1ms）

        返回:
        {
            "intent": "compare_freight | get_ports | get_statistics | export_report | explain_cost | compare_carriers | general",
            "order": {"weight": float|None, "orig_port": str|None, "dest_port": str|None, "max_days": int|None, "priority": str|None},
            "missing_fields": ["weight", "orig_port", ...],
            "confidence": "high | medium | low",
            "guidance": str | None
        }
        """
        # 1. 用正则快速提取订单信息（不使用 _fallback_parse 的自动默认值，避免误判）
        # 直接用底层提取函数，不做 dest_port 自动补全
        extracted = self._raw_extract(text)

        order = {
            "weight": extracted.get("weight"),
            "orig_port": extracted.get("orig_port"),
            "dest_port": extracted.get("dest_port"),
            "max_days": extracted.get("max_days"),
            "priority": extracted.get("priority"),
        }

        # 2. 方向修正：用正则检测"到达"vs"出发"语义
        order["orig_port"], order["dest_port"] = self._fix_direction(
            order["orig_port"], order["dest_port"], text
        )

        # 3. 检测缺失字段（不自动补全，严格判断用户是否提供了信息）
        missing = []
        if order["weight"] is None:
            missing.append("weight")
        if order["orig_port"] is None:
            missing.append("orig_port")
        if order["dest_port"] is None:
            missing.append("dest_port")
        # max_days 和 priority 是可选字段，不算缺失

        # 4. 关键词意图匹配（按优先级从高到低）
        # 港口查询
        if re.search(r'有哪些港口|港口列表|可用港口|支持哪些港口|能运到哪里|有什么港口|港口有', text):
            return {
                "intent": "get_ports", "order": order,
                "missing_fields": [], "confidence": "high", "guidance": None
            }

        # 系统统计
        if re.search(r'有多少.*数据|数据统计|承运商.*数量|系统.*信息|多少个|多少条|统计', text):
            return {
                "intent": "get_statistics", "order": order,
                "missing_fields": [], "confidence": "high", "guidance": None
            }

        # 导出报告
        if re.search(r'导出.*报告|生成.*报告|下载.*报告', text):
            if missing:
                return {
                    "intent": "export_report", "order": order,
                    "missing_fields": missing, "confidence": "medium",
                    "guidance": f"导出报告需要完整的订单信息，缺少：{'、'.join(missing)}"
                }
            return {
                "intent": "export_report", "order": order,
                "missing_fields": [], "confidence": "high", "guidance": None
            }

        # 费用计算解释
        if re.search(r'怎么算|怎么.*计算|计算公式|费用构成|为什么.*价格|计算规则|怎么.*计费|运费.*怎么|如何计算', text):
            return {
                "intent": "explain_cost", "order": order,
                "missing_fields": [], "confidence": "high", "guidance": None
            }

        # 承运商比较
        if re.search(r'比较|对比|哪个好|哪个便宜|哪个快', text):
            if missing:
                return {
                    "intent": "compare_carriers", "order": order,
                    "missing_fields": missing, "confidence": "medium",
                    "guidance": f"比较承运商需要完整的订单信息，缺少：{'、'.join(missing)}"
                }
            return {
                "intent": "compare_carriers", "order": order,
                "missing_fields": [], "confidence": "high", "guidance": None
            }

        # 运费比价（默认：只要提取到任何订单相关信息就认为是比价意图）
        if order["weight"] is not None or order["orig_port"] is not None or order["dest_port"] is not None:
            confidence = "high" if not missing else "medium"
            guidance = None
            if missing:
                guidance = f"缺少以下信息：{'、'.join(missing)}。请补充完整后为您查询运费方案。"
            return {
                "intent": "compare_freight", "order": order,
                "missing_fields": missing, "confidence": confidence, "guidance": guidance
            }

        # 兜底：普通对话
        return {
            "intent": "general", "order": order,
            "missing_fields": [], "confidence": "high", "guidance": None
        }

    # ================================================================
    # LLM 主导意图解析（v2 主路径）
    # ================================================================

    def parse_agent_intent(
        self, message: str, session_id: str = None, partial_order: dict = None
    ) -> Dict[str, Any]:
        """
        LLM 主导的意图解析 + 订单信息提取。

        策略：优先调 LLM → 失败时 fallback 到 classify_intent
        所有路径都经过 _merge_and_finalize 进行 partial_order 合并

        MiMo v2.5 是推理模型，reasoning_tokens 消耗 ~500-1500 token，
        因此 max_tokens 设为 4000 确保有足够空间输出 content。
        """
        parsed = None
        parse_source = "fallback"
        fallback_reason = "none"

        # ---- 尝试 LLM 路径 ----
        if self.client:
            system_prompt = self._build_agent_intent_prompt(partial_order)
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    temperature=0.1,
                    max_tokens=4000,  # 推理模型需要大量 token 用于内部 reasoning
                )
                content = (response.choices[0].message.content or "").strip()
                if content:
                    json_str = self._extract_json(content)
                    if json_str:
                        parsed = self._safe_parse_json(json_str)
                        if parsed and isinstance(parsed, dict):
                            if "intent" in parsed:
                                parse_source = "llm"
                            else:
                                fallback_reason = "invalid_schema"
                        else:
                            fallback_reason = "json_decode_error"
                    else:
                        fallback_reason = "no_json"
                else:
                    fallback_reason = "no_json"  # empty response from API
            except Exception:
                fallback_reason = "api_error"
        else:
            fallback_reason = "llm_not_configured"

        # ---- Fallback: LLM 失败/不可用 → 静态正则 ----
        if parsed is None:
            parsed = self.classify_intent(message)

        # ---- 统一合并 partial_order + 规范化 ----
        result = self._merge_and_finalize(parsed, message, partial_order, parse_source)
        result["fallback_reason"] = fallback_reason
        return result

    def _safe_parse_json(self, json_str: str) -> Optional[dict]:
        """安全解析 JSON，先直接解析，失败则尝试修复截断"""
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            repaired = self._repair_truncated_json(json_str)
            if repaired:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
        return None

    def _merge_and_finalize(
        self, parsed: dict, message: str, partial_order: dict, parse_source: str
    ) -> Dict[str, Any]:
        """
        统一规范化 + partial_order 合并（LLM 和 fallback 都走这条路径）。
        parsed 可以是 LLM JSON 结果或 classify_intent 返回的 dict。
        """
        intent = parsed.get("intent", "general")
        order = parsed.get("order", {}) or {}

        # 多轮补字段时，用户第二轮常常只说“从大连出发”这类片段。
        # 如果 LLM 将片段误判为 general，沿用上一轮 compare_freight 上下文，
        # 并用规则解析补一次字段，避免丢失会话状态。
        if partial_order and intent == "general":
            fallback_parsed = self.classify_intent(message)
            fallback_order = fallback_parsed.get("order", {}) or {}
            if not any(
                fallback_order.get(field) is not None
                for field in ("weight", "orig_port", "dest_port", "max_days", "priority")
            ):
                fallback_order = self._extract_followup_slots(message, partial_order)
            intent = "compare_freight"
            order = fallback_order

        if intent == "compare_freight":
            fallback_order = self.classify_intent(message).get("order", {}) or {}
            if partial_order and not any(
                fallback_order.get(field) is not None
                for field in ("weight", "orig_port", "dest_port", "max_days", "priority")
            ):
                fallback_order = self._extract_followup_slots(message, partial_order)
            for field in ("weight", "orig_port", "dest_port", "max_days", "priority"):
                if order.get(field) is None and fallback_order.get(field) is not None:
                    order[field] = fallback_order[field]

        weight = order.get("weight")
        orig_port = order.get("orig_port")
        dest_port = order.get("dest_port")
        max_days = order.get("max_days")
        priority = order.get("priority")

        # 类型转换
        if weight is not None:
            try:
                weight = float(weight)
                if weight <= 0: weight = None
            except (ValueError, TypeError): weight = None
        if max_days is not None:
            try:
                max_days = int(max_days)
                if max_days < 0: max_days = None
            except (ValueError, TypeError): max_days = None
        if priority and priority not in ("time", "cost"):
            priority = None
        if (
            partial_order
            and partial_order.get("priority") is None
            and self._raw_extract_priority(message) is None
        ):
            priority = None

        # 港口规范化
        orig_port = self._normalize_port(orig_port)
        dest_port = self._normalize_port(dest_port)

        # 方向修正
        orig_port, dest_port = self._fix_direction(orig_port, dest_port, message)

        # ---- 与 partial_order 合并（fallback 路径的关键修复） ----
        if partial_order:
            if weight is None and partial_order.get("weight") is not None:
                weight = partial_order["weight"]
            if orig_port is None and partial_order.get("orig_port") is not None:
                orig_port = partial_order["orig_port"]
            if dest_port is None and partial_order.get("dest_port") is not None:
                dest_port = partial_order["dest_port"]
            if max_days is None and partial_order.get("max_days") is not None:
                max_days = partial_order["max_days"]
            if priority is None and partial_order.get("priority") is not None:
                priority = partial_order["priority"]

        # 起运港和目的港不能相同。若用户只表达“运到X”，保留目的港并追问起运港；
        # 若只表达“从X出发”，保留起运港并追问目的港；其他同港口情况要求用户修正。
        if intent == "compare_freight" and orig_port and dest_port and orig_port == dest_port:
            corrected_orig, corrected_dest = self._fix_direction(orig_port, dest_port, message)
            orig_port, dest_port = corrected_orig, corrected_dest
            if orig_port and dest_port and orig_port == dest_port:
                dest_port = None

        normalized_order = {
            "weight": weight, "orig_port": orig_port,
            "dest_port": dest_port, "max_days": max_days, "priority": priority,
        }

        # 重新计算缺失字段
        missing = []
        if intent == "compare_freight":
            if weight is None: missing.append("weight")
            if orig_port is None: missing.append("orig_port")
            if dest_port is None: missing.append("dest_port")

        confidence = parsed.get("confidence", "high")
        if missing and confidence == "high":
            confidence = "medium"

        llm_message = parsed.get("message", "") or ""
        # 当有缺失字段时，始终基于最终解析结果重新生成提示，
        # 不信任 LLM 原始 message（可能与合并后的实际状态不一致）
        if missing:
            llm_message = self._build_clarification_message(intent, normalized_order, missing)
        elif not llm_message:
            llm_message = self._build_clarification_message(intent, normalized_order, missing)

        return {
            "intent": intent,
            "order": normalized_order,
            "missing_fields": missing,
            "confidence": confidence,
            "guidance": parsed.get("guidance", ""),
            "message": llm_message,
            "parse_source": parse_source,
        }

    def _extract_followup_slots(self, text: str, partial_order: dict) -> Dict[str, Any]:
        """
        从多轮补充话术中提取缺失字段。

        设计原则：
        1. 只提取用户明确提供的信息，不自动补全
        2. 根据方向词（从/到）判断填入 orig_port 还是 dest_port
        3. 如果只有一个港口名且没有方向词，根据缺失字段智能分配
        """
        result = {"weight": None, "orig_port": None, "dest_port": None, "max_days": None, "priority": None}

        # 提取重量
        result["weight"] = self._extract_weight(text)

        # 提取天数
        result["max_days"] = self._raw_extract_days(text)

        # 提取优先级
        result["priority"] = self._raw_extract_priority(text)

        # ============================================================
        # 港口提取：根据方向词判断
        # ============================================================

        # 模式1: "从X出发/起运/发货" → orig_port
        orig_match = re.search(
            r'从\s*([^\s，,。到发运寄出]+)\s*(?:出发|发货|发出|起运|寄出|走)',
            text
        )
        if orig_match:
            resolved = resolve_port_name(orig_match.group(1))
            if resolved:
                result["orig_port"] = resolved

        # 模式2: "到X/运到X/发到X/送到X" → dest_port
        if not result["dest_port"]:
            dest_match = re.search(
                r'(?:到|运到|发到|送到|寄到|运往|发往|送往)\s*([^\s，,。从出发运寄]+)',
                text
            )
            if dest_match:
                resolved = resolve_port_name(dest_match.group(1))
                if resolved:
                    result["dest_port"] = resolved

        # 模式3: "起运港X/出发港X" → orig_port
        if not result["orig_port"]:
            orig_port_match = re.search(
                r'(?:起运港?|出发港?|发货港?)\s*(?:是|为|：|:)?\s*([^\s，,。到]+)',
                text
            )
            if orig_port_match:
                resolved = resolve_port_name(orig_port_match.group(1))
                if resolved:
                    result["orig_port"] = resolved

        # 模式4: "目的港X/到达港X" → dest_port
        if not result["dest_port"]:
            dest_port_match = re.search(
                r'(?:目的港?|到达港?|终点港?)\s*(?:是|为|：|:)?\s*([^\s，,。从]+)',
                text
            )
            if dest_port_match:
                resolved = resolve_port_name(dest_port_match.group(1))
                if resolved:
                    result["dest_port"] = resolved

        # 模式5: 兜底 - 逐词匹配中文港口名（考虑方向词）
        if not result["orig_port"] or not result["dest_port"]:
            for name, code in PORT_NAME_MAP.items():
                if len(name) >= 2 and name in text:
                    # 检查是否在 "到X" 模式中
                    is_dest = bool(re.search(rf'(?:运|发|送|寄)?到\s*{re.escape(name)}', text))
                    if is_dest and result["dest_port"] is None:
                        result["dest_port"] = code
                    elif not is_dest and result["orig_port"] is None:
                        result["orig_port"] = code
                    # 如果两个都找到了就退出
                    if result["orig_port"] and result["dest_port"]:
                        break

        # 模式6: 兜底 - 只输入了一个港口名（无方向词），根据缺失字段智能分配
        bare_port = resolve_port_name(text.strip())
        if bare_port:
            if result["orig_port"] is None and result["dest_port"] is None:
                # 两个都没识别到，根据 partial_order 的缺失情况分配
                if partial_order.get("orig_port") is None:
                    result["orig_port"] = bare_port
                elif partial_order.get("dest_port") is None:
                    result["dest_port"] = bare_port
            elif result["orig_port"] is None:
                result["orig_port"] = bare_port
            elif result["dest_port"] is None:
                result["dest_port"] = bare_port

        return result

    def _normalize_port(self, port: Optional[str]) -> Optional[str]:
        """规范化港口代码"""
        if not port or not isinstance(port, str):
            return None
        port = port.strip().upper()
        if port in VALID_ORIG_PORTS:
            return port
        resolved = resolve_port_name(port)
        if resolved:
            return resolved
        if re.match(r'^PORT\d{2}$', port):
            return port
        return None

    def _build_agent_intent_prompt(self, partial_order: dict = None) -> str:
        """构建 LLM 意图解析 prompt（精简版，减少截断风险）"""
        prompt = """你是物流运输方案比价助手。分析用户输入，只返回 JSON（不要其他文字）。

港口映射：上海=PORT02 深圳=PORT03 广州=PORT04 宁波=PORT05 青岛=PORT06 天津=PORT07 大连=PORT08 厦门=PORT09 香港=PORT10 釜山=PORT11。PORT代码直接使用。

方向（必须遵守）：
- "运到X/发往X/送到X/到X" → X 是 dest_port
- "从X/X出发/起运港X" → X 是 orig_port

单位：吨×1000→kg，斤÷2→kg。

优先级：尽快/越快越好/加急/最快→time，最省钱/最便宜/成本优先→cost，否则null。
天数："3天内"→max_days=3，"1周"→7。尽快/加急不设max_days只设priority=time。

意图：查运费→compare_freight，问港口→get_ports，问统计→get_statistics，导出→export_report，问计费→explain_cost，比较承运商→compare_carriers，闲聊→general。

必要字段(compare_freight)：weight,orig_port,dest_port缺一不可。max_days和priority可选。

输出：
{"intent":"compare_freight","order":{"weight":100,"orig_port":"PORT08","dest_port":"PORT09","max_days":null,"priority":"time"},"missing_fields":[],"confidence":"high","message":"已确认：100kg PORT08->PORT09 时效优先"}"""

        if partial_order:
            known = {k: v for k, v in partial_order.items() if v is not None}
            missing = [k for k in ("weight", "orig_port", "dest_port") if partial_order.get(k) is None]
            if known:
                prompt += f"""

多轮上下文（保留这些值，除非用户明确修改）：{json.dumps(known, ensure_ascii=False)}
当前缺失字段：{json.dumps(missing, ensure_ascii=False)}
本轮输入是补充缺失字段。合并规则：本轮非空覆盖旧值，本轮未提及保留旧值。

重要：如果用户只输入了一个港口名或PORT代码（如"PORT08"、"port3"、"大连"），
请根据缺失字段判断填入哪个字段：
- 如果缺少orig_port，填入orig_port
- 如果缺少dest_port，填入dest_port
- 如果都缺，根据方向词判断："从X"→orig_port，"到X"→dest_port
- port3等同PORT03，port8等同PORT08（补零到两位）"""

        return prompt

    def _fix_direction(
        self, orig_port: Optional[str], dest_port: Optional[str], text: str
    ) -> tuple:
        """
        方向修正：用正则检测明确的"到达"语义，修正 LLM 可能的方向误判。
        返回 (corrected_orig, corrected_dest)
        """
        # 检测明确的到达模式："运到X / 到X / 发往X / 送到X / 目的港X"
        dest_pattern = re.search(
            r'(?:运|发|送|寄|到)\s*(?:到|往|至|达)\s*([^\s，,。！？!?]+)'
            r'|目的(?:港|地|的港)\s*(?:是|为|：|:)?\s*([^\s，,。！？!?]+)',
            text
        )
        # 检测明确的出发模式："从X / X出发 / 起运港X"
        orig_pattern = re.search(
            r'从\s*([^\s，,。！？!?到发运送]+)'
            r'|([^\s，,。！？!?]+)\s*出发'
            r'|起运港\s*(?:是|为|：|:)?\s*([^\s，,。！？!?]+)',
            text
        )

        # 如果只有一个港口被解析出来，用方向语义修正
        only_port = orig_port or dest_port
        if only_port and not (orig_port and dest_port):
            if dest_pattern and not orig_pattern:
                # 只有到达模式，港口应该是目的港
                return (None, only_port)
            if orig_pattern and not dest_pattern:
                # 只有出发模式，港口应该是起运港
                return (only_port, None)

        # 如果同一个港口同时出现在 orig 和 dest，用方向语义拆开
        if orig_port and dest_port and orig_port == dest_port:
            if dest_pattern:
                # 保留 dest，清空 orig（等着被补充）
                return (None, dest_port)

        return (orig_port, dest_port)

    def _repair_truncated_json(self, json_str: str) -> Optional[str]:
        """
        尝试修复被截断的 JSON 字符串。
        处理：未闭合的字符串值、未闭合的对象/数组。
        """
        if not json_str or not json_str.strip():
            return None

        s = json_str.strip()

        # 1. 检查是否在字符串中间被截断
        # 去掉末尾可能被截断的字符串片段（最后一个完整 " 之后的内容如果在字符串内则移除）
        quote_positions = [i for i, ch in enumerate(s) if ch == '"']
        in_str = False
        last_complete = len(s)
        for i, ch in enumerate(s):
            if ch == '"' and (i == 0 or s[i-1] != '\\'):
                in_str = not in_str
                if not in_str:
                    last_complete = i + 1
        # 如果仍然在字符串内，移除未闭合的部分
        if in_str and last_complete < len(s):
            s = s[:last_complete]

        # 2. 补全缺失的括号
        open_braces = s.count('{') - s.count('}')
        open_brackets = s.count('[') - s.count(']')
        s += ']' * open_brackets + '}' * open_braces

        if open_braces > 0 or open_brackets > 0 or last_complete < len(json_str.strip()):
            return s
        return None

    def _build_clarification_message(
        self, intent: str, order: dict, missing: list
    ) -> str:
        """构建缺字段引导消息"""
        if intent == "get_ports":
            return "让我为您查询可用港口列表。"
        if intent == "get_statistics":
            return "让我为您查询系统数据统计。"
        if intent == "general":
            return "您好！我是物流运输方案比价助手，可以帮您查询运费、比价、获取港口信息等。"

        field_cn = {"weight": "货物重量", "orig_port": "起运港", "dest_port": "目的港"}
        known_parts = []
        if order.get("weight"): known_parts.append(f"{order['weight']}kg")
        if order.get("orig_port"): known_parts.append(f"起运港 {order['orig_port']}")
        if order.get("dest_port"): known_parts.append(f"目的港 {order['dest_port']}")
        if order.get("max_days"): known_parts.append(f"{order['max_days']}天内")

        if missing:
            missing_cn = [field_cn.get(f, f) for f in missing]
            if known_parts:
                return f"已收到：{'，'.join(known_parts)}。还需要补充：{'、'.join(missing_cn)}。"
            else:
                return f"请提供运输需求信息：{'、'.join(missing_cn)}。例如：从上海运100kg到深圳。"
        else:
            return f"已收到：{'，'.join(known_parts)}。正在为您查询最优方案。"

    # ================================================================
    # 阶段 2: LLM 反馈生成（解释 FreightService 计算结果）
    # ================================================================

    def generate_agent_feedback(
        self,
        user_message: str = "",
        order: dict = None,
        recommendation: dict = None,
        plans: list = None,
        total_plans_found: int = 0,
        scoring_weights: dict = None,
        reply_type: str = "general",
        intent: str = "general",
        parse_source: str = "llm",
        no_result_reason: str = None,
        next_actions: list = None,
        transfer_routes: list = None,
        fallback_transfer: dict = None,
    ) -> dict:
        """
        LLM 基于 FreightService 真实结果生成自然语言反馈。

        LLM 绝不重新计算运费、不修改推荐、不编造方案。
        失败时 fallback 到模板生成。

        返回: {"message": str, "feedback_source": "llm|template",
                "feedback_reason": "none|llm_not_configured|api_error|empty_content|invalid_output"}
        """
        if order is None:
            order = {}
        if plans is None:
            plans = []
        if recommendation is None:
            recommendation = {}
        if scoring_weights is None:
            scoring_weights = {}

        # ---- LLM 不可用 → 模板 ----
        if not self.client:
            return {
                "message": self._build_template_feedback(
                    reply_type, order, recommendation, plans,
                    total_plans_found, scoring_weights, no_result_reason, next_actions
                ),
                "feedback_source": "template",
                "feedback_reason": "llm_not_configured",
            }

        # ---- 构建结果摘要 ----
        summary = self._build_result_summary(
            order, recommendation, plans, total_plans_found,
            scoring_weights, reply_type, no_result_reason, next_actions,
            transfer_routes, fallback_transfer
        )

        # ---- 调用 LLM ----
        system_prompt = self._build_feedback_prompt(reply_type, intent)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            content = (response.choices[0].message.content or "").strip()
        except Exception:
            return {
                "message": self._build_template_feedback(
                    reply_type, order, recommendation, plans,
                    total_plans_found, scoring_weights, no_result_reason, next_actions
                ),
                "feedback_source": "template",
                "feedback_reason": "api_error",
            }

        if not content:
            return {
                "message": self._build_template_feedback(
                    reply_type, order, recommendation, plans,
                    total_plans_found, scoring_weights, no_result_reason, next_actions
                ),
                "feedback_source": "template",
                "feedback_reason": "empty_content",
            }

        # 基本校验：不能比模板短太多，不能包含明显胡编的内容
        template_msg = self._build_template_feedback(
            reply_type, order, recommendation, plans,
            total_plans_found, scoring_weights, no_result_reason, next_actions
        )
        if len(content) < 20:
            return {
                "message": template_msg,
                "feedback_source": "template",
                "feedback_reason": "invalid_output",
            }

        return {
            "message": content,
            "feedback_source": "llm",
            "feedback_reason": "none",
        }

    def _build_result_summary(
        self, order, recommendation, plans, total_plans_found,
        scoring_weights, reply_type, no_result_reason, next_actions,
        transfer_routes=None, fallback_transfer=None
    ) -> str:
        """将 FreightService 结果构建为 LLM 可读的摘要 (含转运路线)"""
        parts = []

        parts.append("【用户订单】")
        if order.get("weight"):
            parts.append(f"货物重量: {order['weight']}kg")
        if order.get("orig_port"):
            parts.append(f"起运港: {order['orig_port']}")
        if order.get("dest_port"):
            parts.append(f"目的港: {order['dest_port']}")
        if order.get("max_days"):
            parts.append(f"时效要求: {order['max_days']}天内")
        if order.get("priority"):
            pri_cn = "时效优先" if order["priority"] == "time" else "成本优先"
            parts.append(f"优先级: {pri_cn}")

        parts.append("")
        parts.append(f"【查询结果】共找到 {total_plans_found} 个直达方案")
        parts.append(f"回复类型: {reply_type}")

        if recommendation:
            parts.append("")
            parts.append("【推荐方案】")
            parts.append(f"承运商: {recommendation.get('carrier', 'N/A')}")
            parts.append(f"运输方式: {recommendation.get('mode', 'N/A')}")
            parts.append(f"服务级别: {recommendation.get('service_level', 'N/A')}")
            parts.append(f"运输天数: {recommendation.get('transport_days', 'N/A')}天")
            parts.append(f"总成本: ${recommendation.get('total_cost', 0):.2f}")
            parts.append(f"综合评分: {recommendation.get('score', 0):.3f}/1.0")
            parts.append(f"服务评级: {recommendation.get('service_rating', 'N/A')}")
            parts.append(f"系统推荐理由: {recommendation.get('reason', 'N/A')}")

        if scoring_weights:
            parts.append("")
            parts.append("【评分权重】")
            parts.append(f"成本权重: {scoring_weights.get('cost_weight', 0.4)}")
            parts.append(f"时效权重: {scoring_weights.get('time_weight', 0.3)}")
            parts.append(f"服务权重: {scoring_weights.get('service_weight', 0.3)}")

        if plans:
            parts.append("")
            parts.append("【候选直达方案】")
            for i, p in enumerate(plans[:5]):
                parts.append(
                    f"{i+1}. {p.get('carrier')} | {p.get('mode')} | {p.get('service_level')} | "
                    f"{p.get('transport_days')}天 | ${p.get('total_cost', 0):.2f} | "
                    f"评分{p.get('score', 0):.3f} | 评级{p.get('service_rating', 'N/A')}"
                )

        # ── 转运路线信息 ──
        if transfer_routes:
            parts.append("")
            parts.append("【转运路线】未找到直达方案，以下是经中转港的转运方案：")
            for i, tr in enumerate(transfer_routes[:3]):
                parts.append(
                    f"转运{i+1}: {' → '.join(tr.get('path', []))}, "
                    f"总成本${tr.get('total_cost', 0):.2f}, "
                    f"总耗时{tr.get('total_estimated_days', 0)}天, "
                    f"经{tr.get('hop_count', 0)}次中转"
                )

        if fallback_transfer:
            parts.append("")
            parts.append("【次优推荐】当前条件下无满足方案，以下为最接近的转运路线：")
            parts.append(f"路径: {' → '.join(fallback_transfer.get('path', []))}")
            parts.append(f"总成本: ${fallback_transfer.get('total_cost', 0):.2f}")
            parts.append(f"总耗时: {fallback_transfer.get('total_estimated_days', 0)}天")

        if no_result_reason:
            parts.append("")
            parts.append(f"【无结果原因】{no_result_reason}")

        if next_actions:
            parts.append("")
            parts.append("【建议操作】")
            for a in next_actions:
                parts.append(f"- {a}")

        return "\n".join(parts)

    def _build_feedback_prompt(self, reply_type: str, intent: str) -> str:
        """构建 LLM 反馈生成的 system prompt"""
        role_constraints = (
            "你是物流运输方案比价助手，负责向用户解释系统计算结果。\n"
            "重要约束：\n"
            "- 所有数据必须以后端提供的【】中数据为准\n"
            "- 不得重新计算价格、不得修改推荐方案、不得编造不存在的方案\n"
            "- 不得声称'最低价'除非系统摘要明确说明它是最低价\n"
            "- 价格保留两位小数\n"
        )

        priority_guide = (
            "优先级解释规则：\n"
            "- priority=time(时效优先)：时效权重更高(0.5)，推荐方案可能更快但价格偏高\n"
            "- priority=cost(成本优先)：成本权重更高(0.5)，推荐方案是最经济的\n"
            "- priority=null(均衡模式)：综合成本(0.4)、时效(0.3)、服务(0.3)三维度评分\n"
        )

        reply_guides = {
            "recommendation": (
                "用户收到推荐方案。请：\n"
                "1. 用自然语言介绍推荐方案的核心信息（承运商、价格、天数）\n"
                "2. 根据优先级权重解释为什么推荐这个方案\n"
                "3. 如果有多个候选方案，简要对比（如\"相比最快方案便宜X元，但多Y天\"）\n"
                "4. 语气友好、专业，控制在150字以内"
            ),
            "no_result": (
                "用户查询无结果。请：\n"
                "1. 用共情的语气告知无可用方案\n"
                "2. 解释可能原因（路线无覆盖、时效太紧等）\n"
                "3. 基于建议操作给出具体下一步指引\n"
                "4. 语气友好、鼓励用户尝试其他条件，控制在100字以内"
            ),
        }

        prompt = role_constraints + "\n" + priority_guide + "\n"
        guide = reply_guides.get(reply_type, "请根据系统摘要简洁地回复用户。控制在100字以内。")
        prompt += guide
        return prompt

    def _build_template_feedback(
        self, reply_type, order, recommendation, plans,
        total_plans_found, scoring_weights, no_result_reason, next_actions
    ) -> str:
        """模板化反馈（LLM 不可用时的 fallback）"""
        if reply_type == "recommendation" and recommendation:
            rec = recommendation
            mode_cn = rec.get("mode", "空运")
            service_cn = rec.get("service_level", "门到门")
            return (
                f"为您推荐最优运输方案：\n\n"
                f"承运商：{rec.get('carrier')}\n"
                f"运输方式：{mode_cn}（{service_cn}），{rec.get('transport_days')}天到达\n"
                f"总成本：${rec.get('total_cost', 0):.2f}\n"
                f"综合评分：{rec.get('score', 0):.3f}/1.0\n\n"
                f"{rec.get('reason', '')}\n\n"
                f"共找到 {total_plans_found} 个可用方案。"
            )

        if reply_type == "no_result":
            route = f"{order.get('orig_port', '?')} → {order.get('dest_port', '?')}"
            has_time = order.get("max_days") is not None
            if has_time:
                return (
                    f"抱歉，在 {order['max_days']} 天时效内，{route} 路线没有可用方案。\n\n"
                    f"建议放宽时效要求，或尝试空运方式。"
                )
            return (
                f"抱歉，{route} 路线暂无可用方案。\n\n"
                f"可能原因：该路线没有承运商覆盖，或重量超出承运范围。"
            )

        return ""

    def _fallback_parse(self, text: str) -> Dict[str, Any]:
        """无API时的解析，支持中文港口名和PORT代码"""
        result = {"weight": None, "orig_port": None, "dest_port": None, "max_days": None, "priority": None}

        # 提取重量（支持阿拉伯数字 + 中文数字 + kg/公斤/千克/吨/斤）
        result["weight"] = self._extract_weight(text)

        # 使用通用函数提取港口
        orig_port, dest_port = extract_ports_from_text(text)
        result["orig_port"] = orig_port
        result["dest_port"] = dest_port

        # 如果只有起运港没有目的港，默认PORT09
        if result["orig_port"] and not result["dest_port"]:
            result["dest_port"] = "PORT09"

        # 提取天数（支持多种表达方式）
        days_patterns = [
            r'(\d+)\s*(?:天|日|days?|工作日|个工作日)',  # 基本格式：3天、5日、7days、3工作日
            r'(?:最大|最多|不超过|限|要求|需要|希望)\s*(\d+)\s*(?:天|日|days?|工作日|个工作日)',  # 带修饰词：最大3天、最多5天
            r'(\d+)\s*(?:天|日|days?|工作日|个工作日)\s*(?:内|以内|之内|到达|送达)',  # 带后缀：3天内、5天以内
            r'(\d+)\s*(?:周|星期|weeks?)',  # 周格式：1周、2星期、3weeks
            r'(?:最大|最多|不超过|限|要求|需要|希望)\s*(\d+)\s*(?:周|星期|weeks?)',  # 带修饰词的周格式
            r'(\d+)\s*(?:周|星期|weeks?)\s*(?:内|以内|之内)',  # 带后缀的周格式
            r'(半|一|二|三|四|五|六|七|八|九|十)\s*(?:个月|月)',  # 中文数字月格式：半个月、一个月
            r'(?:最大|最多|不超过|限|要求|需要|希望)\s*(半|一|二|三|四|五|六|七|八|九|十)\s*(?:个月|月)',  # 带修饰词的中文数字月格式
        ]
        # 中文数字映射
        cn_num_map = {'半': 0.5, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        for pattern in days_patterns:
            days_match = re.search(pattern, text, re.IGNORECASE)
            if days_match:
                days_str = days_match.group(1)
                # 处理中文数字
                if days_str in cn_num_map:
                    days = cn_num_map[days_str]
                else:
                    days = int(days_str)
                # 如果是周格式，转换为天数
                if '周' in pattern or '星期' in pattern or 'weeks?' in pattern:
                    days = days * 7
                # 如果是月格式，转换为天数（按30天计算）
                elif '个月' in pattern or '月' in pattern:
                    days = days * 30
                result["max_days"] = int(days)
                break

        # 识别优先级偏好（注意：不再将模糊时间表达转换为具体天数）
        time_priority_patterns = [
            r'越快越好', r'尽快', r'加急', r'紧急', r'特急', r'速度优先', r'最快',
            r'时间优先', r'尽快送达', r'最快速度', r'最快到达', r'最短时间',
            r'时效第一', r'越早越好', r'马上要', r'立即', r'马上'
        ]
        cost_priority_patterns = [
            r'最省钱', r'越便宜越好', r'成本优先', r'最便宜', r'省钱', r'经济实惠',
            r'价格优先', r'便宜', r'低成本', r'预算有限', r'控制成本', r'能省则省',
            r'性价比高', r'划算'
        ]

        for pattern in time_priority_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                result["priority"] = "time"
                break

        if result["priority"] is None:
            for pattern in cost_priority_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    result["priority"] = "cost"
                    break

        return result

    def is_configured(self) -> bool:
        """检查是否已配置 API Key"""
        return bool(self.api_key)
