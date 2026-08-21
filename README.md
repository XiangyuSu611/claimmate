# ClaimMate

ClaimMate 是一个面向出差报销的 ChatGPT/Codex 插件。它使用大模型识别材料类型、费用类型、金额、项目归属和材料配对，并通过可维护的报销要求表检查材料完整性。

## 安装

需要已安装并登录 ChatGPT/Codex，且本机可使用 Python 3.10 或更高版本。

```bash
codex plugin marketplace add XiangyuSu611/claimmate
codex plugin add claimmate@claimmate
```

安装后重新打开 ChatGPT 桌面应用或新建任务，然后输入：

```text
使用 ClaimMate 初始化一个测试工作区
```

## 更新

```bash
codex plugin marketplace upgrade claimmate
codex plugin add claimmate@claimmate
```

此仓库目前为私有仓库。安装者需要拥有仓库访问权限，并在本机完成 GitHub 身份验证。
