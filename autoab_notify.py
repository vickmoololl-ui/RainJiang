"""
autoab.net 订单通知监控脚本
带 session 持久化，减少重复登录
"""
import os
import json
import sys
import time
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
    from_loc = order.get("from_location", "?")
    to_loc = order.get("to_location", "?")
    remark = order.get("remark", "")
    mode_map = {"to": "去机场", "from": "从机场出发", "fare": "一口价"}
    mode_cn = mode_map.get(match_mode, match_mode)
    message = (
        f"🚗 <b>新订单提醒</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 金额：<b>{amount} 马币</b>\n"
        f"🕐 时间：{order_time}\n"
        f"🚘 类型：{match_type.upper()} | {mode_cn}\n"
        f"📍 上车：{from_loc}\n"
        f"🏁 下车：{to_loc}\n"
        f"\n{remark}"
    )
    return send_telegram(message)


def login_and_get_session() -> requests.Session:
    """登录并返回带 cookie 的 session"""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    })
    login_data = {
        "username": CONFIG["autoab_username"],
        "password": CONFIG["autoab_password"],
        "keeptime": "31536000",
    }
    resp = session.post(LOGIN_URL, data=login_data, timeout=15)
    data = resp.json()
    if data.get("code") != 1:
        raise Exception(f"登录失败: {data.get('msg', '未知错误')}")
    print(f"[+] 登录成功: {data['data']['userinfo']['username']}")
    return session


def try_saved_session(session: requests.Session) -> bool:
    """尝试用已有的 session 访问 profile，成功返回 True"""
    try:
        resp = session.get(PROFILE_URL, timeout=10)
        data = resp.json()
        if data.get("code") == 1:
            print(f"[+] 使用已有 session（无需登录）")
            return True
        return False
    except Exception:
        return False


def load_state() -> dict:
    """加载完整状态（含 cookie）"""
    default = {"notified_ids": [], "phpsessid": None}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {**default, **state}
        except Exception as e:
            print(f"[!] 状态文件读取失败: {e}")
    return default


def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )


def poll_orders(session) -> dict:
    grabid = CONFIG["autoab_grabid"]
    resp = session.get(POLL_URL, params={"grabid": grabid}, timeout=15)
    data = resp.json()
    if data.get("code") != 1:
        print(f"[x] 轮询失败: {data.get('msg', '未知错误')}")
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

    # 加载完整状态
    state = load_state()
    notified_ids = set(state.get("notified_ids", []))
    saved_phpsessid = state.get("phpsessid")
    print(f"[*] 已通知订单数: {len(notified_ids)}")

    # 尝试用已有 cookie
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    })
    session_ok = False

    if saved_phpsessid:
        # 设置保存的 cookie
        session.cookies.set("PHPSESSID", saved_phpsessid, domain="www.autoab.net", path="/")
        if try_saved_session(session):
            session_ok = True
        else:
            print("[*] 已有 session 已过期，重新登录")

    if not session_ok:
        # 重新登录
        session = login_and_get_session()
        # 保存新的 PHPSESSID
        for cookie in session.cookies:
            if cookie.name == "PHPSESSID":
                state["phpsessid"] = cookie.value
                print(f"[+] 保存新 session: {cookie.value[:20]}...")
                break

    # 轮询
    data = poll_orders(session)
    orders = data.get("list", [])

    if not orders:
        print("[*] 没有新订单")
    else:
        new_orders = [o for o in orders if o["id"] not in notified_ids]
        print(f"[*] 获取 {len(orders)} 条，新 {len(new_orders)} 条")
        for order in new_orders:
            print(f"  -> 订单 #{order['id']}: {order['order_amount']} 马币")
            notify_new_order(order)
            notified_ids.add(order["id"])
            time.sleep(0.5)

    # 保存状态
    state["notified_ids"] = list(notified_ids)
    save_state(state)
    print(f"[*] 完成")


if __name__ == "__main__":
    main()
