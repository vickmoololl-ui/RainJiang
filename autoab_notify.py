"""
autoab.net 订单通知监控脚本
带 session 持久化 + Google Maps 导航 + 驾车距离计算
"""
import os
import json
import sys
import time
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
try:
    import requests
except ImportError:
    print("请先安装 requests: pip install requests")
    sys.exit(1)

CONFIG = {
    "autoab_username": os.environ.get("AUTOAB_USERNAME", ""),
    "autoab_password": os.environ.get("AUTOAB_PASSWORD", ""),
    "autoab_grabid": os.environ.get("AUTOAB_GRABID", ""),
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
}

BASE_URL = "https://www.autoab.net/index.php/api"
LOGIN_URL = f"{BASE_URL}/user/login"
PROFILE_URL = f"{BASE_URL}/user/profile"
POLL_URL = f"{BASE_URL}/grab/poll_orders"
STATE_FILE = Path(__file__).parent / "state.json"
GEO_HEADERS = {"User-Agent": "autoab-notify/1.0"}

# 中英文地址翻译对照
TRANSLATE_MAP = {
    "吉隆坡": "Kuala Lumpur",
    "国际机场": "International Airport",
    "第一终站": "Terminal 1",
    "第二终站": "Terminal 2",
    "机场": "Airport",
    "酒店": "Hotel",
    "雪邦": "Sepang",
    "莎阿南": "Shah Alam",
    "马来西亚": "Malaysia",
    "柏威年": "Pavilion",
    "逸林": "DoubleTree",
    "希尔顿": "Hilton",
}

# 地址 → 搜索关键词 映射（用于 Nominatim 查不到时的后备）
# 按前缀匹配，越长越优先
ADDRESS_ALIAS = [
    ("Legasi Kampung Baru Residensi", "Kampung Baru, Kuala Lumpur, Malaysia"),
    ("PV18 Residences", "Bukit Jalil, Kuala Lumpur, Malaysia"),
    ("i-City", "i-City, Shah Alam, Malaysia"),
    ("Ulu Bernam", "Ulu Bernam, Selangor, Malaysia"),
    ("吉隆坡国际机场", "KLIA, Sepang, Malaysia"),
    ("KLIA", "KLIA, Sepang, Malaysia"),
]


def send_telegram(message: str) -> bool:
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"[x] Telegram 异常: {e}")
        return False


# ============================================================
# 距离计算
# ============================================================

def _clean(address):
    """清理地址，去掉房间号后缀"""
    a = address.strip()
    a = re.sub(r'\s*[-–]\s*\w+\s*[-–]\s*\w+\s*$', '', a)
    a = re.sub(r'\s*[-–]\s*\w+\s*$', '', a)
    a = re.sub(r'\s+', ' ', a).strip()
    return a


def _translate(address):
    """中文字词转英文"""
    for cn, en in TRANSLATE_MAP.items():
        address = address.replace(cn, en + " ")
    return re.sub(r'\s+', ' ', address).strip()


def _find_alias(address):
    """用别名表查搜索关键词"""
    for prefix, alias in ADDRESS_ALIAS:
        if address.startswith(prefix):
            return alias
    return None


def _geocode(query):
    """Nominatim 地址转坐标"""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers=GEO_HEADERS, timeout=10
        )
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None


def get_coordinates(address):
    """多策略转坐标"""
    cleaned = _clean(address)

    # 策略1: 直接查
    lat, lon = _geocode(cleaned)
    if lat:
        return lat, lon

    # 策略2: 翻译后查
    translated = _translate(cleaned)
    if translated != cleaned:
        lat, lon = _geocode(translated)
        if lat:
            return lat, lon

    # 策略3: 别名查
    alias = _find_alias(cleaned)
    if alias:
        lat, lon = _geocode(alias)
        if lat:
            return lat, lon

    return None, None


def calc_driving_distance(from_addr, to_addr):
    """计算两点间驾车距离（公里）"""
    f_lat, f_lon = get_coordinates(from_addr)
    if not f_lat:
        return None
    time.sleep(1.1)  # Nominatim 限速

    t_lat, t_lon = get_coordinates(to_addr)
    if not t_lat:
        return None

    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{f_lon},{f_lat};{t_lon},{t_lat}?overview=false"
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if data.get("code") == "Ok" and data["routes"]:
            return round(data["routes"][0]["distance"] / 1000, 1)
    except Exception:
        pass
    return None


# ============================================================
# 通知消息
# ============================================================

def notify_new_order(order: dict) -> bool:
    amount = order.get("order_amount", "?")
    order_time = order.get("order_time", "?")
    match_type = order.get("match_type", "?")
    match_mode = order.get("match_mode", "?")
    from_loc = order.get("from_location", "?")
    to_loc = order.get("to_location", "?")
    remark = order.get("remark", "")

    mode_map = {"to": "去机场", "from": "从机场出发", "fare": "一口价"}
    mode_cn = mode_map.get(match_mode, match_mode)

    lines = [f"🚗 <b>新订单提醒</b>"]
    lines.append(f"💰 金额：<b>{amount} 马币</b>")

    # 计算距离（不影响主流程）
    dist = calc_driving_distance(from_loc, to_loc)
    if dist and amount != "?":
        ppkm = round(float(amount) / dist, 2)
        lines.append(f"📏 驾车距离：{dist} km ｜ {ppkm} 马币/km")
    else:
        lines.append(f"🕐 {order_time} ｜ 🚘 {match_type.upper()} | {mode_cn}")

    if not (dist and amount != "?"):
        pass  # 时间和类型已经在上面显示了
    else:
        lines.append(f"🕐 {order_time} ｜ 🚘 {match_type.upper()} | {mode_cn}")

    lines.append(f"📍 上车：{from_loc}")
    lines.append(f"🏁 下车：{to_loc}")

    # Google Maps 导航链接（地址转英文，让地图搜得准）
    maps_from = _translate(from_loc) if from_loc != "?" else from_loc
    maps_to = _translate(to_loc) if to_loc != "?" else to_loc
    maps_url = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote(maps_from)}&destination={urllib.parse.quote(maps_to)}"
    lines.append(f"🗺️ <a href=\"{maps_url}\">Google Maps 导航</a>")

    if remark:
        lines.append(f"\n{remark}")

    return send_telegram("\n".join(lines))


# ============================================================
# autoab API
# ============================================================

def login_and_get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    })
    resp = session.post(LOGIN_URL, data={
        "username": CONFIG["autoab_username"],
        "password": CONFIG["autoab_password"],
        "keeptime": "31536000",
    }, timeout=15)
    data = resp.json()
    if data.get("code") != 1:
        raise Exception(f"登录失败: {data.get('msg', '未知错误')}")
    print(f"[+] 登录成功: {data['data']['userinfo']['username']}")
    return session


def try_saved_session(session: requests.Session) -> bool:
    try:
        resp = session.get(PROFILE_URL, timeout=10)
        data = resp.json()
        if data.get("code") == 1:
            print("[+] 使用已有 session（无需登录）")
            return True
        return False
    except Exception:
        return False


def load_state() -> dict:
    default = {"notified_ids": [], "phpsessid": None}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {**default, **state}
        except Exception:
            return default
    return default


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def poll_orders(session) -> dict:
    resp = session.get(POLL_URL, params={"grabid": CONFIG["autoab_grabid"]}, timeout=15)
    data = resp.json()
    if data.get("code") != 1:
        return {"list": []}
    return data["data"]


def main():
    print(f"[*] 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not CONFIG["telegram_bot_token"] or not CONFIG["telegram_chat_id"]:
        print("[!] 请设置 Telegram 配置")
        return
    if not CONFIG["autoab_username"] or not CONFIG["autoab_password"]:
        print("[!] 请设置 autoab 账号")
        return

    state = load_state()
    notified_ids = set(state.get("notified_ids", []))
    saved_phpsessid = state.get("phpsessid")
    print(f"[*] 已通知订单数: {len(notified_ids)}")

    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    session_ok = False

    if saved_phpsessid:
        session.cookies.set("PHPSESSID", saved_phpsessid, domain="www.autoab.net", path="/")
        if try_saved_session(session):
            session_ok = True
        else:
            print("[*] 已有 session 过期，重新登录")

    if not session_ok:
        session = login_and_get_session()
        for cookie in session.cookies:
            if cookie.name == "PHPSESSID":
                state["phpsessid"] = cookie.value
                break

    data = poll_orders(session)
    orders = data.get("list", [])

    if not orders:
        print("[*] 没有新订单")
    else:
        new_orders = [o for o in orders if o["id"] not in notified_ids]
        print(f"[*] 获取 {len(orders)} 条，新 {len(new_orders)} 条")
        for order in new_orders:
            print(f"  -> #{order['id']}: {order['order_amount']} 马币")
            notify_new_order(order)
            notified_ids.add(order["id"])
            time.sleep(0.5)

    state["notified_ids"] = list(notified_ids)
    save_state(state)
    print("[*] 完成")


if __name__ == "__main__":
    main()
