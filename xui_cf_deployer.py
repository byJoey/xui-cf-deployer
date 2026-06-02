#!/usr/bin/env python3
import http.cookiejar
import ipaddress
import json
import os
import random
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import uuid
from getpass import getpass
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib import error, parse, request
from urllib.request import HTTPCookieProcessor, HTTPSHandler, build_opener

try:
    import termios
    import tty

    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False


DB_PATH = "/etc/x-ui/x-ui.db"
STATE_PATH = "/etc/x-ui/cf_auto_state.json"
CF_ACCOUNT_PATH = "/etc/x-ui/cf_account.json"
PANEL_INFO_PATH = "/etc/x-ui/cf_panel_access.json"
LAST_LINKS_PATH = os.path.join(os.getcwd(), "cf_auto_last_links.txt")
PANEL_INFO_SNAPSHOT = os.path.join(os.getcwd(), "cf_panel_last_access.txt")
CFD_BIN = "/usr/local/bin/cfd"
DEPLOYER_INSTALL_PATH = "/usr/local/lib/cf-deployer/xui_cf_deployer.py"
XUI_INSTALL_URL = "https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh"
XUI_INSTALL_STDIN = "\nn\n4\n\n"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_PANEL_URL = "http://127.0.0.1:2053"
PORT_MIN = 10000
PORT_MAX = 60000
PROTOCOL_ORDER = ["vless", "trojan", "vmess"]
PROTOCOL_SUFFIX = {"vless": "vl", "trojan": "tr", "vmess": "vm"}
PROTOCOL_LABEL = {"vless": "VLESS", "trojan": "TROJAN", "vmess": "VMESS"}
PROTOCOL_QUERY_FLAG = {"vless": "ev", "trojan": "et", "vmess": "evm"}
MANAGED_RULE_PREFIX = "3x-ui-auto "
MANAGED_TAG_RE = re.compile(r"^([0-9a-f]{8})-(vless|trojan|vmess)$", re.I)
PANEL_API_PREFIX = "panel/api"
BACKEND_DB = "db"
BACKEND_API = "api"
API_MIN_VERSION = (2, 0, 0)
XUI_BINARY_CANDIDATES = ("/usr/local/x-ui/x-ui", "/usr/bin/x-ui")
XUI_CLI_SCRIPT_CANDIDATES = ("/usr/bin/x-ui", "/usr/local/x-ui/x-ui.sh")
XUI_MENU_ZH_MARKER = "# cf-deployer-xui-menu-zh"
XUI_MENU_REPLACEMENTS: List[tuple[str, str]] = [
    ('echo "The OS release is: $release"', 'echo "系统发行版: $release"'),
    ("3X-UI Panel Management Script", "3X-UI 面板管理脚本"),
    ("0.${plain} Exit Script", "0.${plain} 退出脚本"),
    ("1.${plain} Install", "1.${plain} 安装"),
    ("2.${plain} Update", "2.${plain} 更新"),
    ("3.${plain} Update Menu", "3.${plain} 更新菜单"),
    ("4.${plain} Legacy Version", "4.${plain} 旧版安装"),
    ("5.${plain} Uninstall", "5.${plain} 卸载"),
    ("6.${plain} Reset Username & Password", "6.${plain} 重置用户名和密码"),
    ("7.${plain} Reset Web Base Path", "7.${plain} 重置面板访问路径"),
    ("8.${plain} Reset Settings", "8.${plain} 重置面板设置"),
    ("9.${plain} Change Port", "9.${plain} 修改面板端口"),
    ("10.${plain} View Current Settings", "10.${plain} 查看当前设置"),
    ("11.${plain} Start", "11.${plain} 启动"),
    ("12.${plain} Stop", "12.${plain} 停止"),
    ("13.${plain} Restart", "13.${plain} 重启"),
    ("14.${plain} Restart Xray", "14.${plain} 重启 Xray"),
    ("15.${plain} Check Status", "15.${plain} 查看状态"),
    ("16.${plain} Logs Management", "16.${plain} 日志管理"),
    ("17.${plain} Enable Autostart", "17.${plain} 启用开机自启"),
    ("18.${plain} Disable Autostart", "18.${plain} 禁用开机自启"),
    ("19.${plain} SSL Certificate Management", "19.${plain} SSL 证书管理"),
    ("20.${plain} Cloudflare SSL Certificate", "20.${plain} Cloudflare SSL 证书"),
    ("21.${plain} IP Limit Management", "21.${plain} IP 限制管理"),
    ("22.${plain} Firewall Management", "22.${plain} 防火墙管理"),
    ("23.${plain} SSH Port Forwarding Management", "23.${plain} SSH 端口转发管理"),
    ("24.${plain} Enable BBR", "24.${plain} 启用 BBR"),
    ("25.${plain} Update Geo Files", "25.${plain} 更新 Geo 文件"),
    ("26.${plain} Speedtest by Ookla", "26.${plain} Ookla 测速"),
    ("27.${plain} PostgreSQL Management", "27.${plain} PostgreSQL 管理"),
    ('read -rp "Please enter your selection [0-27]: " num', 'read -rp "请输入选项 [0-27]: " num'),
    ('LOGE "Please enter the correct number [0-27]"', 'LOGE "请输入正确选项 [0-27]"'),
    ('echo -e "Panel state: ${green}Running${plain}"', 'echo -e "面板状态: ${green}运行中${plain}"'),
    ('echo -e "Panel state: ${yellow}Not Running${plain}"', 'echo -e "面板状态: ${yellow}未运行${plain}"'),
    ('echo -e "Panel state: ${red}Not Installed${plain}"', 'echo -e "面板状态: ${red}未安装${plain}"'),
    ('echo -e "Start automatically: ${green}Yes${plain}"', 'echo -e "开机自启: ${green}是${plain}"'),
    ('echo -e "Start automatically: ${red}No${plain}"', 'echo -e "开机自启: ${red}否${plain}"'),
    ('echo -e "xray state: ${green}Running${plain}"', 'echo -e "xray 状态: ${green}运行中${plain}"'),
    ('echo -e "xray state: ${red}Not Running${plain}"', 'echo -e "xray 状态: ${red}未运行${plain}"'),
]


def exit_error(message: str) -> None:
    print(message)
    sys.exit(1)


def call_json_api(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
    exit_on_http_error: bool = True,
    opener: Optional[Any] = None,
):
    payload = None
    if data is not None:
        payload = json.dumps(data).encode("utf-8")

    req = request.Request(url=url, data=payload, headers=headers or {}, method=method)

    open_fn = opener.open if opener is not None else request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if exit_on_http_error:
            print(body)
            sys.exit(1)
        if body:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"success": False, "errors": [{"message": body}]}
        return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}
    except error.URLError as e:
        exit_error(f"网络错误: {e}")

    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def call_cf_api(
    method: str,
    endpoint: str,
    headers: Dict[str, str],
    data: Optional[Dict[str, Any]] = None,
):
    result = call_json_api(method=method, url=f"{CF_API_BASE}{endpoint}", headers=headers, data=data)
    if not result.get("success", False):
        errors = result.get("errors") or [{"message": "Cloudflare API 未知错误"}]
        print(json.dumps(errors, ensure_ascii=False))
        sys.exit(1)
    return result.get("result")


def call_cf_api_result(
    method: str,
    endpoint: str,
    headers: Dict[str, str],
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return call_json_api(
        method=method,
        url=f"{CF_API_BASE}{endpoint}",
        headers=headers,
        data=data,
        exit_on_http_error=False,
    )


def build_cf_headers(email: str, api_key: str) -> Dict[str, str]:
    return {
        "X-Auth-Email": email,
        "X-Auth-Key": api_key,
        "Content-Type": "application/json",
    }


def load_cf_account() -> Optional[Dict[str, str]]:
    if not os.path.isfile(CF_ACCOUNT_PATH):
        return None
    try:
        with open(CF_ACCOUNT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    email = str(data.get("email", "")).strip()
    api_key = str(data.get("api_key", "")).strip()
    if not email or not api_key:
        return None
    return {"email": email, "api_key": api_key}


def save_cf_account(email: str, api_key: str) -> None:
    try:
        os.makedirs(os.path.dirname(CF_ACCOUNT_PATH), exist_ok=True)
        with open(CF_ACCOUNT_PATH, "w", encoding="utf-8") as f:
            json.dump({"email": email, "api_key": api_key}, f, ensure_ascii=False, indent=2)
        os.chmod(CF_ACCOUNT_PATH, 0o600)
    except OSError as e:
        exit_error(f"保存 Cloudflare 凭据失败: {e}")


def prompt_cf_credentials() -> Tuple[str, str]:
    env_email = os.environ.get("CF_EMAIL", "").strip()
    env_key = (
        os.environ.get("CF_API_KEY", "").strip()
        or os.environ.get("CF_GLOBAL_API_KEY", "").strip()
    )
    if env_email and env_key:
        save_cf_account(env_email, env_key)
        return env_email, env_key

    saved = load_cf_account()
    if saved:
        print(f"Cloudflare 账号: {saved['email']} (已保存本地)")
        answer = input("使用已保存凭据? (Y/n): ").strip().lower()
        if answer in ("", "y", "yes"):
            return saved["email"], saved["api_key"]

    email = input("Cloudflare 邮箱: ").strip()
    api_key = getpass("Cloudflare Global API Key: ").strip()
    if not email or not api_key:
        exit_error("Cloudflare 邮箱和 API Key 不能为空")
    save_cf_account(email, api_key)
    print(f"Cloudflare 凭据已保存到 {CF_ACCOUNT_PATH}")
    return email, api_key


class XuiPanelClient:
    """3x-ui 面板 REST API 客户端（支持 Session 登录或 Bearer Token）。"""

    def __init__(self, base_url: str, token: Optional[str] = None, insecure_tls: bool = False):
        self.base_url = base_url.rstrip("/")
        self.token = (token or "").strip() or None
        self.csrf_token: Optional[str] = None
        self.insecure_tls = insecure_tls
        jar = http.cookiejar.CookieJar()
        handlers: List[Any] = [HTTPCookieProcessor(jar)]
        if insecure_tls:
            handlers.append(HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = build_opener(*handlers)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if extra:
            headers.update(extra)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return headers

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        *,
        require_success: bool = True,
        auth_required: bool = True,
    ) -> Dict[str, Any]:
        if auth_required and not self.token and not self.csrf_token:
            exit_error("未登录 3x-ui 面板，请先调用 login() 或提供 API Token")

        result = call_json_api(
            method=method,
            url=self._url(path),
            headers=self._headers(),
            data=data,
            opener=self.opener,
        )
        if require_success and not result.get("success", False):
            msg = result.get("msg") or result.get("message") or json.dumps(result, ensure_ascii=False)
            exit_error(f"3x-ui API 失败: {msg}")
        return result

    def fetch_csrf_token(self) -> str:
        result = self._request("GET", "csrf-token", require_success=True, auth_required=False)
        token = result.get("obj")
        if not isinstance(token, str) or not token:
            exit_error("获取 CSRF Token 失败")
        self.csrf_token = token
        return token

    def login(self, username: str, password: str, two_factor_code: str = "") -> None:
        self.fetch_csrf_token()
        payload: Dict[str, Any] = {"username": username, "password": password}
        if two_factor_code.strip():
            payload["twoFactorCode"] = two_factor_code.strip()
        self._request("POST", "login", data=payload, auth_required=False)
        if not self.csrf_token:
            exit_error("3x-ui 登录失败：未获得 CSRF Token")

    def list_inbounds(self) -> List[Dict[str, Any]]:
        result = self._request("GET", f"{PANEL_API_PREFIX}/inbounds/list")
        obj = result.get("obj")
        if isinstance(obj, list):
            return obj
        return []

    def add_inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._request("POST", f"{PANEL_API_PREFIX}/inbounds/add", data=payload)
        obj = result.get("obj")
        if isinstance(obj, dict):
            return obj
        return {}

    def delete_inbound(self, inbound_id: int) -> None:
        self._request("POST", f"{PANEL_API_PREFIX}/inbounds/del/{inbound_id}")

    def restart_xray(self) -> None:
        self._request("POST", f"{PANEL_API_PREFIX}/server/restartXrayService")


def parse_version(version_text: str) -> Tuple[int, ...]:
    parts: List[int] = []
    for token in re.split(r"[^0-9]+", version_text.strip()):
        if token.isdigit():
            parts.append(int(token))
    return tuple(parts) if parts else (0,)


def version_at_least(version_tuple: Tuple[int, ...], minimum: Tuple[int, ...]) -> bool:
    width = max(len(version_tuple), len(minimum))
    left = version_tuple + (0,) * (width - len(version_tuple))
    right = minimum + (0,) * (width - len(minimum))
    return left >= right


def find_xui_binary() -> Optional[str]:
    candidates: List[str] = []
    which = shutil.which("x-ui")
    if which:
        candidates.append(which)
    candidates.extend(XUI_BINARY_CANDIDATES)

    seen: Set[str] = set()
    for path in candidates:
        if not path or path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            result = subprocess.run(
                [path, "-v"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        version = (result.stdout or result.stderr or "").strip().splitlines()
        if version and re.match(r"^\d", version[0]):
            return path
    return None


def read_xui_version(binary: Optional[str]) -> Optional[str]:
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "-v"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr or "").strip().splitlines()
    if not text:
        return None
    return text[0]


def read_setting_from_db(key: str) -> Optional[str]:
    if not os.path.isfile(DB_PATH):
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cur.fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] is None:
        return None
    return str(row[0])


def detect_panel_url() -> Tuple[str, bool]:
    env_url = os.environ.get("XUI_PANEL_URL", "").strip()
    if env_url:
        return env_url.rstrip("/"), env_url.lower().startswith("https://")

    port = read_setting_from_db("webPort") or "2053"
    base_path = read_setting_from_db("webBasePath") or "/"
    cert = (read_setting_from_db("webCertFile") or "").strip()
    key = (read_setting_from_db("webKeyFile") or "").strip()
    https = bool(cert and key)
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"
    base_path = base_path.rstrip("/") or ""
    return f"{'https' if https else 'http'}://127.0.0.1:{port}{base_path}", https


def read_api_token_from_cli(binary: Optional[str]) -> Optional[str]:
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "setting", "-getApiToken"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    for line in output.splitlines():
        if line.startswith("apiToken:"):
            token = line.split(":", 1)[1].strip()
            return token or None
    return None


def is_xui_installed() -> bool:
    return os.path.isfile(DB_PATH) and find_xui_binary() is not None


def parse_credentials_from_install_output(output: str) -> Tuple[Optional[str], Optional[str]]:
    username: Optional[str] = None
    password: Optional[str] = None
    for line in output.splitlines():
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        user_match = re.search(r"Username:\s*(\S+)", clean, re.I)
        if user_match:
            username = user_match.group(1)
        pass_match = re.search(r"Password:\s*(\S+)", clean, re.I)
        if pass_match:
            password = pass_match.group(1)
    return username, password


def is_password_hash(value: str) -> bool:
    return value.startswith(("$2a$", "$2b$", "$2y$"))


def run_xui_install_script() -> Tuple[str, str]:
    print("正在安装 3x-ui（SQLite / 随机端口 / 跳过 SSL）...")
    try:
        proc = subprocess.run(
            ["bash", "-c", f"curl -fsSL {shlex_quote(XUI_INSTALL_URL)} | bash"],
            input=XUI_INSTALL_STDIN,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        exit_error("3x-ui 安装超时")

    install_output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if proc.returncode != 0:
        exit_error(f"3x-ui 安装失败 (exit {proc.returncode}):\n{install_output.strip()[-2000:]}")

    for _ in range(45):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "x-ui"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except OSError:
            break
        if result.stdout.strip() == "active":
            print("3x-ui 安装完成，服务已启动")
            username, password = parse_credentials_from_install_output(install_output)
            if not username or not password:
                exit_error("3x-ui 安装成功但未解析到登录凭据，请检查安装输出")
            return username, password
        time.sleep(2)
    exit_error("3x-ui 安装完成但服务未启动，请检查 journalctl -u x-ui")


def shlex_quote(value: str) -> str:
    if re.match(r"^[A-Za-z0-9_/@.+-]+$", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def read_panel_user_from_db() -> Tuple[str, str]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT username, password FROM users ORDER BY id LIMIT 1")
            row = cur.fetchone()
    except sqlite3.Error as e:
        exit_error(str(e))
    if not row or not row[0]:
        exit_error("未找到面板登录账号，请先安装 3x-ui")
    return str(row[0]), str(row[1] or "")


def collect_panel_access_info(
    *,
    installed_by_script: bool = False,
    plain_username: Optional[str] = None,
    plain_password: Optional[str] = None,
) -> Dict[str, Any]:
    binary = find_xui_binary()
    db_username, db_password = read_panel_user_from_db()
    username = plain_username or db_username
    password = plain_password or db_password
    if is_password_hash(password):
        exit_error("无法读取面板明文密码，请重新执行模式 4 全新安装")
    port_text = read_setting_from_db("webPort") or "2053"
    base_path = read_setting_from_db("webBasePath") or "/"
    listen_ip = (read_setting_from_db("listenIP") or "").strip()
    cert = (read_setting_from_db("webCertFile") or "").strip()
    key = (read_setting_from_db("webKeyFile") or "").strip()
    https = bool(cert and key)
    scheme = "https" if https else "http"
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"
    base_path = base_path.rstrip("/") or ""
    path_suffix = base_path if base_path else ""
    try:
        port = int(port_text)
    except ValueError:
        port = 2053
    local_host = "127.0.0.1"
    if listen_ip in ("127.0.0.1", "::1", "localhost"):
        local_host = "127.0.0.1"
    local_url = f"{scheme}://{local_host}:{port}{path_suffix}"
    public_url = ""
    if listen_ip not in ("127.0.0.1", "::1", "localhost"):
        try:
            public_ip = get_public_ipv4()
            public_url = f"{scheme}://{public_ip}:{port}{path_suffix}"
        except SystemExit:
            public_url = ""
    api_token = read_api_token_from_cli(binary) or os.environ.get("XUI_API_TOKEN", "").strip()
    info: Dict[str, Any] = {
        "username": username,
        "password": password,
        "port": port,
        "web_base_path": base_path or "/",
        "listen_ip": listen_ip,
        "access_url_local": local_url,
        "access_url_public": public_url,
        "api_token": api_token,
        "installed_at": int(time.time()),
    }
    if installed_by_script:
        info["installed_by_script"] = True
    return info


def save_panel_access_info(info: Dict[str, Any]) -> None:
    try:
        with open(PANEL_INFO_PATH, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        os.chmod(PANEL_INFO_PATH, 0o600)
    except OSError as e:
        exit_error(f"保存面板访问信息失败: {e}")

    lines = [
        "3x-ui 面板访问信息",
        f"用户名: {info.get('username', '')}",
        f"密码: {info.get('password', '')}",
        f"本机地址: {info.get('access_url_local', '')}",
    ]
    public_url = str(info.get("access_url_public") or "").strip()
    if public_url:
        lines.append(f"公网地址: {public_url}")
    api_token = str(info.get("api_token") or "").strip()
    if api_token:
        lines.append(f"API Token: {api_token}")
    lines.append("")
    try:
        with open(PANEL_INFO_SNAPSHOT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        exit_error(f"保存面板快照失败: {e}")


def load_panel_access_record() -> Optional[Dict[str, Any]]:
    if not os.path.isfile(PANEL_INFO_PATH):
        return None
    try:
        with open(PANEL_INFO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def has_script_installed_panel() -> bool:
    info = load_panel_access_record()
    return bool(info and info.get("installed_by_script"))


def load_panel_access_info() -> Optional[Dict[str, Any]]:
    return load_panel_access_record()


def print_panel_access_info() -> None:
    if not has_script_installed_panel():
        exit_error("当前面板非本脚本安装，无法查看面板访问信息")

    if os.path.isfile(PANEL_INFO_SNAPSHOT):
        try:
            with open(PANEL_INFO_SNAPSHOT, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except OSError as e:
            exit_error(f"读取面板访问信息失败: {e}")
        if content:
            print(content)
            return

    info = load_panel_access_info()
    if not info:
        exit_error("未找到面板访问信息，请先使用模式 4 全新安装")
    exit_error("未找到面板访问快照，请重新执行模式 4 全新安装")


def ensure_xui_for_fresh_setup() -> None:
    if is_xui_installed():
        exit_error("检测到已安装 3x-ui，请使用模式 1 安装节点")
    username, password = run_xui_install_script()
    info = collect_panel_access_info(
        installed_by_script=True,
        plain_username=username,
        plain_password=password,
    )
    save_panel_access_info(info)
    print(f"面板信息已保存到 {PANEL_INFO_SNAPSHOT}")
    print(f"本机地址: {info['access_url_local']}")
    print(f"用户名: {info['username']}")
    print(f"密码: {info['password']}")


def ensure_cfd_command() -> bool:
    if os.geteuid() != 0:
        return False
    script_path = os.path.realpath(__file__)
    install_dir = os.path.dirname(DEPLOYER_INSTALL_PATH)
    try:
        os.makedirs(install_dir, exist_ok=True)
        need_copy = True
        if os.path.isfile(DEPLOYER_INSTALL_PATH):
            try:
                need_copy = os.path.getsize(script_path) != os.path.getsize(DEPLOYER_INSTALL_PATH)
            except OSError:
                need_copy = True
        if need_copy:
            shutil.copy2(script_path, DEPLOYER_INSTALL_PATH)
        os.chmod(DEPLOYER_INSTALL_PATH, 0o755)

        first_install = not os.path.isfile(CFD_BIN)
        cfd_script = (
            "#!/bin/bash\n"
            f"exec python3 {DEPLOYER_INSTALL_PATH} \"$@\"\n"
        )
        with open(CFD_BIN, "w", encoding="utf-8") as f:
            f.write(cfd_script)
        os.chmod(CFD_BIN, 0o755)
        if first_install:
            print(f"已注册快捷命令 cfd，后续输入 cfd 即可打开本脚本")
        return True
    except OSError:
        return False


def print_xui_management_help() -> None:
    if not is_xui_installed():
        exit_error("未安装 3x-ui，暂无管理命令")

    lines = [
        "3x-ui 管理命令",
        "",
        "后续管理面板，在终端输入：",
        "  x-ui",
        "",
        "常用命令（可直接执行，无需进入菜单）：",
        "  x-ui start              启动面板",
        "  x-ui stop               停止面板",
        "  x-ui restart            重启面板",
        "  x-ui restart-xray       重启 Xray",
        "  x-ui status             查看运行状态",
        "  x-ui settings           查看当前面板设置",
        "  x-ui enable             启用开机自启",
        "  x-ui disable            禁用开机自启",
        "  x-ui log                查看日志",
        "  x-ui update             更新 3x-ui",
        "",
        "CF 部署器快捷命令：",
        "  cfd                     再次打开本脚本",
        "",
        "进入 x-ui 交互菜单后，还可修改端口、重置密码、SSL 证书等。",
        "",
        "提示: 输入 x-ui 可随时进入 3x-ui 管理菜单",
    ]
    if shutil.which("cfd"):
        lines.append("提示: 输入 cfd 可随时调用本部署脚本")
    print("\n".join(lines))


def build_mode_menu_items() -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = [
        ("install", "安装节点"),
        ("uninstall", "卸载"),
        ("show", "查看订阅"),
    ]
    if not is_xui_installed():
        items.append(("fresh", "全新安装(含 x-ui)"))
    if has_script_installed_panel():
        items.append(("panel", "查看面板"))
    if is_xui_installed():
        items.append(("xui_manage", "面板管理命令"))
    return items


def default_mode_index(items: List[Tuple[str, str]]) -> int:
    preferred = "fresh" if not is_xui_installed() else "install"
    for i, (mode_id, _) in enumerate(items):
        if mode_id == preferred:
            return i
    return 0


def parse_mode(raw: str, items: Optional[List[Tuple[str, str]]] = None) -> str:
    menu = items or build_mode_menu_items()
    text = raw.strip().lower()
    if text == "":
        return menu[default_mode_index(menu)][0]
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(menu):
            return menu[idx][0]
    aliases = {
        "install": "install",
        "i": "install",
        "安装": "install",
        "uninstall": "uninstall",
        "u": "uninstall",
        "卸载": "uninstall",
        "show": "show",
        "view": "show",
        "v": "show",
        "查看": "show",
        "查看订阅": "show",
        "fresh": "fresh",
        "setup": "fresh",
        "全新": "fresh",
        "全新安装": "fresh",
        "安装x-ui": "fresh",
        "panel": "panel",
        "面板": "panel",
        "查看面板": "panel",
        "xui": "xui_manage",
        "manage": "xui_manage",
        "管理": "xui_manage",
        "管理命令": "xui_manage",
        "面板管理": "xui_manage",
    }
    mode_id = aliases.get(text)
    if mode_id and any(item[0] == mode_id for item in menu):
        return mode_id
    valid = " / ".join(str(i + 1) for i in range(len(menu)))
    exit_error(f"无效模式，请输入 {valid}")


def _read_nav_key() -> str:
    if not HAS_TERMIOS:
        return "enter"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                return "enter"
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                if seq == "[A":
                    return "up"
                if seq == "[B":
                    return "down"
                if seq == "[C":
                    return "right"
                if seq == "[D":
                    return "left"
                continue
            if ch in ("k", "K"):
                return "up"
            if ch in ("j", "J"):
                return "down"
            if ch in ("h", "H"):
                return "left"
            if ch in ("l", "L"):
                return "right"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _mode_menu_line_count(items: List[Tuple[str, str]]) -> int:
    # 标题 + 空行 + 选项 + 空行 + 底部提示
    return 2 + len(items) + 2


def _render_mode_menu(items: List[Tuple[str, str]], index: int, *, redraw: bool) -> None:
    if redraw:
        sys.stdout.write(f"\033[{_mode_menu_line_count(items)}A")

    lines = [
        "请选择模式 (↑↓←→ 移动, 回车确认):",
        "",
    ]
    for i, (_, label) in enumerate(items):
        if i == index:
            lines.append(f"  \033[1;36m> {label}\033[0m")
        else:
            lines.append(f"    {label}")
    lines.extend(["", "↑↓←→ 移动  回车 确认"])

    for line in lines:
        sys.stdout.write("\033[2K\r")
        sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def select_mode_plain(items: List[Tuple[str, str]]) -> str:
    default_idx = default_mode_index(items)
    print("请选择模式:")
    for i, (_, label) in enumerate(items, 1):
        marker = " (默认)" if i - 1 == default_idx else ""
        print(f"  {i}. {label}{marker}")
    raw = input(f"输入序号 (回车={items[default_idx][1]}): ")
    return parse_mode(raw, items)


def select_mode_cursor(items: List[Tuple[str, str]]) -> str:
    index = default_mode_index(items)
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    _render_mode_menu(items, index, redraw=False)
    while True:
        key = _read_nav_key()
        if key in ("up", "left"):
            index = (index - 1) % len(items)
            _render_mode_menu(items, index, redraw=True)
            continue
        if key in ("down", "right"):
            index = (index + 1) % len(items)
            _render_mode_menu(items, index, redraw=True)
            continue
        if key == "enter":
            sys.stdout.write("\033[?25h\n")
            sys.stdout.flush()
            return items[index][0]


def select_mode_interactive() -> str:
    items = build_mode_menu_items()
    if not items:
        exit_error("无可用模式")
    use_plain = (
        not sys.stdin.isatty()
        or not HAS_TERMIOS
        or os.environ.get("CFD_PLAIN_MENU", "").strip().lower() in ("1", "true", "yes", "y")
    )
    if use_plain:
        return select_mode_plain(items)
    try:
        return select_mode_cursor(items)
    except KeyboardInterrupt:
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()
        print("已取消")
        sys.exit(130)


def panel_tls_insecure(panel_url: str, panel_https: bool) -> bool:
    if not panel_https:
        return False
    if os.environ.get("XUI_TLS_INSECURE", "").strip().lower() in ("1", "true", "yes", "y"):
        return True
    host = parse.urlparse(panel_url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def probe_panel_api(panel_url: str, api_token: Optional[str], insecure_tls: bool) -> bool:
    client = XuiPanelClient(panel_url, token=api_token, insecure_tls=insecure_tls)
    csrf = call_json_api(
        "GET",
        client._url("csrf-token"),
        headers=client._headers(),
        opener=client.opener,
        exit_on_http_error=False,
        timeout=8,
    )
    if csrf.get("success") and isinstance(csrf.get("obj"), str):
        return True
    if api_token:
        listed = call_json_api(
            "GET",
            client._url(f"{PANEL_API_PREFIX}/inbounds/list"),
            headers=client._headers(),
            opener=client.opener,
            exit_on_http_error=False,
            timeout=8,
        )
        return bool(listed.get("success"))
    return False


def api_auth_available(env: Dict[str, Any]) -> bool:
    return bool((env.get("api_token") or "").strip())


def find_xui_cli_script() -> Optional[str]:
    candidates: List[str] = []
    which = shutil.which("x-ui")
    if which:
        candidates.append(which)
    candidates.extend(XUI_CLI_SCRIPT_CANDIDATES)

    seen: Set[str] = set()
    for path in candidates:
        if not path or path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                head = handle.read(4096)
        except OSError:
            continue
        if "show_menu()" in head or "Panel Management Script" in head:
            return path
    return None


def is_xui_menu_localized(script_path: str) -> bool:
    try:
        with open(script_path, "r", encoding="utf-8", errors="ignore") as handle:
            return XUI_MENU_ZH_MARKER in handle.read(8192)
    except OSError:
        return False


def apply_xui_menu_localization() -> None:
    script_path = find_xui_cli_script()
    if not script_path:
        print("未找到 x-ui 命令脚本，跳过汉化")
        return
    if is_xui_menu_localized(script_path):
        print("x-ui 命令菜单已是中文，跳过")
        return

    try:
        with open(script_path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read()
    except OSError as e:
        exit_error(f"读取 x-ui 脚本失败: {e}")

    backup_path = f"{script_path}.en.bak"
    if not os.path.exists(backup_path):
        try:
            shutil.copy2(script_path, backup_path)
        except OSError as e:
            exit_error(f"备份 x-ui 脚本失败: {e}")

    updated = content
    applied = 0
    for old, new in XUI_MENU_REPLACEMENTS:
        if old not in updated:
            continue
        updated = updated.replace(old, new)
        applied += 1

    if applied == 0:
        exit_error("x-ui 汉化失败：未匹配到菜单文本，可能脚本版本不兼容")

    if updated.startswith("#!"):
        lines = updated.splitlines(keepends=True)
        if not any(XUI_MENU_ZH_MARKER in line for line in lines[:5]):
            lines.insert(1, f"{XUI_MENU_ZH_MARKER}\n")
        updated = "".join(lines)
    else:
        updated = f"{XUI_MENU_ZH_MARKER}\n{updated}"

    try:
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write(updated)
    except OSError as e:
        exit_error(f"写入 x-ui 汉化脚本失败: {e}")

    print(f"x-ui 命令菜单已汉化: {script_path}")
    print(f"英文备份: {backup_path}")


def prompt_maybe_localize_xui_menu() -> None:
    env_flag = os.environ.get("XUI_LOCALIZE_MENU", "").strip().lower()
    if env_flag in ("0", "no", "n", "false"):
        return
    if env_flag in ("1", "yes", "y", "true"):
        apply_xui_menu_localization()
        return

    script_path = find_xui_cli_script()
    if not script_path or is_xui_menu_localized(script_path):
        return

    answer = input("是否汉化 x-ui 命令菜单? (y/N): ").strip().lower()
    if answer in ("y", "yes"):
        apply_xui_menu_localization()


def detect_xui_environment() -> Dict[str, Any]:
    binary = find_xui_binary()
    version = read_xui_version(binary)
    version_tuple = parse_version(version) if version else (0,)
    db_available = os.path.isfile(DB_PATH)
    panel_url, panel_https = detect_panel_url()
    insecure_tls = panel_tls_insecure(panel_url, panel_https)
    api_token = os.environ.get("XUI_API_TOKEN", "").strip() or read_api_token_from_cli(binary)

    api_capable = version_tuple == (0,) or version_at_least(version_tuple, API_MIN_VERSION)
    api_reachable = False
    if api_capable:
        api_reachable = probe_panel_api(panel_url, api_token, insecure_tls)

    return {
        "binary": binary,
        "version": version,
        "version_tuple": version_tuple,
        "db_available": db_available,
        "panel_url": panel_url,
        "panel_https": panel_https,
        "insecure_tls": insecure_tls,
        "api_token": api_token,
        "api_capable": api_capable,
        "api_reachable": api_reachable,
    }


def backend_label(backend: str) -> str:
    return "API" if backend == BACKEND_API else "数据库直写"


def auto_select_backend(
    env: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    explicit = os.environ.get("XUI_BACKEND", "").strip().lower()
    if explicit == BACKEND_DB:
        return BACKEND_DB, "环境变量 XUI_BACKEND=db"
    if explicit == BACKEND_API:
        if not api_auth_available(env):
            exit_error("已强制 API 模式，但未检测到 API Token")
        return BACKEND_API, "环境变量 XUI_BACKEND=api"

    from_state = backend_from_state(state)
    if from_state:
        return from_state, "状态文件记录"

    if api_auth_available(env):
        return BACKEND_API, "检测到 API Token，使用 API"

    if env.get("db_available"):
        return BACKEND_DB, "未检测到 API Token，使用数据库直写"

    exit_error("未检测到 API Token，且不存在本地数据库")


def resolve_backend(
    state: Optional[Dict[str, Any]] = None,
    env: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any], str]:
    runtime = env or detect_xui_environment()
    backend, reason = auto_select_backend(runtime, state)
    return backend, runtime, reason


def setup_panel_client(env: Dict[str, Any], *, interactive: bool = True) -> XuiPanelClient:
    panel_url = os.environ.get("XUI_PANEL_URL", "").strip() or str(env["panel_url"])
    insecure = bool(env.get("insecure_tls"))
    token = os.environ.get("XUI_API_TOKEN", "").strip() or str(env.get("api_token") or "").strip()
    if not token:
        exit_error("API 模式需要 API Token（可通过 x-ui setting -getApiToken 获取）")
    return XuiPanelClient(panel_url, token=token, insecure_tls=insecure)


def backend_from_state(state: Optional[Dict[str, Any]]) -> Optional[str]:
    if not state:
        return None
    backend = str(state.get("backend", "")).strip().lower()
    if backend in (BACKEND_DB, BACKEND_API):
        return backend
    version = state.get("version")
    if version == 2:
        return BACKEND_API
    if version == 1:
        return BACKEND_DB
    return None


def prompt_panel_client() -> XuiPanelClient:
    panel_url = (
        os.environ.get("XUI_PANEL_URL", "").strip()
        or input(f"3x-ui 面板地址(回车={DEFAULT_PANEL_URL}): ").strip()
        or DEFAULT_PANEL_URL
    )
    insecure = panel_url.lower().startswith("https://")
    if insecure:
        answer = input("面板为 HTTPS 且可能自签名，跳过证书校验? (Y/n): ").strip().lower()
        insecure = answer in ("", "y", "yes")

    token = os.environ.get("XUI_API_TOKEN", "").strip()
    if not token:
        auth_mode = input("3x-ui 认证(1=用户名密码,2=API Token，回车=1): ").strip() or "1"
        if auth_mode in ("2", "token", "t"):
            token = getpass("3x-ui API Token: ").strip()
            if not token:
                exit_error("API Token 不能为空")
            return XuiPanelClient(panel_url, token=token, insecure_tls=insecure)

    username = os.environ.get("XUI_USERNAME", "").strip()
    password = os.environ.get("XUI_PASSWORD", "").strip()
    if not username:
        username = input("3x-ui 用户名: ").strip()
    if not password:
        password = getpass("3x-ui 密码: ").strip()
    if not username or not password:
        exit_error("3x-ui 用户名和密码不能为空")

    client = XuiPanelClient(panel_url, insecure_tls=insecure)
    two_factor = os.environ.get("XUI_2FA", "").strip()
    if not two_factor and not sys.stdin.isatty():
        two_factor = ""
    elif not two_factor:
        two_factor = input("3x-ui 两步验证码(无则回车): ").strip()
    client.login(username, password, two_factor_code=two_factor)
    return client


def get_public_ipv4() -> str:
    providers = [
        "https://api.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://ifconfig.me/ip",
    ]
    for url in providers:
        try:
            with request.urlopen(url, timeout=8) as resp:
                ip_text = resp.read().decode("utf-8").strip()
            ipaddress.IPv4Address(ip_text)
            return ip_text
        except error.HTTPError as e:
            print(e.read().decode("utf-8", errors="ignore"))
            sys.exit(1)
        except Exception:
            continue
    exit_error("获取公网 IPv4 失败")


def find_best_zone(domain: str, zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    input_domain = domain.strip(".").lower()
    best_match = None
    for zone in zones:
        zone_name = str(zone.get("name", "")).strip(".").lower()
        if not zone_name:
            continue
        if input_domain == zone_name or input_domain.endswith(f".{zone_name}"):
            if best_match is None or len(zone_name) > len(best_match["name"]):
                best_match = zone
    return best_match


def fetch_all_zones(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    page = 1
    zones: List[Dict[str, Any]] = []
    while True:
        endpoint = f"/zones?per_page=100&page={page}"
        result = call_json_api("GET", f"{CF_API_BASE}{endpoint}", headers=headers)
        if not result.get("success", False):
            errors = result.get("errors") or [{"message": "获取 Zone 列表失败"}]
            print(json.dumps(errors, ensure_ascii=False))
            sys.exit(1)
        zones.extend(result.get("result", []))
        info = result.get("result_info") or {}
        total_pages = int(info.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
    return zones


def get_dns_record(zone_id: str, domain: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    q = parse.urlencode({"type": "A", "name": domain})
    existing = call_cf_api("GET", f"/zones/{zone_id}/dns_records?{q}", headers=headers)
    if existing:
        return existing[0]
    return None


def upsert_dns_record(zone_id: str, domain: str, ip: str, headers: Dict[str, str]) -> str:
    existing = get_dns_record(zone_id, domain, headers)
    payload = {
        "type": "A",
        "name": domain,
        "content": ip,
        "proxied": True,
        "ttl": 1,
    }
    if existing:
        record_id = str(existing["id"])
        call_cf_api("PUT", f"/zones/{zone_id}/dns_records/{record_id}", headers=headers, data=payload)
        return record_id
    created = call_cf_api("POST", f"/zones/{zone_id}/dns_records", headers=headers, data=payload)
    return str(created["id"])


def get_ssl_mode(zone_id: str, headers: Dict[str, str]) -> str:
    result = call_cf_api("GET", f"/zones/{zone_id}/settings/ssl", headers=headers)
    value = str(result.get("value", "")).strip()
    if not value:
        exit_error("读取 Cloudflare SSL 模式失败")
    return value


def set_ssl_mode(zone_id: str, headers: Dict[str, str], mode: str) -> None:
    call_cf_api(
        "PATCH",
        f"/zones/{zone_id}/settings/ssl",
        headers=headers,
        data={"value": mode},
    )


def build_origin_rules(domain: str, routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rules = []
    host = domain.strip().lower()
    for route in routes:
        rules.append(
            {
                "description": f"{MANAGED_RULE_PREFIX}{route['protocol']} {route['path']}",
                "enabled": True,
                "expression": (
                    f'(http.host eq "{host}" and '
                    f'http.request.uri.path eq "{route["path"]}")'
                ),
                "action": "route",
                "action_parameters": {"origin": {"port": route["port"]}},
            }
        )
    return rules


def managed_origin_rule_for_domain(rule: Dict[str, Any], domain: str) -> bool:
    if not str(rule.get("description", "")).startswith(MANAGED_RULE_PREFIX):
        return False
    host = domain.strip().lower()
    expr = str(rule.get("expression", "")).lower()
    return f'http.host eq "{host}"' in expr


def strip_managed_origin_rules(
    rules: List[Dict[str, Any]], domain: Optional[str] = None
) -> List[Dict[str, Any]]:
    host = domain.strip().lower() if domain else None
    filtered: List[Dict[str, Any]] = []
    for rule in rules:
        description = str(rule.get("description", ""))
        if not description.startswith(MANAGED_RULE_PREFIX):
            filtered.append(rule)
            continue
        if host and managed_origin_rule_for_domain(rule, host):
            continue
        if host is None:
            continue
        filtered.append(rule)
    return filtered


def get_origin_rules(zone_id: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    result = call_json_api(
        "GET",
        f"{CF_API_BASE}/zones/{zone_id}/rulesets/phases/http_request_origin/entrypoint",
        headers=headers,
        exit_on_http_error=False,
    )
    if not result.get("success", False):
        return []
    ruleset = result.get("result") or {}
    rules = ruleset.get("rules")
    if isinstance(rules, list):
        return rules
    return []


def origin_rule_host(rule: Dict[str, Any]) -> str:
    expr = str(rule.get("expression", ""))
    match = re.search(r'http\.host eq "([^"]+)"', expr, re.I)
    return match.group(1) if match else "?"


def origin_rule_port(rule: Dict[str, Any]) -> str:
    origin = ((rule.get("action_parameters") or {}).get("origin") or {})
    port = origin.get("port")
    return str(port) if port is not None else "?"


def is_origin_rule_limit_error(result: Dict[str, Any]) -> bool:
    for item in result.get("errors") or []:
        message = str(item.get("message", "")).lower()
        if any(
            token in message
            for token in ("limit", "quota", "maximum", "exceeded", "too many", "规则")
        ):
            return True
        try:
            if int(item.get("code", 0)) in (10006, 20127, 20217):
                return True
        except (TypeError, ValueError):
            pass
    return False


def format_origin_rule_line(index: int, rule: Dict[str, Any]) -> str:
    description = str(rule.get("description") or "(无描述)")
    host = origin_rule_host(rule)
    port = origin_rule_port(rule)
    kind = "脚本" if description.startswith(MANAGED_RULE_PREFIX) else "其他"
    path_match = re.search(r'http\.request\.uri\.path eq "([^"]+)"', str(rule.get("expression", "")))
    path = path_match.group(1) if path_match else ""
    extra = f" path={path}" if path else ""
    return f"{index}. [{kind}] {host}{extra} -> :{port} | {description}"


def prompt_delete_origin_rules(rules: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    if not rules:
        exit_error("Origin Rules 为空，无法继续")

    print("\nCloudflare Origin Rules 已达额度上限，请删除部分规则后重试：")
    for i, rule in enumerate(rules, 1):
        print(format_origin_rule_line(i, rule))

    raw = input("\n请输入要删除的序号(逗号分隔，回车=取消): ").strip()
    if not raw:
        return None

    remove_indexes: Set[int] = set()
    for token in raw.replace(" ", "").split(","):
        if not token:
            continue
        if not token.isdigit():
            exit_error(f"无效序号: {token}")
        idx = int(token)
        if idx < 1 or idx > len(rules):
            exit_error(f"序号超出范围: {idx}")
        remove_indexes.add(idx)

    if not remove_indexes:
        return None

    kept = [rule for i, rule in enumerate(rules, 1) if i not in remove_indexes]
    print(f"将删除 {len(remove_indexes)} 条规则，保留 {len(kept)} 条")
    return kept


def put_origin_rules(zone_id: str, headers: Dict[str, str], rules: List[Dict[str, Any]]) -> None:
    payload = {"rules": rules}
    result = call_cf_api_result(
        "PUT",
        f"/zones/{zone_id}/rulesets/phases/http_request_origin/entrypoint",
        headers=headers,
        data=payload,
    )
    if result.get("success", False):
        return
    if is_origin_rule_limit_error(result):
        errors = result.get("errors") or [{"message": "Origin Rules 已达额度上限"}]
        print(json.dumps(errors, ensure_ascii=False))
        next_rules = prompt_delete_origin_rules(rules)
        if next_rules is None:
            exit_error("已取消删除 Origin Rules")
        put_origin_rules(zone_id, headers, next_rules)
        return
    errors = result.get("errors") or [{"message": "Cloudflare API 未知错误"}]
    print(json.dumps(errors, ensure_ascii=False))
    sys.exit(1)


def apply_origin_rules(
    zone_id: str, headers: Dict[str, str], domain: str, routes: List[Dict[str, Any]]
) -> None:
    existing = get_origin_rules(zone_id, headers)
    next_rules = strip_managed_origin_rules(existing, domain) + build_origin_rules(domain, routes)
    put_origin_rules(zone_id, headers, next_rules)


def client_email_for_route(short_id: str, protocol: str) -> str:
    """3x-ui 客户端 email：小写字母数字，无 @，与面板校验一致。"""
    return f"{short_id.lower()}{PROTOCOL_SUFFIX[protocol]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    )
    return cursor.fetchone() is not None


def has_v3_client_schema(conn: sqlite3.Connection) -> bool:
    return table_exists(conn, "clients") and table_exists(conn, "client_inbounds")


def inbound_client_entry(protocol: str, user_uuid: str, email: str, *, v3: bool = True) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "email": email,
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "subId": "",
        "comment": "",
        "reset": 0,
        "flow": "",
        "tgId": 0 if v3 else "",
    }
    if protocol == "vless":
        entry["id"] = user_uuid
    elif protocol == "trojan":
        entry["password"] = user_uuid
    elif protocol == "vmess":
        entry["id"] = user_uuid
        entry["alterId"] = 0
        entry["security"] = "auto"
    else:
        raise ValueError(f"不支持的协议: {protocol}")
    return entry


def ensure_vless_crypto_fields(payload: Dict[str, Any]) -> None:
    payload["decryption"] = "none"
    payload["encryption"] = "none"


def protocol_settings(protocol: str, user_uuid: str, email: str, *, v3: bool = True) -> Dict[str, Any]:
    client = inbound_client_entry(protocol, user_uuid, email, v3=v3)
    if protocol == "vless":
        return {
            "clients": [client],
            "decryption": "none",
            "encryption": "none",
            "fallbacks": [],
        }
    if protocol == "trojan":
        return {
            "clients": [client],
            "fallbacks": [],
        }
    if protocol == "vmess":
        return {
            "clients": [client],
        }
    raise ValueError(f"不支持的协议: {protocol}")


def parse_inbound_client_from_settings(protocol: str, settings_text: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(settings_text or "{}")
    except json.JSONDecodeError:
        return None
    clients = payload.get("clients")
    if not isinstance(clients, list) or not clients:
        return None
    first = clients[0]
    return first if isinstance(first, dict) else None


def client_email_from_tag(tag: str) -> Optional[str]:
    match = MANAGED_TAG_RE.match(tag or "")
    if not match:
        return None
    short_id, protocol = match.group(1), match.group(2).lower()
    return client_email_for_route(short_id, protocol)


def upsert_v3_client_record(
    cursor: sqlite3.Cursor,
    protocol: str,
    user_uuid: str,
    email: str,
    ts_ms: int,
) -> int:
    uuid_val = user_uuid if protocol in ("vless", "vmess") else ""
    password_val = user_uuid if protocol == "trojan" else ""
    security_val = "auto" if protocol == "vmess" else ""

    cursor.execute("SELECT id FROM clients WHERE email = ?", (email,))
    row = cursor.fetchone()
    if row:
        client_id = int(row[0])
        cursor.execute(
            """
            UPDATE clients
            SET uuid=?, password=?, flow='', security=?, limit_ip=0, total_gb=0,
                expiry_time=0, enable=1, tg_id=0, comment='', reset=0, updated_at=?
            WHERE id=?
            """,
            (uuid_val, password_val, security_val, ts_ms, client_id),
        )
        return client_id

    cursor.execute(
        """
        INSERT INTO clients (
            email, sub_id, uuid, password, auth, flow, security, reverse,
            limit_ip, total_gb, expiry_time, enable, tg_id, group_name, comment, reset,
            created_at, updated_at
        ) VALUES (?, '', ?, ?, '', '', ?, '', 0, 0, 0, 1, 0, '', '', 0, ?, ?)
        """,
        (email, uuid_val, password_val, security_val, ts_ms, ts_ms),
    )
    return int(cursor.lastrowid)


def link_v3_client_inbound(
    cursor: sqlite3.Cursor,
    client_id: int,
    inbound_id: int,
    ts_ms: int,
    flow: str = "",
) -> None:
    cursor.execute("DELETE FROM client_inbounds WHERE inbound_id = ?", (inbound_id,))
    cursor.execute(
        """
        INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (client_id, inbound_id, flow, ts_ms),
    )


def ensure_v3_client_traffic(cursor: sqlite3.Cursor, conn: sqlite3.Connection, inbound_id: int, email: str) -> None:
    if not table_exists(conn, "client_traffics"):
        return
    cursor.execute("SELECT 1 FROM client_traffics WHERE email = ? LIMIT 1", (email,))
    if cursor.fetchone():
        cursor.execute(
            """
            UPDATE client_traffics
            SET inbound_id=?, enable=1, total=0, expiry_time=0, reset=0
            WHERE email=?
            """,
            (inbound_id, email),
        )
        return
    cursor.execute(
        """
        INSERT INTO client_traffics (
            inbound_id, enable, email, up, down, expiry_time, total, reset, last_online
        ) VALUES (?, 1, ?, 0, 0, 0, 0, 0, 0)
        """,
        (inbound_id, email),
    )


def sync_v3_client_for_inbound(
    conn: sqlite3.Connection,
    inbound_id: int,
    protocol: str,
    user_uuid: str,
    email: str,
    ts_ms: Optional[int] = None,
) -> None:
    if not has_v3_client_schema(conn):
        return
    ts = ts_ms if ts_ms is not None else now_ms()
    cursor = conn.cursor()
    client_id = upsert_v3_client_record(cursor, protocol, user_uuid, email, ts)
    link_v3_client_inbound(cursor, client_id, inbound_id, ts)
    ensure_v3_client_traffic(cursor, conn, inbound_id, email)


def extract_client_uuid(protocol: str, client: Dict[str, Any]) -> str:
    if protocol == "trojan":
        return str(client.get("password") or "")
    return str(client.get("id") or "")


def repair_v3_missing_client_bindings(
    db_path: str,
    inbound_ids: Optional[List[int]] = None,
) -> int:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return 0

    try:
        if not has_v3_client_schema(conn):
            return 0

        cursor = conn.cursor()
        if inbound_ids:
            placeholders = ",".join(["?"] * len(inbound_ids))
            cursor.execute(
                f"""
                SELECT id, tag, protocol, settings
                FROM inbounds
                WHERE id IN ({placeholders})
                  AND protocol IN ('vless', 'trojan', 'vmess')
                """,
                inbound_ids,
            )
        else:
            cursor.execute(
                """
                SELECT id, tag, protocol, settings
                FROM inbounds
                WHERE protocol IN ('vless', 'trojan', 'vmess')
                """
            )

        repaired = 0
        ts_ms = now_ms()
        for inbound_id, tag, protocol, settings_text in cursor.fetchall():
            inbound_id = int(inbound_id)
            protocol = str(protocol)
            cursor.execute(
                "SELECT COUNT(*) FROM client_inbounds WHERE inbound_id = ?",
                (inbound_id,),
            )
            if int(cursor.fetchone()[0]) > 0:
                continue

            client = parse_inbound_client_from_settings(protocol, str(settings_text or ""))
            if client is None:
                continue

            email = str(client.get("email") or "").strip()
            if not email:
                email = client_email_from_tag(str(tag or "")) or ""
            if not email:
                continue

            user_uuid = extract_client_uuid(protocol, client)
            if not user_uuid:
                continue

            if not str(client.get("email") or "").strip():
                payload = json.loads(settings_text or "{}")
                clients = payload.get("clients")
                if isinstance(clients, list) and clients and isinstance(clients[0], dict):
                    clients[0]["email"] = email
                    clients[0]["enable"] = True
                    if protocol == "vmess":
                        clients[0]["security"] = "auto"
                    clients[0]["tgId"] = 0
                    payload["clients"] = clients
                    if protocol == "vless":
                        ensure_vless_crypto_fields(payload)
                    cursor.execute(
                        "UPDATE inbounds SET settings=? WHERE id=?",
                        (json.dumps(payload, separators=(",", ":")), inbound_id),
                    )
            else:
                payload = json.loads(settings_text or "{}")
                changed = False
                if protocol == "vless":
                    old_d, old_e = payload.get("decryption"), payload.get("encryption")
                    ensure_vless_crypto_fields(payload)
                    if old_d != "none" or old_e != "none":
                        changed = True
                clients = payload.get("clients")
                if isinstance(clients, list) and clients and isinstance(clients[0], dict):
                    c0 = clients[0]
                    if c0.get("enable") is False:
                        c0["enable"] = True
                        changed = True
                    if protocol == "vmess" and not str(c0.get("security") or "").strip():
                        c0["security"] = "auto"
                        changed = True
                    if changed:
                        payload["clients"] = clients
                if changed:
                    cursor.execute(
                        "UPDATE inbounds SET settings=? WHERE id=?",
                        (json.dumps(payload, separators=(",", ":")), inbound_id),
                    )

            sync_v3_client_for_inbound(conn, inbound_id, protocol, user_uuid, email, ts_ms)
            repaired += 1

        if repaired:
            conn.commit()
        return repaired
    except sqlite3.Error as e:
        print(str(e))
        sys.exit(1)
    finally:
        conn.close()


def cleanup_v3_clients_for_inbounds(conn: sqlite3.Connection, inbound_ids: List[int]) -> None:
    if not inbound_ids or not has_v3_client_schema(conn):
        return

    cursor = conn.cursor()
    placeholders = ",".join(["?"] * len(inbound_ids))
    cursor.execute(
        f"""
        SELECT DISTINCT c.email
        FROM clients c
        JOIN client_inbounds ci ON ci.client_id = c.id
        WHERE ci.inbound_id IN ({placeholders})
        """,
        inbound_ids,
    )
    emails = [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    cursor.execute(f"DELETE FROM client_inbounds WHERE inbound_id IN ({placeholders})", inbound_ids)

    for email in emails:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM client_inbounds ci
            JOIN clients c ON c.id = ci.client_id
            WHERE c.email = ?
            """,
            (email,),
        )
        if int(cursor.fetchone()[0]) > 0:
            continue
        cursor.execute("DELETE FROM clients WHERE email = ?", (email,))
        if table_exists(conn, "client_traffics"):
            cursor.execute("DELETE FROM client_traffics WHERE email = ?", (email,))


def protocol_settings_legacy(protocol: str, user_uuid: str) -> Dict[str, Any]:
    """旧版 3x-ui：clients 嵌在 settings 内，email 可为空。"""
    if protocol == "vless":
        return {
            "clients": [{"id": user_uuid, "flow": "", "email": ""}],
            "decryption": "none",
            "encryption": "none",
            "fallbacks": [],
        }
    if protocol == "trojan":
        return {
            "clients": [{"password": user_uuid, "flow": "", "email": ""}],
            "fallbacks": [],
        }
    if protocol == "vmess":
        return {
            "clients": [{"id": user_uuid, "alterId": 0, "email": ""}],
        }
    raise ValueError(f"不支持的协议: {protocol}")


def normalize_existing_inbound_client_email(db_path: str) -> None:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        exit_error(str(e))

    try:
        v3_schema = has_v3_client_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, tag, settings FROM inbounds WHERE protocol IN ('vless','trojan','vmess')"
        )
        rows = cursor.fetchall()
        changed: List[tuple[str, int]] = []
        for row in rows:
            inbound_id = int(row[0])
            tag = str(row[1] or "")
            settings_text = str(row[2] or "")
            try:
                payload = json.loads(settings_text or "{}")
            except json.JSONDecodeError:
                continue
            clients = payload.get("clients")
            if not isinstance(clients, list):
                continue

            updated = False
            for client in clients:
                if not isinstance(client, dict):
                    continue
                email = str(client.get("email") or "").strip()
                if not email and v3_schema:
                    derived = client_email_from_tag(tag)
                    if derived:
                        client["email"] = derived
                        updated = True
                        continue
                if not email and v3_schema:
                    continue
                if client.get("email") is None:
                    client["email"] = ""
                    updated = True
                elif "email" not in client:
                    client["email"] = ""
                    updated = True

            if updated:
                changed.append((json.dumps(payload, separators=(",", ":")), inbound_id))

        if changed:
            cursor.executemany("UPDATE inbounds SET settings=? WHERE id=?", changed)
            conn.commit()
    except sqlite3.Error as e:
        print(str(e))
        sys.exit(1)
    finally:
        conn.close()


def maybe_repair_v3_client_bindings(
    db_path: str,
    mode: str,
    state: Optional[Dict[str, Any]] = None,
) -> None:
    if mode == "uninstall" or not os.path.exists(db_path):
        return
    inbound_ids: Optional[List[int]] = None
    if state and isinstance(state.get("inbound_ids"), list):
        parsed: List[int] = []
        for item in state["inbound_ids"]:
            try:
                parsed.append(int(item))
            except Exception:
                continue
        if parsed:
            inbound_ids = parsed
    repaired = repair_v3_missing_client_bindings(db_path, inbound_ids)
    if repaired:
        print(f"已修复 {repaired} 个 3x-ui v3 入站客户端绑定")
        restart_xui_service()


def ws_stream_settings(path: str) -> Dict[str, Any]:
    return {
        "network": "ws",
        "security": "none",
        "wsSettings": {"path": path},
    }


def sniffing_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
        "destOverride": ["http", "tls"],
        "metadataOnly": False,
        "routeOnly": False,
    }


def allocate_settings() -> Dict[str, Any]:
    return {"strategy": "always", "refresh": 5, "concurrency": 3}


def build_inbound_payload(protocol: str, user_uuid: str, short_id: str, route: Dict[str, Any]) -> Dict[str, Any]:
    email = client_email_for_route(short_id, protocol)
    return {
        "enable": True,
        "remark": f"{short_id}-{protocol}",
        "listen": "",
        "port": route["port"],
        "protocol": protocol,
        "expiryTime": 0,
        "tag": f"{short_id}-{protocol}",
        "settings": json.dumps(protocol_settings(protocol, user_uuid, email, v3=True), separators=(",", ":")),
        "streamSettings": json.dumps(ws_stream_settings(route["path"]), separators=(",", ":")),
        "sniffing": json.dumps(sniffing_settings(), separators=(",", ":")),
    }


def load_existing_ports_db(conn: sqlite3.Connection) -> Set[int]:
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT port FROM inbounds")
    except sqlite3.Error:
        return set()
    ports = set()
    for row in cursor.fetchall():
        try:
            ports.add(int(row[0]))
        except Exception:
            continue
    return ports


def load_existing_ports_api(client: XuiPanelClient) -> Set[int]:
    ports: Set[int] = set()
    for inbound in client.list_inbounds():
        try:
            ports.add(int(inbound.get("port", 0)))
        except (TypeError, ValueError):
            continue
    return ports


def random_ports(count: int, existing: Set[int]) -> List[int]:
    selected = set()
    while len(selected) < count:
        p = random.randint(PORT_MIN, PORT_MAX)
        if p in existing or p in selected:
            continue
        selected.add(p)
    return list(selected)


def parse_protocol_selection(raw: str) -> List[str]:
    text = raw.strip().lower()
    if not text:
        return list(PROTOCOL_ORDER)

    index_mapping = {"1": "vless", "2": "trojan", "3": "vmess"}
    name_mapping = {"vless": "vless", "trojan": "trojan", "vmess": "vmess"}

    selected: List[str] = []
    for token in text.replace(" ", "").split(","):
        if not token:
            continue
        protocol = index_mapping.get(token) or name_mapping.get(token)
        if protocol is None:
            exit_error(f"无效协议选项: {token}")
        if protocol not in selected:
            selected.append(protocol)

    if not selected:
        exit_error("至少选择一个协议")
    return selected


def get_inbounds_schema(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(inbounds)")
    rows = cursor.fetchall()
    schema: List[Dict[str, Any]] = []
    for row in rows:
        schema.append(
            {
                "name": row[1],
                "type": (row[2] or "").upper(),
                "notnull": bool(row[3]),
                "default": row[4],
                "pk": bool(row[5]),
            }
        )
    return schema


def load_template_inbound(conn: sqlite3.Connection) -> Dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inbounds ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        return {}
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def infer_default_value(col_type: str):
    if "INT" in col_type:
        return 0
    if "REAL" in col_type or "FLOA" in col_type or "DOUB" in col_type:
        return 0
    if "BLOB" in col_type:
        return b""
    return ""


def insert_inbounds_db(
    db_path: str,
    user_uuid: str,
    short_id: str,
    routes: List[Dict[str, Any]],
) -> List[int]:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        exit_error(str(e))

    try:
        schema = get_inbounds_schema(conn)
        if not schema:
            exit_error("未找到 inbounds 表")
        template = load_template_inbound(conn)
        cursor = conn.cursor()
        inserted_ids: List[int] = []
        v3_schema = has_v3_client_schema(conn)
        ts_ms = now_ms()

        for route in routes:
            protocol = route["protocol"]
            email = client_email_for_route(short_id, protocol)
            settings = (
                protocol_settings(protocol, user_uuid, email, v3=True)
                if v3_schema
                else protocol_settings_legacy(protocol, user_uuid)
            )
            row_data = dict(template)
            row_data.update(
                {
                    "user_id": 1,
                    "enable": 1,
                    "up": 0,
                    "down": 0,
                    "total": 0,
                    "remark": f"{short_id}-{protocol}",
                    "listen": "",
                    "port": route["port"],
                    "protocol": protocol,
                    "settings": json.dumps(settings, separators=(",", ":")),
                    "stream_settings": json.dumps(ws_stream_settings(route["path"]), separators=(",", ":")),
                    "sniffing": json.dumps(sniffing_settings(), separators=(",", ":")),
                    "allocate": json.dumps(allocate_settings(), separators=(",", ":")),
                    "tag": f"{short_id}-{protocol}",
                }
            )

            columns: List[str] = []
            values: List[Any] = []
            for col in schema:
                name = col["name"]
                if col["pk"]:
                    continue
                if name in row_data:
                    columns.append(name)
                    values.append(row_data[name])
                    continue
                if col["notnull"] and col["default"] is None:
                    columns.append(name)
                    values.append(infer_default_value(col["type"]))

            placeholders = ",".join(["?"] * len(columns))
            sql = f"INSERT INTO inbounds ({','.join(columns)}) VALUES ({placeholders})"
            cursor.execute(sql, values)
            inbound_id = int(cursor.lastrowid)
            inserted_ids.append(inbound_id)
            if v3_schema:
                sync_v3_client_for_inbound(conn, inbound_id, protocol, user_uuid, email, ts_ms)

        conn.commit()
        return inserted_ids
    except sqlite3.Error as e:
        print(str(e))
        sys.exit(1)
    finally:
        conn.close()


def delete_inbounds_db(db_path: str, inbound_ids: List[int], tags: List[str]) -> None:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        exit_error(str(e))

    try:
        cursor = conn.cursor()
        if inbound_ids:
            cleanup_v3_clients_for_inbounds(conn, inbound_ids)
            placeholders = ",".join(["?"] * len(inbound_ids))
            cursor.execute(f"DELETE FROM inbounds WHERE id IN ({placeholders})", inbound_ids)
        elif tags:
            cursor.execute(
                f"SELECT id FROM inbounds WHERE tag IN ({','.join(['?'] * len(tags))})",
                tags,
            )
            resolved_ids = [int(row[0]) for row in cursor.fetchall()]
            if resolved_ids:
                cleanup_v3_clients_for_inbounds(conn, resolved_ids)
            placeholders = ",".join(["?"] * len(tags))
            cursor.execute(f"DELETE FROM inbounds WHERE tag IN ({placeholders})", tags)
        conn.commit()
    except sqlite3.Error as e:
        print(str(e))
        sys.exit(1)
    finally:
        conn.close()


def restart_xui_service() -> None:
    try:
        result = subprocess.run(
            ["systemctl", "restart", "x-ui"],
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stderr.strip():
            print(result.stderr.strip())
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        if stderr:
            print(stderr)
        elif stdout:
            print(stdout)
        else:
            print(str(e))
        sys.exit(1)


def create_inbounds_via_api(
    client: XuiPanelClient,
    user_uuid: str,
    short_id: str,
    routes: List[Dict[str, Any]],
) -> List[int]:
    inserted_ids: List[int] = []
    for route in routes:
        protocol = route["protocol"]
        payload = build_inbound_payload(protocol, user_uuid, short_id, route)
        created = client.add_inbound(payload)
        inbound_id = created.get("id")
        if inbound_id is None:
            exit_error(f"创建 {protocol} 入站失败：API 未返回 id")
        inserted_ids.append(int(inbound_id))
    client.restart_xray()
    return inserted_ids


def delete_inbounds_via_api(client: XuiPanelClient, inbound_ids: List[int]) -> None:
    for inbound_id in inbound_ids:
        client.delete_inbound(inbound_id)
    if inbound_ids:
        client.restart_xray()


def create_inbounds(
    backend: str,
    user_uuid: str,
    short_id: str,
    routes: List[Dict[str, Any]],
    panel: Optional[XuiPanelClient] = None,
) -> List[int]:
    if backend == BACKEND_API:
        if panel is None:
            exit_error("API 模式需要已登录的面板客户端")
        return create_inbounds_via_api(panel, user_uuid, short_id, routes)
    inbound_ids = insert_inbounds_db(DB_PATH, user_uuid, short_id, routes)
    restart_xui_service()
    return inbound_ids


def delete_managed_inbounds(
    backend: str,
    inbound_ids: List[int],
    tags: List[str],
    panel: Optional[XuiPanelClient] = None,
) -> None:
    if backend == BACKEND_API:
        if panel is None:
            exit_error("API 模式需要已登录的面板客户端")
        delete_inbounds_via_api(panel, inbound_ids)
        return
    delete_inbounds_db(DB_PATH, inbound_ids, tags)
    restart_xui_service()


def build_links(user_uuid: str, domain: str, routes: List[Dict[str, Any]]) -> Dict[str, str]:
    base_url = f"https://yx-auto.pages.dev/{user_uuid}/sub"
    common = {
        "domain": domain,
        "epd": "yes",
        "epi": "yes",
        "egi": "no",
        "dkby": "yes",
    }

    links = {}
    for route in routes:
        protocol = route["protocol"]
        params = dict(common)
        params["ev"] = "no"
        params["et"] = "no"
        params["evm"] = "no"
        params[PROTOCOL_QUERY_FLAG[protocol]] = "yes"
        params["path"] = route["path"]
        links[protocol] = f"{base_url}?{parse.urlencode(params, safe='', quote_via=parse.quote)}"

    return links


def load_last_state() -> Optional[Dict[str, Any]]:
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        exit_error(f"读取上次配置失败: {e}")
    if not isinstance(data, dict):
        return None
    return data


def save_last_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.chmod(STATE_PATH, 0o600)
    except OSError as e:
        exit_error(f"保存上次配置失败: {e}")


def remove_last_state() -> None:
    try:
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
    except OSError as e:
        exit_error(f"删除上次配置记录失败: {e}")


def save_last_links_snapshot(domain: str, user_uuid: str, links: Dict[str, str], order: List[str]) -> None:
    lines = [
        "上次生成订阅",
        f"域名: {domain}",
        f"UUID: {user_uuid}",
        "",
    ]
    for protocol in order:
        link = links.get(protocol)
        if link:
            lines.append(f"{PROTOCOL_LABEL[protocol]}订阅 {link}")
    lines.append("")
    content = "\n".join(lines)
    try:
        with open(LAST_LINKS_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(LAST_LINKS_PATH, 0o600)
    except OSError as e:
        exit_error(f"保存上次订阅失败: {e}")


def extract_client_key(protocol: str) -> str:
    if protocol == "trojan":
        return "password"
    return "id"


def extract_uuid_from_settings(protocol: str, settings_text: str) -> str:
    try:
        payload = json.loads(settings_text or "{}")
    except json.JSONDecodeError:
        return ""
    clients = payload.get("clients")
    if not isinstance(clients, list) or not clients:
        return ""
    first = clients[0] if isinstance(clients[0], dict) else {}
    key = extract_client_key(protocol)
    value = str(first.get(key, "")).strip()
    return value


def extract_ws_path(stream_settings_text: str) -> str:
    if isinstance(stream_settings_text, dict):
        payload = stream_settings_text
    else:
        try:
            payload = json.loads(stream_settings_text or "{}")
        except json.JSONDecodeError:
            return ""
    ws = payload.get("wsSettings")
    if not isinstance(ws, dict):
        return ""
    path = str(ws.get("path", "")).strip()
    if not path.startswith("/"):
        return ""
    return path


def extract_short_id(path: str, tag: str, remark: str) -> str:
    path_match = re.match(r"^/([0-9a-f]{8})-(vl|tr|vm)$", path.strip().lower())
    if path_match:
        return path_match.group(1)

    for text in (tag, remark):
        m = re.match(r"^([0-9a-f]{8})-(vless|trojan|vmess)$", str(text).strip().lower())
        if m:
            return m.group(1)
    return ""


def _group_legacy_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sid = row["short_id"]
        bucket = grouped.setdefault(
            sid,
            {"max_id": 0, "uuid_votes": {}, "routes": {}, "enabled_count": 0},
        )
        bucket["max_id"] = max(bucket["max_id"], row["id"])
        bucket["routes"][row["protocol"]] = {"protocol": row["protocol"], "path": row["path"], "port": 0}
        bucket["uuid_votes"][row["uuid"]] = bucket["uuid_votes"].get(row["uuid"], 0) + 1
        if row["enable"] == 1:
            bucket["enabled_count"] += 1

    best_sid = ""
    best_score = (-1, -1, -1)
    for sid, data in grouped.items():
        score = (data["enabled_count"], len(data["routes"]), data["max_id"])
        if score > best_score:
            best_score = score
            best_sid = sid

    if not best_sid:
        return {}

    best = grouped[best_sid]
    if not best["routes"]:
        return {}
    best_uuid = max(best["uuid_votes"].items(), key=lambda x: x[1])[0]
    order = [p for p in PROTOCOL_ORDER if p in best["routes"]]
    return {
        "short_id": best_sid,
        "uuid": best_uuid,
        "routes": [best["routes"][p] for p in order],
        "selected_protocols": order,
    }


def load_legacy_routes_from_db() -> Dict[str, Any]:
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:
        exit_error(str(e))

    rows: List[Dict[str, Any]] = []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, protocol, settings, stream_settings, tag, remark, enable "
            "FROM inbounds WHERE protocol IN ('vless','trojan','vmess') ORDER BY id DESC"
        )
        for item in cursor.fetchall():
            protocol = str(item[1]).strip().lower()
            if protocol not in PROTOCOL_ORDER:
                continue
            ws_path = extract_ws_path(str(item[3] or ""))
            if not ws_path:
                continue
            short_id = extract_short_id(ws_path, str(item[4] or ""), str(item[5] or ""))
            if not short_id:
                continue
            user_uuid = extract_uuid_from_settings(protocol, str(item[2] or ""))
            if not user_uuid:
                continue
            rows.append(
                {
                    "id": int(item[0]),
                    "protocol": protocol,
                    "path": ws_path,
                    "short_id": short_id,
                    "uuid": user_uuid,
                    "enable": int(item[6] or 0),
                }
            )
    except sqlite3.Error as e:
        exit_error(str(e))
    finally:
        conn.close()

    return _group_legacy_rows(rows)


def load_legacy_routes_from_panel(client: XuiPanelClient) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for item in client.list_inbounds():
        protocol = str(item.get("protocol", "")).strip().lower()
        if protocol not in PROTOCOL_ORDER:
            continue
        stream_settings = item.get("streamSettings")
        if isinstance(stream_settings, dict):
            stream_text = json.dumps(stream_settings)
        else:
            stream_text = str(stream_settings or "")
        ws_path = extract_ws_path(stream_text)
        if not ws_path:
            continue
        short_id = extract_short_id(ws_path, str(item.get("tag") or ""), str(item.get("remark") or ""))
        if not short_id:
            continue
        settings = item.get("settings")
        if isinstance(settings, dict):
            settings_text = json.dumps(settings)
        else:
            settings_text = str(settings or "")
        user_uuid = extract_uuid_from_settings(protocol, settings_text)
        if not user_uuid:
            continue
        inbound_id = item.get("id")
        if inbound_id is None:
            continue
        rows.append(
            {
                "id": int(inbound_id),
                "protocol": protocol,
                "path": ws_path,
                "short_id": short_id,
                "uuid": user_uuid,
                "enable": 1 if item.get("enable") else 0,
            }
        )
    return _group_legacy_rows(rows)


def print_last_links() -> None:
    if os.path.exists(LAST_LINKS_PATH):
        try:
            with open(LAST_LINKS_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except OSError as e:
            exit_error(f"读取上次订阅失败: {e}")
        if content:
            print(content)
            return

    state = load_last_state()
    if state:
        links = state.get("links")
        if isinstance(links, dict):
            order = state.get("selected_protocols") or PROTOCOL_ORDER
            for protocol in order:
                p = str(protocol).lower()
                if p in links:
                    print(f"{PROTOCOL_LABEL.get(p, p.upper())}订阅 {links[p]}")
            return

        legacy_domain = str(state.get("domain", "")).strip()
        legacy_uuid = str(state.get("uuid", "")).strip()
        legacy_routes = state.get("routes")
        if legacy_domain and legacy_uuid and isinstance(legacy_routes, list) and legacy_routes:
            links = build_links(legacy_uuid, legacy_domain, legacy_routes)
            order = state.get("selected_protocols") or [r.get("protocol") for r in legacy_routes]
            order = [str(p).lower() for p in order if str(p).lower() in links]
            save_last_links_snapshot(legacy_domain, legacy_uuid, links, order)
            for protocol in order:
                print(f"{PROTOCOL_LABEL.get(protocol, protocol.upper())}订阅 {links[protocol]}")
            return

    if os.path.exists(DB_PATH):
        recovered = load_legacy_routes_from_db()
        if recovered:
            domain = input("未找到缓存，请输入绑定域名用于旧版兼容拼接: ").strip()
            if not domain:
                exit_error("域名不能为空")
            links = build_links(str(recovered["uuid"]), domain, recovered["routes"])
            order = recovered["selected_protocols"]
            save_last_links_snapshot(domain, str(recovered["uuid"]), links, order)
            for protocol in order:
                if protocol in links:
                    print(f"{PROTOCOL_LABEL[protocol]}订阅 {links[protocol]}")
            return

    if os.environ.get("XUI_API_TOKEN") or os.environ.get("XUI_PANEL_URL"):
        runtime = detect_xui_environment()
        panel = setup_panel_client(runtime, interactive=True)
        recovered = load_legacy_routes_from_panel(panel)
        if recovered:
            domain = input("未找到缓存，请输入绑定域名用于旧版兼容拼接: ").strip()
            if not domain:
                exit_error("域名不能为空")
            links = build_links(str(recovered["uuid"]), domain, recovered["routes"])
            order = recovered["selected_protocols"]
            save_last_links_snapshot(domain, str(recovered["uuid"]), links, order)
            for protocol in order:
                if protocol in links:
                    print(f"{PROTOCOL_LABEL[protocol]}订阅 {links[protocol]}")
            return

    exit_error("未找到可查看的上次订阅")


def restore_dns_record(
    zone_id: str,
    domain: str,
    headers: Dict[str, str],
    dns_backup: Optional[Dict[str, Any]],
    managed_dns_record_id: str,
) -> None:
    existed = bool((dns_backup or {}).get("existed"))
    record = (dns_backup or {}).get("record") or {}
    if existed:
        record_id = str(record.get("id", "")).strip()
        if not record_id:
            current = get_dns_record(zone_id, domain, headers)
            if current:
                record_id = str(current.get("id", "")).strip()
        if not record_id:
            return
        payload = {
            "type": record.get("type", "A"),
            "name": record.get("name", domain),
            "content": record.get("content", ""),
            "proxied": bool(record.get("proxied", False)),
            "ttl": int(record.get("ttl", 1)),
        }
        if not payload["content"]:
            return
        call_cf_api("PUT", f"/zones/{zone_id}/dns_records/{record_id}", headers=headers, data=payload)
        return

    record_id = managed_dns_record_id.strip()
    if not record_id:
        current = get_dns_record(zone_id, domain, headers)
        if current:
            record_id = str(current.get("id", "")).strip()
    if record_id:
        call_cf_api("DELETE", f"/zones/{zone_id}/dns_records/{record_id}", headers=headers)


def uninstall_last_config(
    state: Dict[str, Any],
    headers: Dict[str, str],
    backend: str,
    panel: Optional[XuiPanelClient] = None,
) -> None:
    domain = str(state.get("domain", "")).strip()
    zone_id = str(state.get("zone_id", "")).strip()
    if not domain or not zone_id:
        exit_error("上次配置缺少 domain 或 zone_id，无法卸载")

    current_rules = get_origin_rules(zone_id, headers)
    put_origin_rules(zone_id, headers, strip_managed_origin_rules(current_rules, domain))

    ssl_backup = str(state.get("ssl_backup", "")).strip()
    if ssl_backup:
        set_ssl_mode(zone_id, headers, ssl_backup)

    restore_dns_record(
        zone_id=zone_id,
        domain=domain,
        headers=headers,
        dns_backup=state.get("dns_backup"),
        managed_dns_record_id=str(state.get("managed_dns_record_id", "")),
    )

    inbound_ids: List[int] = []
    for item in state.get("inbound_ids", []):
        try:
            inbound_ids.append(int(item))
        except Exception:
            continue
    tags = [str(x) for x in state.get("tags", []) if str(x).strip()]
    delete_managed_inbounds(backend, inbound_ids, tags, panel=panel)


def run_deploy_install() -> None:
    last_state = load_last_state()
    backend, runtime, reason = resolve_backend()
    print(f"x-ui 写入方式: {backend_label(backend)} ({reason})")
    panel = None
    if backend == BACKEND_DB:
        if not os.path.exists(DB_PATH):
            exit_error(f"未找到 3x-ui 数据库: {DB_PATH}")
        normalize_existing_inbound_client_email(DB_PATH)
        maybe_repair_v3_client_bindings(DB_PATH, "install", last_state)
    else:
        panel = setup_panel_client(runtime, interactive=False)

    if last_state is not None:
        last_domain = str(last_state.get("domain", "未知域名"))
        exit_error(f"检测到上次配置({last_domain})，请先执行卸载")

    domain = input("绑定域名: ").strip()
    cf_email, cf_key = prompt_cf_credentials()
    selected_protocols = parse_protocol_selection(
        input("创建协议(1=vless,2=trojan,3=vmess，逗号分隔，留空=全部): ")
    )

    if not domain or not cf_email or not cf_key or not selected_protocols:
        exit_error("域名、邮箱、API Key 和协议选项不能为空")

    user_uuid = str(uuid.uuid4())
    short_id = user_uuid[:8]

    if backend == BACKEND_API:
        existing_ports = load_existing_ports_api(panel)  # type: ignore[arg-type]
    else:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                existing_ports = load_existing_ports_db(conn)
        except sqlite3.Error as e:
            exit_error(str(e))

    ports = random_ports(len(selected_protocols), existing_ports)
    routes = []
    for i, protocol in enumerate(selected_protocols):
        routes.append(
            {
                "protocol": protocol,
                "port": ports[i],
                "path": f"/{short_id}-{PROTOCOL_SUFFIX[protocol]}",
            }
        )

    headers = build_cf_headers(cf_email, cf_key)
    zone = find_best_zone(domain, zones)
    if zone is None:
        exit_error(f"无法匹配该域名对应的 Zone: {domain}")

    zone_id = zone["id"]
    public_ip = get_public_ipv4()
    dns_before = get_dns_record(zone_id, domain, headers)
    ssl_before = get_ssl_mode(zone_id, headers)
    origin_rules_before = get_origin_rules(zone_id, headers)

    inbound_ids = create_inbounds(
        backend,
        user_uuid=user_uuid,
        short_id=short_id,
        routes=routes,
        panel=panel,
    )

    managed_dns_record_id = upsert_dns_record(zone_id, domain, public_ip, headers)
    set_ssl_mode(zone_id, headers, "flexible")
    apply_origin_rules(zone_id, headers, domain, routes)

    links = build_links(user_uuid, domain, routes)
    save_last_links_snapshot(domain=domain, user_uuid=user_uuid, links=links, order=selected_protocols)

    state_version = 2 if backend == BACKEND_API else 1
    save_last_state(
        {
            "version": state_version,
            "backend": backend,
            "domain": domain,
            "zone_id": zone_id,
            "uuid": user_uuid,
            "short_id": short_id,
            "routes": routes,
            "inbound_ids": inbound_ids,
            "tags": [f"{short_id}-{p}" for p in selected_protocols],
            "managed_dns_record_id": managed_dns_record_id,
            "dns_backup": {
                "existed": dns_before is not None,
                "record": dns_before,
            },
            "ssl_backup": ssl_before,
            "origin_rules_backup": origin_rules_before,
            "links": links,
            "selected_protocols": selected_protocols,
        }
    )

    print("成功")
    print(f"已保存订阅到 {LAST_LINKS_PATH}")
    for protocol in selected_protocols:
        print(f"{PROTOCOL_LABEL[protocol]}订阅 {links[protocol]}")


def main() -> None:
    ensure_cfd_command()
    mode = select_mode_interactive()
    prompt_maybe_localize_xui_menu()
    last_state = load_last_state()

    if mode == "fresh":
        if is_xui_installed():
            exit_error("检测到已安装 3x-ui，请使用模式 1 安装节点")
        ensure_xui_for_fresh_setup()
        run_deploy_install()
        return

    if mode == "panel":
        if not has_script_installed_panel():
            exit_error("当前面板非本脚本安装，无法查看面板访问信息")
        print_panel_access_info()
        return

    if mode == "xui_manage":
        print_xui_management_help()
        return

    if mode == "show":
        maybe_repair_v3_client_bindings(DB_PATH, mode, last_state)
        print_last_links()
        return

    if mode == "uninstall":
        if last_state is None:
            exit_error("未检测到上次配置，无法卸载")
        backend, runtime, reason = resolve_backend(last_state)
        print(f"x-ui 写入方式: {backend_label(backend)} ({reason})")
        panel: Optional[XuiPanelClient] = None
        if backend == BACKEND_API:
            panel = setup_panel_client(runtime, interactive=False)
        cf_email, cf_key = prompt_cf_credentials()
        headers = build_cf_headers(cf_email, cf_key)
        uninstall_last_config(last_state, headers, backend, panel=panel)
        remove_last_state()
        print("卸载成功")
        return

    if not is_xui_installed():
        exit_error("未检测到 3x-ui，请使用模式 4(全新安装)")

    run_deploy_install()
    return


if __name__ == "__main__":
    main()
