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
WHITELIST = []          # 只回复这些人（昵称/备注/群名），留空 = 不限制
IGNORE_GROUPS = False   # False=回复群聊；True=只回私聊
GROUP_REQUIRE_AT = False  # True=群里仅当有人 @你 时才回（更安全，需消息里带 @）
REPLY_DELAY = (0.2, 0.6)  # 回复前短延迟（秒）；原先 3~8s 太慢
COOLDOWN = 1.0          # 同一联系人/群回复最小间隔（秒）
POLL_INTERVAL = 1       # 会话表轮询间隔（秒）
# =================================

# 显示名缓存：wxid -> 备注/昵称（UI 搜索/复用会话必须用显示名，不能用 wxid）
_name_cache = {}

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
    # 群聊由 IGNORE_GROUPS 控制，不要在这里一律拦截 @chatroom
    for suf in ("@openim", "@weclaw", "@im.wechat", "@chatbot"):
        if username.endswith(suf):
            return True
    return False


def is_group(username: str) -> bool:
    return username.endswith("@chatroom")


def display_name(db, username: str) -> str:
    """wxid → 备注/昵称；UI 复用会话依赖显示名。"""
    cached = _name_cache.get(username)
    if cached:
        return cached
    try:
        name = (db.get_nickname(username) or "").strip() or username
    except Exception:
        name = username
    _name_cache[username] = name
    return name


def same_chat(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return (len(a) >= 2 and a in b) or (len(b) >= 2 and b in a)


def ai_reply(user: str, text: str) -> str:
    with _hist_lock:
        hist = _histories.setdefault(user, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(hist)
    messages.append({"role": "user", "content": text})
    try:
        t0 = time.time()
        resp = ai_client.chat.completions.create(
            model=AI_MODEL, messages=messages, max_tokens=512, timeout=45)
        msg = resp.choices[0].message
        reply = (msg.content or "").strip()
        # 墨点 DeepSeek 等推理模型可能把长度耗在 reasoning 上，正文为空时兜底
        if not reply:
            reply = (getattr(msg, "reasoning_content", None) or "").strip()
        print(f"[AI耗时] {time.time() - t0:.1f}s")
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


def send_reply(wx, who: str, text: str):
    """复用同一个 WeChatGUI；同会话已打开时不再搜索。"""
    uia = None
    try:
        uia = wx._get_uia()
    except Exception:
        uia = None
    cur = None
    if uia is not None:
        try:
            cur = uia.current_chat()
        except Exception:
            cur = None
    # 当前窗口已是目标会话：直接发，跳过 open_chat / 搜索
    if same_chat(cur, who) or same_chat(getattr(wx, "_current_chat", None), who):
        t0 = time.time()
        if uia is not None and uia.send_text(text):
            wx._current_chat = who
            print(f"[发送] 复用当前会话，耗时 {time.time() - t0:.1f}s")
            return
        # UIA 直发失败再走完整 send_msg（仍带 who，便于库内复用）
    t0 = time.time()
    resp = wx.send_msg(text, who, verify=False)
    ok = bool(resp)  # WxResponse.__bool__ / is_success
    print(f"[发送] {'OK' if ok else resp}，耗时 {time.time() - t0:.1f}s")
    if not ok:
        raise RuntimeError(str(resp))


def process(db, wx, username: str, msgs: list, last_ts_map: dict):
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
    who = display_name(db, username)
    if WHITELIST and who not in WHITELIST and username not in WHITELIST:
        return
    if is_group(username) and GROUP_REQUIRE_AT:
        # 粗判：消息含 @ 才回（微信群 @ 文案因版本略有差异）
        if "@" not in target and "＠" not in target:
            return
    print(f"[收到] {who}: {target[:50]}")
    # 先打 AI，再短延迟发送（以前是先睡 3~8 秒再请求，体感很慢）
    reply = ai_reply(username, target)
    if not reply:
        return
    time.sleep(random.uniform(*REPLY_DELAY))
    try:
        send_reply(wx, who, reply)
        _last_reply[username] = time.time()
        print(f"[回复] {who}: {reply[:50]}")
    except Exception as e:
        print(f"[发送失败] {type(e).__name__}: {str(e)[:150]}")


def main():
    from wechatauto import WeChatDB
    from wechatauto.guia import WeChatGUI

    print("=" * 56)
    print("  微信个人号 AI 自动回复 v2.1（会话复用 / 加速）")
    print(f"  模型: {AI_MODEL}")
    print(f"  群聊: {'不回' if IGNORE_GROUPS else ('仅@才回' if GROUP_REQUIRE_AT else '回复')} | 白名单: {'不限制' if not WHITELIST else WHITELIST}")
    print("=" * 56)

    db = WeChatDB()
    # 全程复用同一个 GUI：同会话第二次回复可走快速路径，不再重新搜索
    wx = WeChatGUI()
    # 记录每个会话已知的 last_time；首轮全部视为已读（只回启动后的新消息）
    known = {}
    for s in db.get_sessions(limit=500):
        known[s["username"]] = s.get("last_time") or 0
    print(f"[运行中] 初始 {len(known)} 个会话，每 {POLL_INTERVAL}s 扫一次。Ctrl+C 停止")

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
                        process(db, wx, u, fresh, known)
        except Exception as e:
            print(f"[轮询异常] {type(e).__name__}: {str(e)[:120]}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
