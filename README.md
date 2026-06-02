# 3x-ui Cloudflare 部署器

`xui_cf_deployer.py` 是一个基于 Python 3 标准库实现的本地脚本，用于在 VPS 上自动完成：

- **可选**：在裸机上自动安装 3x-ui 面板
- 按需创建 VLESS / Trojan / VMess 节点
- 通过 **3x-ui REST API** 或 **SQLite 直写**（旧版默认）创建/删除入站
- 配置 Cloudflare DNS、SSL、Origin Rules
- 生成 `yx-auto.pages.dev` 订阅链接
- 检测上次配置并支持一键卸载回滚

## 前置条件

- **模式 1（安装）**：目标 VPS **必须已安装并可正常运行 3x-ui 面板**
- **模式 4（全新安装）**：**仅**在未安装 3x-ui 的裸机上可用，脚本会自动安装面板后再部署节点
- **旧版默认**：本地存在 `/etc/x-ui/x-ui.db`，脚本直写数据库并 `systemctl restart x-ui`
- **新版可选**：面板 API 可访问，使用用户名密码或 API Token

> 若尚未安装 3x-ui，请使用 **模式 4**；若已安装，使用 **模式 1** 即可。

## 运行环境

- Python 3（无需安装第三方依赖）
- 已安装并可用的 `3x-ui`（服务名通常为 `x-ui`）
- 脚本运行用户具备 root 权限（或可用 `sudo`，用于写入状态文件）
- Cloudflare 账号邮箱 + Global API Key
- 3x-ui 面板登录凭据或 API Token

## 文件说明

- 脚本：`xui_cf_deployer.py`
- 状态记录：`/etc/x-ui/cf_auto_state.json`
- 订阅快照：`cf_auto_last_links.txt`（保存在脚本运行目录）
- 面板访问信息：`/etc/x-ui/cf_panel_access.json`（JSON，权限 600）
- 面板访问快照：`cf_panel_last_access.txt`（保存在脚本运行目录，便于二次查看）
- Cloudflare 凭据：`/etc/x-ui/cf_account.json`（权限 600，安装/卸载时自动复用）

## x-ui 写入方式（自动检测）

| 条件 | 写入方式 |
|------|----------|
| 检测到 **API Token**（`x-ui setting -getApiToken` 或 `XUI_API_TOKEN`） | **API** |
| 无 Token 且存在 `/etc/x-ui/x-ui.db` | **数据库直写** |

强制指定：`XUI_BACKEND=db` 或 `XUI_BACKEND=api`

**3x-ui v3.0+ 数据库直写**：若检测到 `clients` / `client_inbounds` 表，脚本会同步写入客户端记录与绑定关系；启动时也会自动修复缺失绑定的历史入站。

### x-ui 命令菜单汉化（可选）

启动后会询问：`是否汉化 x-ui 命令菜单? (y/N)`（仅汉化 `x-ui` 命令行菜单，不影响 Web 面板）

- 自动备份英文脚本为 `x-ui.en.bak`
- `XUI_LOCALIZE_MENU=1` 自动汉化，`0` 跳过

## 运行命令

```bash
command -v python3 >/dev/null 2>&1 || (sudo apt update && sudo apt install -y python3)
curl -fsSL -o xui_cf_deployer.py https://raw.githubusercontent.com/byJoey/xui-cf-deployer/main/xui_cf_deployer.py
chmod +x xui_cf_deployer.py
sudo python3 xui_cf_deployer.py
```

或：

```bash
command -v python3 >/dev/null 2>&1 || (sudo apt update && sudo apt install -y python3)
curl -fsSL -o xui_cf_deployer.py https://raw.githubusercontent.com/byJoey/xui-cf-deployer/main/xui_cf_deployer.py
chmod +x xui_cf_deployer.py
sudo ./xui_cf_deployer.py
```

## 交互流程

脚本启动后会用**方向键菜单**选择模式（菜单项随环境动态显示）：

- **↑ ↓ ← →** 移动光标（也支持 `hjkl`）
- **回车** 确认选择
- 裸机默认高亮「全新安装」；已安装 x-ui 时默认高亮「安装节点」

非交互环境（管道/脚本）可设置 `CFD_PLAIN_MENU=1` 回退为序号输入。

可用模式：

- 安装节点（需已安装 3x-ui）
- 卸载
- 查看上次订阅
- 全新安装（**仅未安装 x-ui 时显示**；自动安装 3x-ui + 部署节点，并保存面板 URL/密码）
- 查看面板访问信息（**仅本脚本安装过 x-ui 时显示**）
- 查看 x-ui 管理命令（**已安装 x-ui 时显示**；提示后续可输入 `x-ui` 管理面板）

脚本首次以 root 运行时会自动注册快捷命令 **`cfd`**，后续输入 `cfd` 即可再次打开本部署器（安装路径：`/usr/local/lib/cf-deployer/xui_cf_deployer.py`）。

### 全新安装模式（模式 4）

**仅适用于未安装 3x-ui 的新 VPS**。若检测到已有 x-ui，会提示改用模式 1。

流程如下：

1. 自动执行官方 `install.sh`（SQLite / 随机端口 / 跳过 SSL）
2. 采集并保存面板登录信息（标记 `installed_by_script`）到 `cf_panel_last_access.txt` 与 `/etc/x-ui/cf_panel_access.json`
3. 继续执行与模式 1 相同的域名 + Cloudflare + 节点部署流程

### 安装模式（模式 1）

按提示输入：

1. 绑定域名（如 `node.example.com`）
2. Cloudflare 邮箱
3. Cloudflare Global API Key（隐藏输入）
4. 创建协议（`1=vless,2=trojan,3=vmess`，逗号分隔，回车=全部）

脚本会自动检测 x-ui 版本与环境，选择 **数据库直写** 或 **API** 写入方式（无需手动选择）。
- 配置 CF DNS（A 记录 + 代理）
- 设置 CF SSL 为 `flexible`
- 下发/合并 Origin Rules（路径转发到对应端口）
- 输出对应协议订阅链接
- 将订阅结果保存到运行目录下的 `cf_auto_last_links.txt`

### 卸载模式

脚本会读取上次安装状态并回滚：

- 删除上次创建的 x-ui 入站配置
- 恢复 Cloudflare Origin Rules 到安装前状态
- 恢复 Cloudflare SSL 到安装前值
- 恢复/删除该子域名 DNS 记录
- 删除本地状态文件

### 查看订阅模式（模式 3）

无需重装即可回看上次订阅：

- 优先读取运行目录下的 `cf_auto_last_links.txt`
- 若快照不存在，自动尝试旧版兼容重建：
  - 先用旧状态文件中的 `domain/uuid/routes` 重新拼接
  - 再兜底通过 3x-ui API 列出现有入站并反推一套节点

### 查看面板模式（模式 5）

**仅在本脚本通过模式 4 安装过 3x-ui 时可用**。手动安装的面板不会显示此选项，也无法查看。

- 优先读取运行目录下的 `cf_panel_last_access.txt`
- 记录保存在 `/etc/x-ui/cf_panel_access.json`（含 `installed_by_script: true` 标记）

### 面板管理命令（模式 6）

**已安装 3x-ui 时可用**，列出常用 `x-ui` 子命令，并提示：

- 输入 **`x-ui`** 进入 3x-ui 交互管理菜单
- 输入 **`cfd`** 再次打开本 CF 部署脚本

## 订阅链接参数

脚本输出的链接参数基线为：

- `epd=yes`
- `epi=yes`
- `egi=no`
- `dkby=yes`

并显式带三协议开关：

- 当前协议：`yes`
- 未启用协议：`no`

同时附带 URL Encode 后的 `path`。

## 常见问题

- 提示 Zone 匹配失败：检查输入的绑定域名是否在该 Cloudflare 账号下
- 提示 3x-ui API 失败：检查面板地址、WebBasePath、用户名密码或 API Token
- 提示 HTTPS 证书错误：选择跳过证书校验，或改用 `http://127.0.0.1:端口`
- 提示权限不足：使用 `sudo` 运行脚本
- 已存在上次配置无法安装：先用卸载模式清理后再重新安装
- Origin Rules 额度已满：脚本会列出当前规则序号，选择删除后可继续安装
- Cloudflare 凭据保存在 `/etc/x-ui/cf_account.json`，下次默认直接复用（输入 `n` 可重新填写）
