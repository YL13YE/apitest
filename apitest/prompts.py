"""内置测试提示词预设。"""
from __future__ import annotations

_LONG_INPUT_PARA = (
    "人工智能（Artificial Intelligence）是研究、开发用于模拟、延伸和扩展人的智能的理论、方法、技术及应用系统的一门技术科学。"
    "它试图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。该领域的研究包括机器人、语言识别、"
    "图像识别、自然语言处理和专家系统等。自诞生以来，理论和技术日益成熟，应用领域也不断扩大。"
    "The field of machine learning has seen rapid progress over the past decade, driven by larger datasets, "
    "more compute, and architectural innovations such as the transformer. Large language models are now used for "
    "coding, writing, search, and customer support, and their evaluation increasingly focuses on latency, throughput, "
    "and cost in addition to raw quality. "
)

PRESETS = {
    "short": {
        "prompt": "用一句话介绍你自己。",
        "max_tokens": 64,
        "desc": "极短输出，主要看首字延迟（TTFT）",
    },
    "medium": {
        "prompt": "请用中文写一段 300 字左右的短文，介绍人工智能的发展历史，分三段。",
        "max_tokens": 512,
        "desc": "中等输出（默认），兼顾首字延迟与生成速度",
    },
    "long": {
        "prompt": "Write a detailed, well-structured 1000-word essay on the history and future of the Internet, with section headings.",
        "max_tokens": 2048,
        "desc": "长输出，主要看稳定吞吐（tok/s）",
    },
    "code": {
        "prompt": "Write a Python module implementing an LRU cache class with get/put/delete, type hints, docstrings, and a small set of unittest tests. Output code only.",
        "max_tokens": 1024,
        "desc": "代码生成场景",
    },
    "long-input": {
        "prompt": _LONG_INPUT_PARA * 12 + "\n\n请用三句话总结上面的内容。",
        "max_tokens": 256,
        "desc": "长输入（约 3000 tokens）短输出，测试 prefill 速度",
    },
    "json": {
        "prompt": "Return a JSON array of 20 objects, each with fields id, city, country, population (integer). Output JSON only.",
        "max_tokens": 768,
        "desc": "结构化 JSON 输出",
    },
}


def describe_presets() -> str:
    return "\n".join(f"  {k:<11} {v['desc']}（max_tokens={v['max_tokens']}）" for k, v in PRESETS.items())
