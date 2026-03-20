
import asyncio
import json
import os
import random
import time
from typing import Optional

import requests
from requests.utils import dict_from_cookiejar

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
]

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "douyin_ttwid.json")


def _get_cached_ttwid() -> Optional[str]:
    if not os.path.exists(_CACHE_FILE):
        return None
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > 43200:
            return None
        return data.get("ttwid")
    except Exception:
        return None


def _save_ttwid(ttwid: str):
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ttwid": ttwid, "ts": time.time()}, f, ensure_ascii=False)


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
                "region": "cn",
                "aid": 1768,
                "needFid": False,
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
                "cbUrlProtocol": "https",
                "union": True,
            },
            timeout=15,
        )
        ttwid = dict_from_cookiejar(resp.cookies).get("ttwid")
        if ttwid:
            logger.info("[douyin_live_pulse] ttwid 获取成功")
            _save_ttwid(ttwid)
            return ttwid
    except Exception as e:
        logger.error(f"[douyin_live_pulse] 生成 ttwid 失败: {e}")
    return None


def get_ttwid(force: bool = False) -> Optional[str]:
    if not force:
        cached = _get_cached_ttwid()
        if cached:
            return cached
    return _generate_ttwid()


def query_live(douyin_id: str, ttwid: str) -> dict:
    ua = random.choice(_USER_AGENTS)
    try:
        resp = requests.get(
            "https://live.douyin.com/webcast/room/web/enter/",
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-encoding": "gzip, deflate",
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
    content_type = (resp.headers.get("Content-Type") or "").lower()
    body_text = (resp.text or "").strip()
    if not body_text:
        return {"ok": False, "reason": "ttwid_expired"}
    if "json" not in content_type and not body_text.startswith(("{", "[")):
        return {"ok": False, "reason": "响应不是 JSON，可能是风控页或 ttwid 已失效"}

    try:
        result = resp.json()
    except ValueError:
        try:
            result = json.loads(body_text)
        except Exception as e:
            preview = body_text[:120].replace("\n", " ").replace("\r", " ")
            return {"ok": False, "reason": f"JSON 解析失败: {e}; 响应片段: {preview}"}

    if result.get("status_code") != 0:
        return {"ok": False, "reason": f"API 错误码 {result.get('status_code')}"}

    data = result.get("data") or {}
    room_datas = data.get("data")
    if not room_datas:
        return {"ok": False, "reason": "未开通直播间或数据为空"}

    try:
        room_data = room_datas[0]
        room_status = data.get("room_status")
        nickname = data.get("user", {}).get("nickname", douyin_id)
    except Exception as e:
        return {"ok": False, "reason": f"解析失败: {e}"}

    is_live = room_status == 0
    room_title = room_data.get("title", "") if is_live else ""

    return {
        "ok": True,
        "is_live": is_live,
        "nickname": nickname,
        "room_title": room_title,
        "jump_url": f"https://live.douyin.com/{douyin_id}",
    }


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


@register("astrbot_plugin_douyin_live_pulse", "yunus", "抖音直播开播监控插件", "1.0.1")
class DouyinLivePulsePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        # AstrBot 框架在实例化插件时会将用户在面板中填写的配置
        # （经 _conf_schema.json 定义+默认值合并后）作为 config 参数传入。
        # 直接从 config 读取，不再需要任何二次查找逻辑。
        self.config = config or {}

        self.douyin_id            = self.config.get("douyin_id", "123456")
        self.expected_live_time   = self.config.get("expected_live_time", "20:00")
        self.watch_before_minutes = self.config.get("watch_before_minutes", 10)
        self.watch_after_minutes  = self.config.get("watch_after_minutes", 60)
        self.interval_min         = self.config.get("interval_min", 45)
        self.interval_max         = self.config.get("interval_max", 90)
        self.message_target       = self.config.get("message_target", "")
        self.auto_start           = self.config.get("auto_start", True)

        self.monitor_task: Optional[asyncio.Task] = None
        self.running = False
        self.last_status = "未启动"

        logger.info(
            f"[douyin_live_pulse] 配置加载完成 -> 直播间: {self.douyin_id}, "
            f"预计开播: {self.expected_live_time}, "
            f"推送目标: {self.message_target}"
        )

    async def initialize(self):
        self.last_status = "插件已初始化"
        if self.auto_start:
            await self.start_monitor()

    async def terminate(self):
        await self.stop_monitor()

    async def start_monitor(self):
        if self.monitor_task and not self.monitor_task.done():
            self.running = True
            self.last_status = "监控任务已在运行中"
            return
        self.running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        self.last_status = "监控任务已启动"
        logger.info("[douyin_live_pulse] 监控任务已启动")

    async def stop_monitor(self):
        self.running = False
        task = self.monitor_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.monitor_task = None
        self.last_status = "监控任务已停止"
        logger.info("[douyin_live_pulse] 监控任务已停止")

    async def _monitor_loop(self):
        douyin_id = self.douyin_id
        expected  = self.expected_live_time
        before    = self.watch_before_minutes
        after     = self.watch_after_minutes
        iv_min    = self.interval_min
        iv_max    = self.interval_max

        logger.info(f"[douyin_live_pulse] 监控目标: {douyin_id} 预计开播: {expected}")
        logger.info(f"[douyin_live_pulse] 监控窗口: 开播前 {before} 分钟 ～ 开播后 {after} 分钟")

        ttwid = await asyncio.to_thread(get_ttwid)
        if not ttwid:
            self.last_status = "无法获取 ttwid，监控未启动"
            self.running = False
            logger.error("[douyin_live_pulse] 无法获取 ttwid，退出")
            return

        try:
            while self.running:
                if not _in_window(expected, before, after):
                    await self._sleep_until_window(expected, before, after)
                    if not self.running:
                        break
                    self.last_status = "进入监控窗口，开始检测"
                    logger.info("[douyin_live_pulse] 进入监控窗口，开始检测")

                logger.info("[douyin_live_pulse] 开始轮询...")
                while self.running and _in_window(expected, before, after):
                    result = await asyncio.to_thread(query_live, douyin_id, ttwid)

                    if not result["ok"]:
                        if result["reason"] == "ttwid_expired":
                            logger.warning("[douyin_live_pulse] ttwid 过期，重新获取")
                            ttwid = await asyncio.to_thread(get_ttwid, True) or ttwid
                        else:
                            logger.warning(f"[douyin_live_pulse] 查询失败: {result['reason']}")
                        interval = random.uniform(iv_min, iv_max)
                        self.last_status = f"查询失败，{interval:.0f} 秒后重试"
                        await asyncio.sleep(interval)
                        continue

                    if result["is_live"]:
                        self.last_status = f"检测到开播：{result['nickname']}"
                        logger.info(
                            f"[douyin_live_pulse] 检测到开播！{result['nickname']}: {result['room_title']}"
                        )
                        if await self.push_message(
                            result["nickname"],
                            result["room_title"],
                            result["jump_url"],
                        ):
                            self.last_status = "已推送开播通知，监控结束"
                        else:
                            self.last_status = "检测到开播，但推送失败，监控结束"
                        self.running = False
                        self.monitor_task = None
                        logger.info("[douyin_live_pulse] 推送完成，监控结束")
                        return

                    interval = random.uniform(iv_min, iv_max)
                    self.last_status = f"未开播，{interval:.0f} 秒后重试"
                    logger.info(f"[douyin_live_pulse] 未开播，{interval:.0f} 秒后重试")
                    await asyncio.sleep(interval)

                if self.running:
                    self.last_status = f"监控窗口结束，等待下一天 {expected}"
                    logger.info(f"[douyin_live_pulse] 监控窗口结束（开播后 {after} 分钟无响应），等待下一天")
        except asyncio.CancelledError:
            self.last_status = "监控任务已取消"
            logger.info("[douyin_live_pulse] 监控任务被取消")
            raise
        except Exception as e:
            self.last_status = f"监控异常退出: {e}"
            logger.exception(f"[douyin_live_pulse] 监控异常退出: {e}")
        finally:
            if not self.running:
                self.monitor_task = None

    async def _sleep_until_window(self, expected: str, before: int, after: int):
        while self.running and not _in_window(expected, before, after):
            base = _hhmm_to_minutes(expected)
            cur  = _now_minutes()
            diff = (base - before) - cur
            if diff <= 0:
                diff += 1440
            self.last_status = f"距离监控窗口还有 {diff} 分钟"
            logger.info(
                f"[douyin_live_pulse] 距离监控窗口还有 {diff} 分钟（{expected} 前 {before} 分钟），等待中..."
            )
            await asyncio.sleep(min(60, diff * 60))

    async def push_message(self, nickname: str, room_title: str, jump_url: str) -> bool:
        target = self.message_target
        if not target:
            logger.error("[douyin_live_pulse] 未配置 message_target，无法主动推送")
            return False

        msg = f"🔴 {nickname} 开播了！\n📺 {room_title}\n🔗 {jump_url}"
        message_chain = MessageChain().message(msg)

        try:
            await self.context.send_message(target, message_chain)
            logger.info(f"[douyin_live_pulse] 推送成功: {msg}")
            return True
        except Exception as e:
            logger.error(f"[douyin_live_pulse] 推送异常: {e}")
            return False

    @filter.command("直播监控状态")
    async def douyin_live_status(self, event: AstrMessageEvent):
        """查看当前抖音开播监控状态"""
        state = "运行中" if self.monitor_task and not self.monitor_task.done() else "未运行"
        message = (
            f"抖音直播监控状态\n"
            f"- 直播间: {self.douyin_id}\n"
            f"- 预计开播: {self.expected_live_time}\n"
            f"- 当前状态: {state}\n"
            f"- 详细信息: {self.last_status}"
        )
        yield event.plain_result(message)

    @filter.command("启动开播监控")
    async def douyin_live_start(self, event: AstrMessageEvent):
        """手动启动抖音开播监控"""
        await self.start_monitor()
        yield event.plain_result("抖音直播监控任务已启动")

    @filter.command("停止开播监控")
    async def douyin_live_stop(self, event: AstrMessageEvent):
        """停止当前抖音开播监控"""
        await self.stop_monitor()
        yield event.plain_result("抖音直播监控任务已停止")

    @filter.command("修改时间")
    async def douyin_live_set_time(self, event: AstrMessageEvent):
        """修改预计开播时间，格式：修改时间 HH:MM"""
        # 取指令后面的参数部分
        raw = event.message_str.strip()
        # 兼容 "/修改时间 20:30" 和 "修改时间 20:30" 两种写法
        parts = raw.lstrip("/").split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result(
                "❌ 格式错误，请使用：修改时间 HH:MM\n例如：修改时间 20:30"
            )
            return

        new_time = parts[1].strip()

        # 校验格式
        try:
            h, m = new_time.split(":")
            h, m = int(h), int(m)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("时间超出范围")
            # 统一格式为 HH:MM
            new_time = f"{h:02d}:{m:02d}"
        except Exception:
            yield event.plain_result(
                f"❌ 时间格式无效：{new_time}\n请使用 HH:MM 格式，例如：修改时间 20:30"
            )
            return

        old_time = self.expected_live_time
        self.expected_live_time = new_time

        # 如果监控正在运行，重启以使新时间生效
        was_running = self.monitor_task and not self.monitor_task.done()
        if was_running:
            await self.stop_monitor()
            await self.start_monitor()
            yield event.plain_result(
                f"✅ 开播时间已更新：{old_time} → {new_time}\n"
                f"监控任务已自动重启，新时间立即生效。"
            )
        else:
            yield event.plain_result(
                f"✅ 开播时间已更新：{old_time} → {new_time}\n"
                f"（监控未运行，使用【启动开播监控】启动后生效）"
            )
