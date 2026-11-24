# -*- coding: utf-8 -*-
"""
HY Memory SDK - pytest 配置

共享的 fixtures 和配置。
"""

import os
from pathlib import Path

import pytest

from hy_memory.config import parse_dotenv


# 加载 .env 文件（如果存在，向上逐级查找）
def _load_env():
    cur = Path(__file__).resolve().parent
    for _ in range(5):
        candidate = cur / ".env"
        if candidate.exists():
            # 与运行时共用同一个解析器：`.env.example` 用的是行尾注释写法，
            # 自己再写一遍 `partition("=")` 会把注释一起带进值里。
            values = parse_dotenv(candidate.read_text(encoding="utf-8"))
            for key, value in values.items():
                if key not in os.environ:
                    os.environ[key] = value
            return
        cur = cur.parent


_load_env()


def pytest_configure(config):
    """pytest 配置钩子"""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )


@pytest.fixture(scope="session")
def test_uid():
    """测试用 uid"""
    return "test_user"


@pytest.fixture(scope="session")
def test_agent_id():
    """测试用 agent_id"""
    return "test_scene"


@pytest.fixture
def sample_text_content():
    """示例文本内容"""
    return "用户喜欢在周末去爬山，特别喜欢北京香山"


@pytest.fixture
def sample_qa_pair():
    """示例 QA 对"""
    from hy_memory.models import QAPair
    return QAPair(
        question="你最喜欢的食物是什么？",
        answer="我最喜欢吃火锅，尤其是四川麻辣火锅"
    )


@pytest.fixture
def sample_dialogue():
    """示例对话"""
    return [
        {"role": "user", "content": "你好！"},
        {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"},
        {"role": "user", "content": "我想了解一下天气"},
        {"role": "assistant", "content": "今天北京天气晴朗，气温 15-25 度"}
    ]
