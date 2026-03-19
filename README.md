# astrbot_plugin_douyin_live_pulse

用于监控单个抖音直播间是否开播，并在检测到开播后通过 AstrBot HTTP API 发送通知。

## 功能说明

- 监控一个指定抖音直播间
- 在预计开播时间前后窗口内轮询
- 检测到已开播后自动推送消息并结束本轮监控
- 缓存 [`ttwid`](main.py:44) 以减少重复获取
- 提供 AstrBot 指令用于查看状态、启动、停止监控

## 依赖

需要 AstrBot 运行环境可用，并安装 [`requests`](main.py:8) 依赖。

## 配置

当前配置直接写在 [`CONFIG`](main.py:14) 中，需至少修改以下字段：

- [`douyin_id`](main.py:16)：抖音直播间 ID
- [`expected_live_time`](main.py:18)：预计开播时间，格式 `HH:MM`
- [`astrbot_url`](main.py:26)：AstrBot HTTP API 地址
- [`astrbot_target`](main.py:30)：推送目标 `unified_msg_origin`
- [`astrbot_token`](main.py:32)：如果 HTTP API 启用了鉴权则填写
- [`auto_start`](main.py:34)：插件加载后是否自动启动监控

## 指令

- `/douyin_live_status`：查看当前监控状态
- `/douyin_live_start`：手动启动监控
- `/douyin_live_stop`：停止监控

## 工作方式

插件在 [`initialize()`](main.py:233) 中按配置自动启动后台监控任务，后台任务会在 [`_monitor_loop()`](main.py:258) 中：

1. 获取或复用缓存的 `ttwid`
2. 等待进入预计开播时间窗口
3. 在窗口内以随机间隔轮询直播状态
4. 检测到开播后调用 [`push_message()`](main.py:186) 推送通知
5. 推送完成后结束本轮任务

## 说明

- [`monitor.py`](monitor.py) 原有逻辑已整合进 [`main.py`](main.py)
- `ttwid` 缓存文件会写入插件目录下的 [`douyin_ttwid.json`](douyin_ttwid.json)
- 推送依赖 AstrBot 的 HTTP API 能从当前插件环境访问
