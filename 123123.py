import os
import sqlite3
import random
import string
import requests
import time
import datetime
import nest_asyncio
nest_asyncio.apply()

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

API_TOKEN = "7022711443:AAG2kU-TWDskXqFxCjap1DGw2jjji2HE2Ac"
TELEGRAM_USER_ID = 7550813603
PORT_MIN = 1080
PORT_MAX = 60000
DEFAULT_USER = "vtoan"

conn = sqlite3.connect("proxy.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ipv6 TEXT,
    port INTEGER,
    user TEXT,
    pass TEXT,
    created_date TEXT,
    status TEXT
)
""")
conn.commit()

def generate_password():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=6))

def generate_port():
    while True:
        port = random.randint(PORT_MIN, PORT_MAX)
        cursor.execute("SELECT * FROM proxies WHERE port=? AND status IN ('active','waiting')", (port,))
        if not cursor.fetchone():
            return port

def generate_ipv6(prefix):
    suffix = ":".join(''.join(random.choices("0123456789abcdef", k=4)) for _ in range(4))
    return prefix.split("::")[0] + "::" + suffix

def add_ipv6_linux(ipv6, interface="ens3"):
    print(f"[+] Adding IPv6: {ipv6}")
    os.system(f"ip -6 addr add {ipv6}/64 dev {interface}")

def remove_ipv6_linux(ipv6, interface="ens3"):
    print(f"[-] Removing IPv6: {ipv6}")
    os.system(f"ip -6 addr del {ipv6}/64 dev {interface}")

def get_public_ipv4():
    return requests.get("https://api.ipify.org").text

def update_3proxy_config():
    cursor.execute("SELECT ipv6, port, user, pass FROM proxies WHERE status IN ('active','waiting')")
    rows = cursor.fetchall()
    config = "nscache 65536\n\n"
    config += "setgid 65535\nsetuid 65535\n\n"  # run as nobody
    config += "flush\n"

    for row in rows:
        ipv6, port, user, password = row
        config += f"auth strong\n"
        config += f"users {user}:CL:{password}\n"
        config += f"allow {user}\n"
        config += f"proxy -6 -n -a -p{port} -i{get_public_ipv4()} -e{ipv6}\n"
        config += "flush\n"

    with open("/etc/3proxy/3proxy.cfg", "w") as f:
        f.write(config)

    # Restart 3proxy
    os.system("pkill 3proxy")
    os.system("3proxy /etc/3proxy/3proxy.cfg &")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Proxy Manager Bot Linux đang hoạt động.")

async def new_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    count = 1
    if context.args:
        count = int(context.args[0])
    proxies = []
    for _ in range(count):
        port = generate_port()
        password = generate_password()
        ipv6 = generate_ipv6(context.bot_data["prefix_ipv6"])
        add_ipv6_linux(ipv6, context.bot_data["interface"])
        now = datetime.datetime.now().strftime("%Y-%m-%d")
        cursor.execute("INSERT INTO proxies (ipv6, port, user, pass, created_date, status) VALUES (?, ?, ?, ?, ?, ?)",
                       (ipv6, port, DEFAULT_USER, password, now, "waiting"))
        conn.commit()
        proxies.append(f"{context.bot_data['ipv4']}:{port}:{DEFAULT_USER}:{password}")

    update_3proxy_config()

    filename = f"proxy_new_{int(time.time())}.txt"
    with open(filename, "w") as f:
        for proxy in proxies:
            f.write(proxy + "\n")

    await context.bot.send_document(chat_id=update.effective_chat.id, document=open(filename, "rb"))
    os.remove(filename)

async def del_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Vui lòng nhập IPv6 để xoá.")
        return
    ipv6 = context.args[0]
    cursor.execute("SELECT port FROM proxies WHERE ipv6=?", (ipv6,))
    result = cursor.fetchone()
    if result:
        remove_ipv6_linux(ipv6, context.bot_data["interface"])
        cursor.execute("UPDATE proxies SET status='deleted' WHERE ipv6=?", (ipv6,))
        conn.commit()
        update_3proxy_config()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Đã xoá proxy {ipv6}.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Proxy không tồn tại.")

async def list_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except:
            page = 1
    limit = 50
    offset = (page - 1) * limit
    cursor.execute("SELECT ipv6, port, user, pass, status FROM proxies WHERE status IN ('active','waiting') LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    if rows:
        msg = f"✅ Proxy page {page}:\n"
        for row in rows:
            msg += f"{row[0]}:{row[1]}:{row[2]}:{row[3]} ({row[4]})\n"
        msg += "\nDùng /list {page_number} để xem trang khác."
    else:
        msg = "❌ Không có proxy trên trang này."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

async def main():
    prefix_ipv6 = input("Nhập prefix IPv6 của bạn (vd: 2001:ee0:48e5:f850::/64): ").strip()
    interface = input("Nhập tên interface network (vd: ens3, eth0): ").strip()
    ipv4 = get_public_ipv4()
    print(f"[+] Public IPv4 của bạn: {ipv4}")

    application = ApplicationBuilder().token(API_TOKEN).build()
    application.bot_data["prefix_ipv6"] = prefix_ipv6
    application.bot_data["ipv4"] = ipv4
    application.bot_data["interface"] = interface

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_proxy))
    application.add_handler(CommandHandler("del", del_proxy))
    application.add_handler(CommandHandler("list", list_proxy))

    await application.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.get_event_loop().run_until_complete(main())
