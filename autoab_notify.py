"""
autoab.net 订单通知监控脚本
精简版：只推送金额、时间、导航链接
"""
import os
import json
import sys
import time
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


def notify_new_order(order: dict) -> bool:
    amount = order.get("order_amount", "?")
    order_time = order.get("order_time", "?")
    match_type = order.get("match_type", "?")
    match_mode = order.get("match_mode", "?")
    from_loc = order.get("from_location", "")
    to_loc = order.get("to_location", "")

    mode_map = {"to": "去机场", "from": "从机场出发", "fare": "一口价"}
    mode_cn = mode_map.get(match_mode, match_mode)

    lines = [f"🚗 <b>新订单提醒</b> @rain1203"]
    lines.append(f"💰 金额：<b>{amount} 马币</b>")
    lines.append(f"🕐 {order_time}")
    lines.append(f"🚘 {match_type.upper()} | {mode_cn}")
    lines.append(f"📍 上车：{from_loc}")
    lines.append(f"🏁 下车：{to_loc}")

    # Google Maps 导航到上车点（from = 当前位置，to = 上车点）
    dest = f"{from_loc}, Malaysia" if from_loc else ""
    maps_url = f"https://www.google.com/maps/dir/?api=1&origin=My+Location&destination={urllib.parse.quote(dest)}"
    lines.append(f"🗺️ <a href=\"{maps_url}\">导航到上车点</a>")

    return send_telegram("\n".join(lines))


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
