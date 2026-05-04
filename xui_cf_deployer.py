#!/usr/bin/env python3
import ipaddress
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import uuid
from getpass import getpass
from typing import Any, Dict, List, Optional, Set
from urllib import error, parse, request


DB_PATH = "/etc/x-ui/x-ui.db"
STATE_PATH = "/etc/x-ui/cf_auto_state.json"
LAST_LINKS_PATH = os.path.join(os.getcwd(), "cf_auto_last_links.txt")
CF_API_BASE = "https://api.cloudflare.com/client/v4"
PORT_MIN = 10000
PORT_MAX = 60000
PROTOCOL_ORDER = ["vless", "trojan", "vmess"]
PROTOCOL_SUFFIX = {"vless": "vl", "trojan": "tr", "vmess": "vm"}
PROTOCOL_LABEL = {"vless": "VLESS", "trojan": "TROJAN", "vmess": "VMESS"}
PROTOCOL_QUERY_FLAG = {"vless": "ev", "trojan": "et", "vmess": "evm"}
MANAGED_RULE_PREFIX = "3x-ui-auto "


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
):
    payload = None
    if data is not None:
        payload = json.dumps(data).encode("utf-8")

    req = request.Request(url=url, data=payload, headers=headers or {}, method=method)

    try:
        with request.urlopen(req, timeout=timeout) as resp:
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
    else:
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


def strip_managed_origin_rules(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = []
    for rule in rules:
        description = str(rule.get("description", ""))
        if description.startswith(MANAGED_RULE_PREFIX):
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


def put_origin_rules(zone_id: str, headers: Dict[str, str], rules: List[Dict[str, Any]]) -> None:
    # Cloudflare phases entrypoint 接口使用最小 body，避免字段不兼容
    payload = {"rules": rules}
    call_cf_api(
        "PUT",
        f"/zones/{zone_id}/rulesets/phases/http_request_origin/entrypoint",
        headers=headers,
        data=payload,
    )


def apply_origin_rules(
    zone_id: str, headers: Dict[str, str], domain: str, routes: List[Dict[str, Any]]
) -> None:
    existing = get_origin_rules(zone_id, headers)
    next_rules = strip_managed_origin_rules(existing) + build_origin_rules(domain, routes)
    put_origin_rules(zone_id, headers, next_rules)


def protocol_settings(protocol: str, user_uuid: str) -> Dict[str, Any]:
    if protocol == "vless":
        return {
            "clients": [{"id": user_uuid, "flow": "", "email": ""}],
            "decryption": "none",
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
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, settings FROM inbounds WHERE protocol IN ('vless','trojan','vmess')"
        )
        rows = cursor.fetchall()
        changed: List[tuple[str, int]] = []
        for row in rows:
            inbound_id = int(row[0])
            settings_text = str(row[1] or "")
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


def load_existing_ports(conn: sqlite3.Connection) -> Set[int]:
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


def parse_mode(raw: str) -> str:
    text = raw.strip().lower()
    if text in ("", "1", "install", "i", "安装"):
        return "install"
    if text in ("2", "uninstall", "u", "卸载"):
        return "uninstall"
    if text in ("3", "show", "view", "v", "查看"):
        return "show"
    exit_error("无效模式，仅支持 1(安装) / 2(卸载) / 3(查看上次订阅)")


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


def insert_inbounds(
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

        for route in routes:
            protocol = route["protocol"]
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
                    "settings": json.dumps(protocol_settings(protocol, user_uuid), separators=(",", ":")),
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
            inserted_ids.append(int(cursor.lastrowid))

        conn.commit()
        return inserted_ids
    except sqlite3.Error as e:
        print(str(e))
        sys.exit(1)
    finally:
        conn.close()


def delete_inbounds(db_path: str, inbound_ids: List[int], tags: List[str]) -> None:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        exit_error(str(e))

    try:
        cursor = conn.cursor()
        if inbound_ids:
            placeholders = ",".join(["?"] * len(inbound_ids))
            cursor.execute(f"DELETE FROM inbounds WHERE id IN ({placeholders})", inbound_ids)
        elif tags:
            placeholders = ",".join(["?"] * len(tags))
            cursor.execute(f"DELETE FROM inbounds WHERE tag IN ({placeholders})", tags)
        conn.commit()
    except sqlite3.Error as e:
        print(str(e))
        sys.exit(1)
    finally:
        conn.close()


def restart_xui() -> None:
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


def load_legacy_routes_from_xui() -> Dict[str, Any]:
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

        # 兼容旧版本：state 里没有 links 时，使用 domain/uuid/routes 重新拼接
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

    # 兼容更旧版本：无 state 信息，直接从 x-ui 数据库反推一套最新节点
    recovered = load_legacy_routes_from_xui()
    if not recovered:
        exit_error("未找到可查看的上次订阅")
    domain = input("未找到缓存，请输入绑定域名用于旧版兼容拼接: ").strip()
    if not domain:
        exit_error("域名不能为空")
    links = build_links(str(recovered["uuid"]), domain, recovered["routes"])
    order = recovered["selected_protocols"]
    save_last_links_snapshot(domain, str(recovered["uuid"]), links, order)
    for protocol in order:
        if protocol in links:
            print(f"{PROTOCOL_LABEL[protocol]}订阅 {links[protocol]}")


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


def uninstall_last_config(state: Dict[str, Any], headers: Dict[str, str]) -> None:
    domain = str(state.get("domain", "")).strip()
    zone_id = str(state.get("zone_id", "")).strip()
    if not domain or not zone_id:
        exit_error("上次配置缺少 domain 或 zone_id，无法卸载")

    origin_backup = state.get("origin_rules_backup")
    if isinstance(origin_backup, list):
        put_origin_rules(zone_id, headers, origin_backup)
    else:
        current_rules = get_origin_rules(zone_id, headers)
        put_origin_rules(zone_id, headers, strip_managed_origin_rules(current_rules))

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
    delete_inbounds(DB_PATH, inbound_ids, tags)
    restart_xui()


def main() -> None:
    normalize_existing_inbound_client_email(DB_PATH)
    mode = parse_mode(input("模式(1=安装,2=卸载,3=查看上次订阅，回车=安装): "))
    last_state = load_last_state()

    if mode == "show":
        print_last_links()
        return

    if mode == "uninstall":
        if last_state is None:
            exit_error("未检测到上次配置，无法卸载")
        cf_email = input("Cloudflare 邮箱: ").strip()
        cf_key = getpass("Cloudflare Global API Key: ").strip()
        if not cf_email or not cf_key:
            exit_error("邮箱和 API Key 不能为空")
        headers = {
            "X-Auth-Email": cf_email,
            "X-Auth-Key": cf_key,
            "Content-Type": "application/json",
        }
        uninstall_last_config(last_state, headers)
        remove_last_state()
        print("卸载成功")
        return

    if last_state is not None:
        last_domain = str(last_state.get("domain", "未知域名"))
        exit_error(f"检测到上次配置({last_domain})，请先执行卸载")

    domain = input("绑定域名: ").strip()
    cf_email = input("Cloudflare 邮箱: ").strip()
    cf_key = getpass("Cloudflare Global API Key: ").strip()
    selected_protocols = parse_protocol_selection(
        input("创建协议(1=vless,2=trojan,3=vmess，逗号分隔，留空=全部): ")
    )

    if not domain or not cf_email or not cf_key or not selected_protocols:
        exit_error("域名、邮箱、API Key 和协议选项不能为空")

    user_uuid = str(uuid.uuid4())
    short_id = user_uuid[:8]

    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing_ports = load_existing_ports(conn)
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

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_key,
        "Content-Type": "application/json",
    }

    zones = fetch_all_zones(headers)
    zone = find_best_zone(domain, zones)
    if zone is None:
        exit_error(f"无法匹配该域名对应的 Zone: {domain}")

    zone_id = zone["id"]
    public_ip = get_public_ipv4()
    dns_before = get_dns_record(zone_id, domain, headers)
    ssl_before = get_ssl_mode(zone_id, headers)
    origin_rules_before = get_origin_rules(zone_id, headers)

    inbound_ids = insert_inbounds(DB_PATH, user_uuid=user_uuid, short_id=short_id, routes=routes)
    restart_xui()

    managed_dns_record_id = upsert_dns_record(zone_id, domain, public_ip, headers)
    set_ssl_mode(zone_id, headers, "flexible")
    apply_origin_rules(zone_id, headers, domain, routes)

    links = build_links(user_uuid, domain, routes)
    save_last_links_snapshot(domain=domain, user_uuid=user_uuid, links=links, order=selected_protocols)

    save_last_state(
        {
            "version": 1,
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


if __name__ == "__main__":
    main()
