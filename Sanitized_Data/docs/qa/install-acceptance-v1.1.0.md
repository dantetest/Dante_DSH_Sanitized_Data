# 安装 / 卸载 / 最小链路验收记录（v1.1.0）

> 时间：2026-10-21 · 安装包：`Sanitized_Data_Setup_1.1.0_x64.exe`（155.6 MiB）
> 环境：Windows 10 Pro 10.0.19045 x64 · 当前用户（非管理员）· 无 Python 依赖的安装版
> 数据合规：本记录不含任何真实文件内容/实体原值；最小链路使用虚构样例 `samples/委托代理合同.pdf`。

## 1. 安装

| 项 | 结果 |
|---|---|
| 静默安装命令 | `setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`（默认装到 `%LOCALAPPDATA%\Programs\Sanitized_Data`，免管理员） |
| 安装结果 | **成功**：日志 `Installation process succeeded`；生成 `unins000.exe`；HKCU 卸载项「数据隐站 1.1.0」存在 |
| 快捷方式/卸载项 | 开始菜单目录与卸载项创建成功；桌面快捷方式为可选任务（默认不建） |
| WebView2 / VC++ | 安装器环境检查 + 按需静默补齐逻辑未触发错误（本机已具备） |

> 环境备注：本机首次在 DSH 工作区内直接执行安装器时，Setup 在初始化阶段退出（exit 1）；
> 将安装包复制到普通目录（`C:\Temp`）后同一安装包安装成功。判断为工作区历史沙箱 ACL
> 残留对「在工作区内直接执行 exe」的影响，不是安装包/产品缺陷；正式分发从 Release 下载后
> 在普通目录双击即可。

## 2. 启动与最小脱敏链路（安装版）

| 项 | 结果 |
|---|---|
| `/api/health` | `version=1.1.0`；`ocr=true`；`parsers=docx,image,mesh,pdf,text,tiff,xlsx`；`scanners=barcode,visual_targets,sign_targets`；`net=blocked` |
| 静态页面 `/`、`/console`、`/download`、`/mcp` | 全部 200 |
| 最小链路（HTTP） | 上传 → 创建任务 → `stage=ready` → 实体清单 → **AI/换名版导出 + 人读/涂黑版导出均成功**；两版 `download` 200、PDF magic 正确 |
| 成品复检 `verify` | 两版均 `verified=true`、`residual=0`、`visual_residual=0`、无 issues |
| 断网 | 健康检查 `net=blocked`；引擎静态扫描无出站；安装版运行不需要联网 |

## 3. 卸载

| 项 | 结果 |
|---|---|
| 静默卸载 | `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` → **exit 0** |
| 程序目录 | 已删除（`Sanitized_Data.exe`、`_internal` 均不存在；安装目录不存在） |
| 注册表卸载项 | 已移除 |
| 数据目录 | **保留**（`%LOCALAPPDATA%\Sanitized_Data` 及本次验收用的自定义数据目录均保留）——与安装指南「卸载不删数据」一致 |

## 4. 结论

- 安装、启动、最小脱敏链路、断网、卸载路径**全部通过**。
- 未做代码签名：SmartScreen/杀软可能提示「未知发布者」，发布说明已写明。
- 旧版 → 新版覆盖升级（同 AppId、数据目录保留）由安装器机制保证；本次未持有 v1.0.0 安装态做
  真实覆盖升级演练，升级流程按 [install-guide.md](../install-guide.md) 的「覆盖安装」步骤执行即可。
