# AI Engineering Learning Lab P0 状态

日期：2026-10-18

结果：**未通过。代码执行保持关闭。**

## 已核对

- 本机 CLI 版本：`codex-cli 0.163.0-alpha.10.2`。
- `codex app-server --help` 将 app-server 标记为实验性接口。
- 检查了本机生成的协议 schema。`command/exec` 包含 argv、超时、输出上限和进程终止相关字段；schema 检查不能证明当前机器上的沙箱隔离实际生效。
- 启动 app-server 并发送初始化请求时，进程以 `Error: Could not find home directory` 退出，未建立可执行沙箱会话。
- 另一次本机 Python harness 启动返回 `uv trampoline failed to spawn Python child process: permission denied (os error 5)`。

## 未实测

由于没有成功启动沙箱进程，下列项目均没有可复现通过证据：

- 只允许后端生成的固定命令参数；课程目录只读、实验目录可写、Wiki 与其他项目不可读写。
- 网络访问被拒绝；敏感环境变量和宿主密钥不可读。
- 父目录穿越、符号链接与 Windows junction 越界被拒绝。
- 超时、输出洪泛、取消、服务崩溃和子进程回收得到明确可恢复状态。
- 本机 Host/Origin 校验能够阻止网页跨站发起执行请求。
- 课程 grader 在 Windows 上完整运行并通过固定与失败样例。

## 当前约束

- `POST /api/learning-lab/missions/{mission_key}/run` 返回 HTTP 503。
- 代码保存只保存文本和 SHA-256；不会启动进程、写入尝试结果、完成任务或发放 XP。
- 不存在裸 `subprocess` 回退路径。

本状态仅记录已观察的本机启动结果。完成以上实测后再更新 P0 状态。
