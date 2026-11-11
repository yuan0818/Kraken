# FLA-01：PhoneAutomationTest 接入核验

2026-10-21，按用户“参考 PhoneAutomationTest 中的实现”核验本地源代码。范围为 `<CHECKOUT_ROOT>/PhoneAutomationTest/NothingRefactor/flash_tool/`，这是静态接入依据，不是 Linux 机器正在运行版本的证明。未连接手机，未取固件，未执行刷写。

## 已定位的调用链

`python -m flash_tool` → `app.main()` → `FlashToolApp` → `FlashEngine(root, product_name, sn_list, share_path, local_path)` → `_flash_task()`。

这是 **GUI 入口**：app 创建 Tk 窗口，engine 通过 `root.after`、弹窗和进度控件回传状态。不能把 `python -m flash_tool` 当成生产无界面 CLI。平台应在原工程抽取 headless service，再保留 GUI 包装它。

| 阶段 | 现有实现与源码位置 | 平台接入要求 |
| --- | --- | --- |
| 设备预检 | `flash_engine.py:33` 构造函数；DeviceInfoCache 检查 unlocked | 分离为结构化结果；指定单台 SN；机型标签匹配与固件产品兼容分别判断 |
| 取包 | `_flash_task`，`flash_engine.py:239`；HTTP 列表/下载，或目录枚举与文件复制；优先 fastboot tar.gz | SMB 可由 Linux 挂载目录或源适配器提供；该路径未见 FTP 下载实现，FTP 需新增来源适配 |
| 内容缓存 | `file_utils.py:39` 以文件名命名，`is_file_same` 比大小/mtime | 固件在 Run 触发时固定路径和校验摘要；不以大小/mtime 证明内容一致 |
| 解包 | `file_utils.py:67`、`flash_engine.py:329` | 使用独立受控目录和明确安全提取策略；当前路径前缀比较与 tar 提取默认行为不足以作为跨 Python 版本一致的边界 |
| merge_super | `flash_engine.py:333`，寻找 merge_super.sh/bat 并调用 `_run_script` | `_run_script` 返回失败，但当前调用点忽略返回值；无界面服务必须失败即终止，禁止继续刷写 |
| 刷写 | `_flash_single_device`，`flash_engine.py:194`；先启用 nothing debug，再运行包内脚本 | 对每个已分配设备单独调用，固定工作目录与 SN，不让原工具另选设备 |
| 结束判定 | `flash_engine.py:226` 以脚本退出码判断成功 | 退出码仅是脚本结果，还需刷后模式、设备身份、系统版本及健康检查；缺证据不能直接成功 |
| 取消 | 未见公开的结构化取消/检查点接口；GUI 关闭按钮只销毁窗口 | 分阶段取消契约；刷写中禁止把 kill 当安全取消；无法判断时保留设备占用、转恢复 |

脚本定位规则在 `file_utils.py:15`：递归寻找 `Fastboot_Flash_*.sh`（Linux）或 `.bat`，排除名称包含 `Without_Data` 的脚本。Linux 实际组合形式在 engine 源码中为：

```text
工作目录：包内刷机脚本所在目录
输入：一个换行，用于脚本交互
参数：<Fastboot_Flash_*.sh> -f <SN> -a <SN>
可选前置：merge_super.sh
```

上述是源代码推导出的调用形式，不是已验证的部署命令。包内脚本可能变化，真正接入时必须核对脚本参数含义、是否所有 adb/fastboot 调用都带 SN、是否有隐式清数据/重启及产品限制。平台适配器不执行拼接 shell 字符串，而由原工程无界面服务按 argv 调用已核验入口。

## 交付给 FLA-02/03/04 的接口要求

输入：attempt_id、设备资产/SN/endpoint、租约代次、已固定包清单（路径、大小、sha256、产品元数据）、独立工作目录、允许操作和取消策略。不能用用户任意命令字符串替代方案。

输出：JSONL 阶段事件（preflight/fetch/extract/merge/flash/reboot/verify/cleanup）、单调序号、日志/证据文件清单、原始退出码、结构化失败原因和清理结果。最终成功需要刷后验证，不由 GUI tab 图标决定。

待实现：原工程提取无界面 service/CLI、校验失败阻断、单设备执行、进程身份与取消状态查询、结构化事件；平台保留适配器与租约/工作区管理。M0 已给出共同适配边界和不可执行的硬件适配占位，未复制刷机引擎。

待真实环境验证：部署 Linux 的工具版本与权限、SMB/FTP 样例包、Fastboot/ADB 序列号映射、解锁与产品检查、刷后验证、安全取消/中断恢复、USB Hub 与并发容量。**FLA-01 静态定位与差距记录已完成，实际 Linux CLI/刷机验收仍未完成。**
