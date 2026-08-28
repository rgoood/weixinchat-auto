# -*- coding: utf-8 -*-
"""
微信个人号 AI 自动回复（微信 4.x 客户端）
原理：轮询本地加密数据库的会话表发现新消息（wechatauto）→ AI 生成 → UIA 模拟发送
降险：群聊默认不回、不回自己、随机延迟、冷却、排除系统号
"""
import json
import os
import random
import re
import sys
import time
import logging

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("wechatauto").setLevel(logging.WARNING)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "wxreply.log")

# ============ 行为配置 ============
WHITELIST = []          # 只回复这些人（昵称/备注），留空 = 所有私聊
IGNORE_GROUPS = True    # 群聊不自动回（推荐）
REPLY_DELAY = (3, 8)    # 回复前随机延迟（秒）
COOLDOWN = 5            # 同一联系人回复最小间隔（秒）
POLL_INTERVAL = 2       # 会话表轮询间隔（秒）
# =================================

# 日志双写：控制台 + 文件
class _Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            try:
                s.write(data); s.flush()
            except Exception:
                pass
    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

_log_stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
sys.stdout = _Tee(sys.__stdout__, _log_stream)
sys.stderr = _Tee(sys.__stderr__, _log_stream)

# ---- AI 配置（复用 ClawBot 的 config.json）----
with open(CONFIG_FILE, encoding="utf-8") as f:
    _cfg = json.load(f)
_pc = _cfg["providers"][_cfg["provider"]]

from openai import OpenAI
ai_client = OpenAI(api_key=_pc["api_key"], base_url=_pc["base_url"])
SYSTEM_PROMPT = _pc["prompt"]
AI_MODEL = _pc["model"]

_histories = {}
_hist_lock = __import__("threading").Lock()
MAX_HISTORY = 6
_seen = set()          # (username, sort_seq) 去重
_last_reply = {}       # username -> 上次回复时间


def is_self(sender_id) -> bool:
    # wechatauto 中 2=自己（官方语义），1 也兜底
    return sender_id in (1, 2)


def is_system_chat(username: str) -> bool:
    if username in ("filehelper", "weixin", "fmessage", "brandsessionholder",
                    "brandservicesessionholder", "medianote"):
        return True
    if username.startswith("gh_"):            # 公众号
        return True
    for suf in ("@chatroom", "@openim", "@weclaw", "@im.wechat", "@chatbot"):
        if username.endswith(suf):
            return True
    return False


def ai_reply(user: str, text: str) -> str:
    with _hist_lock:
        hist = _histories.setdefault(user, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(hist)
    messages.append({"role": "user", "content": text})
    try:
        resp = ai_client.chat.completions.create(
            model=AI_MODEL, messages=messages, max_tokens=500, timeout=60)
        reply = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[AI失败] {type(e).__name__}: {str(e)[:120]}")
        return ""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()
    reply = reply.replace("**", "")
    if reply:
        with _hist_lock:
            hist.append({"role": "user", "content": text})
            hist.append({"role": "assistant", "content": reply})
            del hist[:-MAX_HISTORY]
    return reply


def process(db, guia_send, username: str, msgs: list, last_ts_map: dict):
    """处理某会话的新消息（取最后一条文本触发回复）。"""
    now = time.time()
    if now - _last_reply.get(username, 0) < COOLDOWN:
        return
    # 找对方发的最后一条文本
    target = None
    for m in msgs:
        if is_self(m.get("sender_id")):
            continue
        if m.get("type") != "文本":
            continue
        content = (m.get("content") or "").strip()
        if content:
            target = content
    if not target:
        return
    if WHITELIST and username not in WHITELIST:
        return
    print(f"[收到] {username[:22]}: {target[:50]}")
    time.sleep(random.uniform(*REPLY_DELAY))
    reply = ai_reply(username, target)
    if not reply:
        return
    try:
        guia_send(reply, username)
        _last_reply[username] = time.time()
        print(f"[回复] {username[:22]}: {reply[:50]}")
    except Exception as e:
        print(f"[发送失败] {type(e).__name__}: {str(e)[:150]}")


def main():
    from wechatauto import WeChatDB
    from wechatauto.guia import quick_send

    print("=" * 56)
    print("  微信个人号 AI 自动回复 v2（会话表轮询版）")
    print(f"  模型: {AI_MODEL}")
    print(f"  群聊: {'不回' if IGNORE_GROUPS else '回复'} | 白名单: {'全部私聊' if not WHITELIST else WHITELIST}")
    print("=" * 56)

    db = WeChatDB()
    # 记录每个会话已知的 last_time；首轮全部视为已读（只回启动后的新消息）
    known = {}
    for s in db.get_sessions(limit=500):
        known[s["username"]] = s.get("last_time") or 0
    print(f"[运行中] 初始 {len(known)} 个会话，每 {POLL_INTERVAL}s 扫一次会话表。Ctrl+C 停止")

    while True:
        try:
            for s in db.get_sessions(limit=500):
                u = s["username"]
                lt = s.get("last_time") or 0
                old = known.get(u)
                if old is None:
                    # 新出现的会话：只认当前时间以后的
                    known[u] = lt
                    continue
                if lt > old:
                    known[u] = lt
                    if IGNORE_GROUPS and u.endswith("@chatroom"):
                        continue
                    if is_system_chat(u):
                        continue
                    # 有新动态：取 old 秒之后的消息
                    try:
                        msgs = db.get_new_messages(u, since_seq=old * 1000)
                    except Exception as e:
                        print(f"[读取失败] {u[:20]}: {str(e)[:100]}")
                        continue
                    # 去重后处理
                    fresh = []
                    for m in msgs:
                        key = (u, m.get("sort_seq"))
                        if key in _seen:
                            continue
                        _seen.add(key)
                        fresh.append(m)
                    if fresh:
                        process(db, quick_send, u, fresh, known)
        except Exception as e:
            print(f"[轮询异常] {type(e).__name__}: {str(e)[:120]}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
