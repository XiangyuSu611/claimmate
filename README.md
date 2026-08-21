# ClaimMate

ClaimMate 是一个面向出差报销的 ChatGPT/Codex 插件。它使用大模型识别材料类型、费用类型、金额、项目归属和材料配对，并通过可维护的报销要求表检查材料完整性。

当前稳定版本：`1.0.0`

## 主要功能

- 同时管理多个出差报销项目，自动判断新材料归属。
- 从对话、工作区文件夹或 IMAP 邮箱接收发票、付款记录和补充材料。
- 暂时无法归属的附件保留在 `待处理/待归属`，新建项目时自动重新判断一次，不会循环扫描。
- 使用可编辑的 `报销要求.xlsx` 检查材料完整性和付款金额。
- 说“交付财务”后自动生成逐项明细、归档并打包为 `时间-地点-报销人-报销文件.zip`。
- 原件备份、操作记录和最近一次整理撤销。

## 安装

需要已安装并登录 ChatGPT/Codex，且本机可使用 Python 3.10 或更高版本。Codex 插件市场的官方说明见 [Package your plugin](https://developers.openai.com/plugins/build/plugins)。

安装公开市场和 ClaimMate：

```bash
codex plugin marketplace add XiangyuSu611/claimmate --ref main
codex plugin add claimmate@claimmate
```

安装后重新打开 ChatGPT 桌面应用或新建任务，然后输入：

```text
使用 ClaimMate 初始化一个报销工作区
```

ClaimMate 会先询问使用者姓名、是否接入邮箱，并展示报销要求；确认完整要求后才会处理材料和启动后台监听器。

## 固定安装 1.0.0

如需固定使用稳定版本，而不是跟随 `main`：

```bash
codex plugin marketplace add XiangyuSu611/claimmate --ref v1.0.0
codex plugin add claimmate@claimmate
```

## 更新

使用 `main` 市场源时：

```bash
codex plugin marketplace upgrade claimmate
codex plugin remove claimmate@claimmate
codex plugin add claimmate@claimmate
```

更新后请新建一个任务，以加载新版本的技能和工具。

## 邮箱接入

邮箱接入用于自动下载并处理发票、付款记录等报销附件。可以直接读取 `INBOX`，不要求邮件预先带有 `ClaimMate` 标签；如果邮箱中还有大量无关附件，也可以自行选择专用邮箱或文件夹来缩小范围。

邮箱密码或应用专用密码只保存在 macOS Keychain 或 Windows Credential Manager，不写入工作区或仓库。Microsoft 及部分组织邮箱如果只允许 OAuth，需要使用授权邮箱连接器或另行实现 OAuth 适配器。

## 隐私说明

报销材料可能包含个人、行程和财务信息。ClaimMate 会在本地整理文件，但模型识别过程可能把材料文字发送给当前登录的 Codex/OpenAI 服务。请仅在符合所在组织数据政策时使用，不要把密码、令牌或应用专用密码发送到聊天中。

## 卸载

```bash
codex plugin remove claimmate@claimmate
codex plugin marketplace remove claimmate
```

卸载插件不会自动删除已经建立的报销工作区或归档文件。
