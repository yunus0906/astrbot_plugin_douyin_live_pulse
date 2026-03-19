"""
抖音直播开播检测工具
- 只监控一个直播间
- 在设定开播时间的前后窗口内轮询
- 检测到开播（含启动时已开播）→ 推送 AstrBot → 结束
- 降低风控：随机 UA、随机轮询间隔、完整 Referer/Headers

依赖: pip install requests
"""

import json
import logging
import os
import random
import time
from typing import Optional

import requests
from requests.utils import dict_from_cookiejar

# ─────────────────────────────────────────────
# ★ 修改这里
# ─────────────────────────────────────────────
CONFIG = {
    # 直播间 ID：https://live.douyin.com/123456 → "123456"
    "douyin_id": "123456",

    # 预计开播时间
    "expected_live_time": "20:00",

    # 开播时间前几分钟开始守候
    "watch_before_minutes": 10,

    # 超过开播时间几分钟后放弃
    "watch_after_minutes": 60,

    # 轮询间隔（秒）随机区间，避免固定频率被识别
    "interval_min": 45,
    "interval_max": 90,

    # AstrBot HTTP API 地址（需启用 http_api 插件）
    "astrbot_url": "http://127.0.0.1:6185/api/v1/message/send",

    # 推送目标的 unified_msg_origin
    # 示例: "aiocqhttp:FriendMessage:123456789"
    #       "aiocqhttp:GroupMessage:987654321"
    "astrbot_target": "aiocqhttp:FriendMessage:你的QQ号",

    # AstrBot API Token（没有则留空）
    "astrbot_token": "",
}
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 反风控：随机 UA 池 ──────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
]

# ── ttwid 本地缓存（有效期 12 小时）─────────
_CACHE_FILE = "douyin_ttwid.json"


def _get_cached_ttwid() -> Optional[str]:
    if not os.path.exists(_CACHE_FILE):
        return None
    try:
        with open(_CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > 43200:
            return None
        return data.get("ttwid")
    except Exception:
        return None


def _save_ttwid(ttwid: str):
    with open(_CACHE_FILE, "w") as f:
        json.dump({"ttwid": ttwid, "ts": time.time()}, f)


def _generate_ttwid() -> Optional[str]:
    try:
        resp = requests.post(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            headers={
                "Content-Type": "application/json",
                "User-Agent": random.choice(_USER_AGENTS),
                "Referer": "https://www.ixigua.com/",
            },
            json={
                "region": "cn", "aid": 1768, "needFid": False,
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
                "cbUrlProtocol": "https", "union": True,
            },
            timeout=15,
        )
        ttwid = dict_from_cookiejar(resp.cookies).get("ttwid")
        if ttwid:
            log.info("ttwid 获取成功")
            _save_ttwid(ttwid)
            return ttwid
    except Exception as e:
        log.error(f"生成 ttwid 失败: {e}")
    return None


def get_ttwid(force: bool = False) -> Optional[str]:
    if not force:
        cached = _get_cached_ttwid()
        if cached:
            return cached
    return _generate_ttwid()


# ── 直播状态查询 ─────────────────────────────
def query_live(douyin_id: str, ttwid: str) -> dict:
    """
    成功时返回:
        {"ok": True, "is_live": bool, "nickname": str,
         "room_title": str, "jump_url": str}
    失败时返回:
        {"ok": False, "reason": str}   reason="ttwid_expired" 时需刷新
    """
    ua = random.choice(_USER_AGENTS)
    try:
        resp = requests.get(
            "https://live.douyin.com/webcast/room/web/enter/",
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-encoding": "gzip, deflate, br",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "cookie": f"ttwid={ttwid}",
                "pragma": "no-cache",
                "referer": f"https://live.douyin.com/{douyin_id}",
                "sec-ch-ua": '"Not_A Brand";v="99", "Google Chrome";v="109", "Chromium";v="109"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": ua,
            },
            params={
                "aid": "6383",
                "device_platform": "web",
                "enter_from": "web_live",
                "cookie_enabled": "true",
                "browser_language": "zh-CN",
                "browser_platform": "Win32",
                "browser_name": "Chrome",
                "browser_version": "109.0.0.0",
                "web_rid": douyin_id,
            },
            timeout=15,
        )
    except Exception as e:
        return {"ok": False, "reason": f"请求异常: {e}"}

    if resp.status_code != 200:
        return {"ok": False, "reason": f"HTTP {resp.status_code}"}
    if not resp.content:
        return {"ok": False, "reason": "ttwid_expired"}

    try:
        result = json.loads(resp.content.decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"JSON 解析失败: {e}"}

    if result.get("status_code") != 0:
        return {"ok": False, "reason": f"API 错误码 {result.get('status_code')}"}

    data = result.get("data") or {}
    room_datas = data.get("data")
    if not room_datas:
        return {"ok": False, "reason": "未开通直播间或数据为空"}

    try:
        room_data = room_datas[0]
        room_status = data.get("room_status")   # 0 = 直播中
        nickname = data.get("user", {}).get("nickname", douyin_id)
    except Exception as e:
        return {"ok": False, "reason": f"解析失败: {e}"}

    is_live = (room_status == 0)
    room_title = room_data.get("title", "") if is_live else ""

    return {
        "ok": True,
        "is_live": is_live,
        "nickname": nickname,
        "room_title": room_title,
        "jump_url": f"https://live.douyin.com/{douyin_id}",
    }


# ── AstrBot 推送 ─────────────────────────────
def push(nickname: str, room_title: str, jump_url: str) -> bool:
    headers = {"Content-Type": "application/json"}
    token = CONFIG.get("astrbot_token", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    msg = f"🔴 {nickname} 开播了！\n📺 {room_title}\n🔗 {jump_url}"
    try:
        resp = requests.post(
            CONFIG["astrbot_url"],
            headers=headers,
            json={"unified_msg_origin": CONFIG["astrbot_target"], "message": msg},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info(f"推送成功: {msg}")
            return True
        log.error(f"推送失败 HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"推送异常: {e}")
    return False


# ── 时间工具 ─────────────────────────────────
def _hhmm_to_minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _now_minutes() -> int:
    t = time.localtime()
    return t.tm_hour * 60 + t.tm_min


def _in_window(expected: str, before: int, after: int) -> bool:
    base = _hhmm_to_minutes(expected)
    cur = _now_minutes()
    return (base - before) <= cur <= (base + after)


def _sleep_until_window(expected: str, before: int):
    """每分钟检查一次，直到进入监控窗口"""
    while not _in_window(expected, before, CONFIG["watch_after_minutes"]):
        base = _hhmm_to_minutes(expected)
        cur = _now_minutes()
        diff = (base - before) - cur
        if diff <= 0:
            diff += 1440  # 跨天
        log.info(f"距离监控窗口还有 {diff} 分钟（{expected} 前 {before} 分钟），等待中...")
        # 最多睡 60 秒，以便准时进入窗口
        time.sleep(min(60, diff * 60))


# ── 主逻辑 ───────────────────────────────────
def run():
    douyin_id       = CONFIG["douyin_id"]
    expected        = CONFIG["expected_live_time"]
    before          = CONFIG["watch_before_minutes"]
    after           = CONFIG["watch_after_minutes"]
    iv_min, iv_max  = CONFIG["interval_min"], CONFIG["interval_max"]

    log.info(f"监控目标: {douyin_id}  预计开播: {expected}")
    log.info(f"监控窗口: 开播前 {before} 分钟 ～ 开播后 {after} 分钟")

    ttwid = get_ttwid()
    if not ttwid:
        log.error("无法获取 ttwid，退出")
        return

    while True:
        # ── 等待进入窗口 ──────────────────────
        if not _in_window(expected, before, after):
            _sleep_until_window(expected, before)
            log.info("进入监控窗口，开始检测")

        # ── 窗口内轮询 ────────────────────────
        log.info("开始轮询...")
        while _in_window(expected, before, after):

            result = query_live(douyin_id, ttwid)

            if not result["ok"]:
                if result["reason"] == "ttwid_expired":
                    log.warning("ttwid 过期，重新获取")
                    ttwid = get_ttwid(force=True) or ttwid
                else:
                    log.warning(f"查询失败: {result['reason']}")
                time.sleep(random.uniform(iv_min, iv_max))
                continue

            if result["is_live"]:
                log.info(f"✅ 检测到开播！{result['nickname']}: {result['room_title']}")
                push(result["nickname"], result["room_title"], result["jump_url"])
                log.info("推送完成，程序退出。")
                return  # ← 推送成功后结束

            interval = random.uniform(iv_min, iv_max)
            log.info(f"未开播，{interval:.0f} 秒后重试")
            time.sleep(interval)

        # ── 窗口结束未开播 ────────────────────
        log.info(f"监控窗口结束（开播后 {after} 分钟无响应），等待下一天")
        # 回到外层循环，等待次日窗口


if __name__ == "__main__":
    run()