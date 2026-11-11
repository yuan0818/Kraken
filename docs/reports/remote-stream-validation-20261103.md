# 远控视频验证 · 2026-11-03

环境：本机 macOS 节点 mac-usb-01、显式 SN EXAMPLE_DEVICE_SN（A142P / PacmanPro / Android 17），本机控制面 8100、Web 5178，Chrome WebCodecs。没有操作另一台 SN EXAMPLE_DEVICE_SN。

- 浏览器实际解码 H.264，分辨率 576×1280。通过平台申请控制权后，网页滑动打开应用抽屉，累计实际渲染超过 50 帧；通过 Home 返回桌面。截图证据位于 `output/playwright/stream-initial.png`、`stream-swipe.png`。
- 两个独立浏览器会话同时显示画面；第二个会话申请控制权返回占用冲突，同时保持只读视频。证据 `output/playwright/stream-second-viewer.png`。浏览器帧数是各自解码计数，不用作精确 FPS / 端到端延迟测量。
- 显式终止此采集自身 pidfile 对应的 screenrecord 进程，节点报告编码中断并重建采集。在静止桌面下，重建采集可能不立刻出首帧；经平台 Home 触发画面变化后，浏览器恢复解码，记录 12 帧，见 `output/playwright/stream-recovered.png`。未声称静止场景下无缝恢复。页面会显示等待或错误，截图模式为人工可选。
- 当前镜像原生 screenrecord 支持 `--time-limit 0`，本机设置 `VIDEO_SCREENRECORD_TIME_LIMIT=0`，避免每 180 秒主动重新采集。其他镜像需核实支持。
- 默认 scrcpy 3.3.3 路径已实现，但此镜像的 scrcpy 视频未能持续出帧，因此本机显式使用 screenrecord 兼容后端。通用 scrcpy 路径尚未在其他真机镜像完成验收。
- 103 项 Python 检查、Ruff 和前端生产构建通过；包含未知设备 / 节点认证拒绝、多观看者转发与释放、慢观看者有界队列、关键帧缓存和编码重启。全套检查期间短暂停止本地调度器，避免已有全局 advisory lock 与隔离 schema 检查竞争，随后恢复调度器。

本轮没有测量持续 60 台设备容量、Linux USB 真机或网络故障下的端到端延迟。当前转发要求单 API worker，内网跨电脑访问需 HTTPS / WSS。
