"""
autoab.net 订单通知监控脚本
定时检测新订单并通过 Telegram 推送通知
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

# ============================================================
# 配置（通过环境变量传入，无敏感默认值）
# ============================================================
CONFIG = {
    "autoab_username": os.environ.get("AUTOAB_USERNAME", ""),
    "autoab_password": os.environ.get("AUTOAB_PASSWORD", ""),
    "autoab_grabid": os.environ.get("AUTOAB_GRABID", ""),
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
}

BASE_URL = "https://www.autoab.net/index.php/api"
LOGIN_URL = f"{BASE_URL}/user/login"
POLL_URL = f"{BASE_URL}/grab/poll_orders"
STATE_FILE = Path(__file__).parent / "state.json"


def send_telegram(message: str) -> bool:
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        print("[!] Telegram 未配置，跳过推送")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
        data = resp.json()
        return data.get("ok", False)
    except Exception as e:
        print(f"[x] Telegram 请求异常: {e}")
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


def create_session():
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


def poll_orders(session) -> dict:
    grabid = CONFIG["autoab_grabid"]
    resp = session.get(POLL_URL, params={"grabid": grabid}, timeout=15)
    data = resp.json()
    if data.get("code") != 1:
        print(f"[x] 轮询失败: {data.get('msg', '未知错误')}")
        return {"list": []}
    return data["data"]


def load_state() -> set:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(state.get("notified_ids", []))
        except Exception as e:
            print(f"[!] 状态文件读取失败: {e}")
    return set()


def save_state(notified_ids: set):
    STATE_FILE.write_text(
        json.dumps({"notified_ids": list(notified_ids)}, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    print(f"[*] 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[*] 目标 GrabID: {CONFIG['autoab_grabid']}")
    if not CONFIG["telegram_bot_token"] or not CONFIG["telegram_chat_id"]:
        print("[!] 请设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID")
        return
    try:
        session = create_session()
    except Exception as e:
        print(f"[x] {e}")
        send_telegram(f"❌ <b>autoab 通知出错</b>\n登录失败: {e}")
        return
    notified_ids = load_state()
    print(f"[*] 已通知订单数: {len(notified_ids)}")
    data = poll_orders(session)
    orders = data.get("list", [])
    if not orders:
        print("[*] 没有新订单")
        return
    new_orders = [o for o in orders if o["id"] not in notified_ids]
    print(f"[*] 本次获取 {len(orders)} 条，新订单 {len(new_orders)} 条")
    for order in new_orders:
        print(f"  -> 订单 #{order['id']}: {order['order_amount']} 马币")
        notify_new_order(order)
        notified_ids.add(order["id"])
        time.sleep(0.5)
    save_state(notified_ids)
    print(f"[*] 完成，已通知 ID 数: {len(notified_ids)}")
    if new_orders:
        print(f"[+] 成功推送 {len(new_orders)} 条新订单")
    else:
        print("[*] 无新订单")


if __name__ == "__main__":
    main()
