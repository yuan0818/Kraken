# 真机调度与远程手机

设备中心选择真机，打开详情后点击“打开远程手机”。打开时为只读观看，点击“申请控制权”后可点击画面、拖动滑动、发送文字及使用系统按键。退出窗口或点击“释放控制权”会通知节点释放控制。浏览器意外关闭后，控制授权在 30 秒未续期时失效，由节点确认没有进行中的输入后释放设备。节点失联时继续保留占用。

画面默认使用流式 H.264：节点采集 → 出站 WebSocket → 控制面转发 → 浏览器 Tango WebCodecs 解码。多个窗口共用同一条采集，最后一个观看者关闭后停止采集。视频不写入数据库，慢客户端的缓存有界，断开后自动重连。浏览器显示实际已解码帧数与分辨率；静止画面不重复编码，但节点继续发送状态心跳，心跳超时会标记中断。仍可手动切换“截图兼容模式”。缩放后的点击使用归一化坐标，节点对照当前显示比例校验方向后映射到物理屏幕。观看与输入均经控制面的身份模块。

流式视频需要支持 WebCodecs 的 Chrome / Edge 和安全上下文。开发使用 localhost；其他电脑访问平台时配置 HTTPS / WSS，反向代理需允许 WebSocket Upgrade。当前媒体转发保存在单个 API 进程内，部署应保持一个 API worker；多 worker 或 HA 媒体路由还未实现。当前没有音频和 WebRTC。

默认节点采集后端为 scrcpy，固定视频服务端 3.3.3，前端解码器 @yume-chan/scrcpy-decoder-webcodecs 2.5.3。执行 `.venv/bin/python scripts/fetch_video_server.py` 下载官方发布文件并校验 SHA-256，然后设置 `VIDEO_SCRCPY_SERVER_PATH` 为输出路径，`VIDEO_SCRCPY_VERSION=3.3.3`。二进制不入库；离线 Linux 节点可预分发相同校验值的文件。视频后端使用独立 scid、临时 jar 与 adb forward，退出只清理自身资源。

A142P 当前 Android 17 镜像上的 scrcpy 视频采集未能持续出帧，此节点显式设置 `VIDEO_BACKEND=screenrecord`，使用系统原生 H.264 编码并经 PyAV 分包，浏览器仍使用同一 Tango 解码器。采集最长边约 1280、4 Mbps；兼容后端默认达到 180 秒后重新采集；本机 screenrecord 的帮助明确支持无限时长，因此本机设置 `VIDEO_SCREENRECORD_TIME_LIMIT=0` 避免周期重启，其他镜像应先核实支持情况。静止画面在采集重启后可能需要下一次 UI 变化才出首帧，页面会明确显示等待 / 错误状态，可在申请控制权后亮屏，或手动切换截图模式。此兼容方式按节点进程配置，不会静默切换为截图。Linux 其他镜像仍需各自验收 scrcpy 兼容性。

任务配置的“执行目标”可选择已接入 Artemis 的真机。批次保存目标设备 ID，忙时排队，设备离线时等待。模拟批次只使用模拟设备。测试的准备、执行、取消、清理占用均禁止申请远控输入。远控输入还在节点通过 Artemis 共享设备锁校验。

节点按一个显式 SN 启动，命令为 .venv/bin/python -m node_agent.supervisor.artemis --serial <SN>。

配置 NODE_ID、NODE_STATE_DIR、API_URL、ARTEMIS_URL、ARTEMIS_ROOT 和 ARTEMIS_PYTHON。后二者指向节点 Artemis 源码与 Python，用于共用原生设备锁。平台 Python 环境保持独立。中文输入使用 scrcpy 5.0.1 服务端的剪贴板粘贴能力，配置 SCRCPY_SERVER_PATH，默认探测 /opt/homebrew/share/scrcpy/scrcpy-server 或 /usr/share/scrcpy/scrcpy-server。协议见 [scrcpy 开发文档](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/develop.md)。

真机任务可选择平台模型，触发时固定模型和凭据版本。未指定时使用节点 Artemis 的默认配置。平台将尝试 UUID 作为原生会话 ID，启动意图先持久化，重连不会重新提交设备动作。结果依据本次原生检查与逐条断言笔记，缺少证据时为 inconclusive。原生进程启动失败且确认队列及设备均无残留时记录 blocked。状态未知时保留租约。

2026-11-03 在 macOS 直连 A142P / PacmanPro / Android 17 / SN EXAMPLE_DEVICE_SN 验证了画面、亮屏、点击、滑动、Home/Back、中文英文数字及符号输入，并清空测试搜索内容。控制权冲突、过期操作拒绝、真机目标等待远控及模拟隔离有数据库集成检查。Linux 真机、节点整机重启与多设备容量仍需验收。数据库升级到 0009 前保留协调备份，镜像升级后重启节点进程。

最终真机调度验证批次 f181d256-8e88-42ab-8027-e41b4292ffa6、原生会话 01146838-f9b1-465a-be0e-71f281106a6e 已完成。只读命令 getprop ro.product.model 返回 A142P，平台记录 PASS / complete / verified，结束后设备无活动租约和远控占用。验证过程中原生进程路径与模型环境的三次启动失败保留为历史阻塞记录。开发机为此启动独立的 Artemis API 8001，复用原有 Qwen 模型配置。真机执行的原始命令、提交结果、原生会话、检查与笔记保存在节点日志目录的 attempts/<attempt UUID>/ 下。96 项 Python 检查和前端构建通过。


延迟优化后，本机同设备抽测的画面请求为 1234–1332ms，Back 按键确认为 110–150ms。优化前画面请求为 1987–3245ms，Back 按键为 3029–3335ms。测量基于完整的控制面提交、节点处理及回传；不包含浏览器绘制或操作后等待手机动画的时间。移除了节点每次处理后固定的 2 秒等待，将状态采集限频为 2 秒，远控快速轮询；输入通道复用 Python 进程，每次输入仍分别获取 Artemis 设备锁。截图共享和输入优先级有回归检查。此设备的 scrcpy 视频通道在硬件、软件编码试验中均未持续出帧，因此后续启用了上述原生编码兼容路径。
