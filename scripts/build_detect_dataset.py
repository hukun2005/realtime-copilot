"""生成 P4.6 人工标注检测数据集（130 条）。

不是 ASR 输出；纯文本规则评测，用于回归检测器，不代表真实会议表现。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "detect" / "labeled.jsonl"

TOPICS = [
    "Vue3 的响应式原理", "闭包", "JavaScript 的事件循环", "原型链", "虚拟 DOM",
    "HTTP 缓存", "数据库索引", "事务隔离级别", "死锁", "幂等设计",
    "分布式锁", "微服务拆分", "垃圾回收", "内存泄漏", "跨域",
    "鉴权", "TypeScript 泛型", "React Hooks", "SQL 注入", "算法复杂度",
]
STANDALONE_TECHNICAL = [
    "Vue3 和 Vue2 有什么区别？",
    "解释一下 JavaScript 的事件循环。",
    "说说原型链是什么。",
    "HTTP 和 HTTPS 有什么区别？",
    "为什么数据库索引能加速查询？",
    "请解释一下事务的 ACID。",
    "什么是跨域，怎么解决？",
    "谈谈你对 TypeScript 泛型的理解。",
    "React Hooks 有什么使用限制？",
    "如何避免 SQL 注入？",
]
FOLLOWUPS = [
    "它和 Vue2 有什么区别？",
    "那为什么 Vue3 选择 Proxy？",
    "那如果代理失效怎么办？",
    "具体怎么实现？",
    "然后呢？",
    "还有呢？",
    "你刚才说的响应式具体指什么？",
    "这个方案有什么缺点？",
    "那性能怎么优化？",
    "怎么解决这个问题？",
    "它和闭包有什么关系？",
    "那为什么会出现内存泄漏？",
    "那如果并发很高呢？",
    "具体展开说说。",
    "这个和原型链有什么关系？",
    "那为什么需要事务？",
    "那如果缓存失效呢？",
    "还有别的方案吗？",
    "你刚才提到的幂等怎么保证？",
    "怎么验证这个设计？",
]
PROJECT = [
    "你在项目中如何做权限管理？",
    "介绍一下你负责的模块。",
    "项目里最大的难点是什么？",
    "你如何做技术选型？",
    "实习期间你完成了什么？",
    "你项目的架构是怎样的？",
    "需求变更时你怎么处理？",
    "你在项目中如何保证上线质量？",
    "项目里你负责哪部分？",
    "你在项目中如何做性能优化？",
    "你和团队怎么协作？",
    "项目的迭代节奏是怎样的？",
    "你在项目中如何评估工作量？",
    "项目遇到的最大挑战是什么？",
    "你在项目中如何做接口设计？",
    "项目里用了哪些技术？",
    "你在项目中如何做错误处理？",
    "你在项目中如何做数据校验？",
    "项目上线后怎么监控？",
    "你在项目中如何做代码评审？",
]
BEHAVIORAL = [
    "你遇到过最大的团队冲突是什么？",
    "你如何面对工作压力？",
    "说说你的一次失败经历。",
    "你的优点和缺点是什么？",
    "你的职业规划是什么？",
    "你为什么离职？",
    "你如何看待加班？",
    "你和同事意见不一致怎么办？",
    "你如何向领导汇报问题？",
    "你最近一次复盘学到了什么？",
    "你如何保持学习？",
    "你遇到过最难沟通的人是谁？",
    "你如何接受批评？",
    "你如何激励团队？",
    "你做过最冒险的决定是什么？",
]
STATEMENTS = [
    "我昨天完成了一个项目。",
    "今天讨论了接口设计。",
    "这个方案还需要再确认。",
    "我们下午三点开会。",
    "文档已经发到群里。",
    "接口已经上线了。",
    "我负责用户模块。",
    "团队这周在联调。",
    "需求已经确认。",
    "测试环境部署完成。",
    "这个问题昨天解决了。",
    "我看了相关代码。",
    "数据库加了一个索引。",
    "缓存命中率还不错。",
    "日志里没有异常。",
    "发布计划定在周五。",
    "我整理了一份会议记录。",
    "接口响应时间大约两百毫秒。",
    "前端使用 Vue3。",
    "后端使用 Python。",
    "项目分为三个阶段。",
    "我们复用了现有组件。",
    "这次迭代范围很小。",
    "用户反馈比较正面。",
    "监控告警已经配置。",
    "回滚方案也准备好了。",
    "代码评审发现两个问题。",
    "我把配置抽出来了。",
    "这个字段需要兼容旧版本。",
    "下周开始做压测。",
]
CHATTER = [
    "今天天气不错。",
    "我们休息一下。",
    "我马上回来。",
    "会议室有点冷。",
    "先喝口水吧。",
    "网络好像有点卡。",
    "大家都能听到我的声音。",
    "我这边没问题。",
    "好的，收到。",
    "稍等一下。",
    "我们继续。",
    "这个投影不太清楚。",
    "午饭还没定。",
    "今天人挺多的。",
    "我分享一下屏幕。",
]


def row(text: str, expected_type: str, *, question: bool, parent: bool = False) -> dict:
    return {
        "text": text,
        "expected_is_question": question,
        "expected_type": expected_type,
        "expected_parent": parent,
        "source": "handwritten",
    }


def main() -> None:
    records: list[dict] = []
    for index, topic in enumerate(TOPICS):
        records.append(row(f"什么是{topic}？", "technical", question=True))
        if index < len(FOLLOWUPS):
            records.append(row(FOLLOWUPS[index], "follow_up", question=True, parent=True))
    for text in STANDALONE_TECHNICAL:
        records.append(row(text, "technical", question=True))
    for text in PROJECT:
        records.append(row(text, "project", question=True))
    for text in BEHAVIORAL:
        records.append(row(text, "behavioral", question=True))
    for text in STATEMENTS:
        records.append(row(text, "statement", question=False))
    for text in CHATTER:
        records.append(row(text, "statement", question=False))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(records)} rows)")


if __name__ == "__main__":
    main()
