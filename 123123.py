import os
import sqlite3
import random
import string
import requests
import time
import datetime
import subprocess
import platform
import nest_asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from ipaddress import IPv6Address, IPv6Network

nest_asyncio.apply()

# Configuration
API_TOKEN = "7022711443:AAG2kU-TWDskXqFxCjap1DGw2jjji2HE2Ac"  # Replace with your Telegram bot token
TELEGRAM_USER_ID = 7550813603  # Replace with your Telegram user ID
PORT_MIN = 1080
PORT_MAX = 60000
DEFAULT_USER = "vtoan"
PROXY_TTL_DAYS = 30
DB_NAME = "proxy.db"

# Database setup
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ipv6 TEXT UNIQUE,
    port INTEGER UNIQUE,
    user TEXT,
    pass TEXT,
    created_date TEXT,
    last_used_date TEXT,
    status TEXT
)
""")
conn.commit()

def generate_password():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=6))

def generate_port():
    while True:
        port = random.randint(PORT_MIN, PORT_MAX)
        cursor.execute("SELECT port FROM proxies WHERE port=? AND status IN ('active','waiting')", (port,))
        if not cursor.fetchone():
            return port

def generate_ipv6(prefix):
    try:
        network = IPv6Network(prefix, strict=False)
        random_addr = IPv6Address(network.network_address + random.randint(0, network.num_addresses - 1))
        return str(random_addr)
    except ValueError as e:
        print(f"[!] Invalid IPv6 prefix: {e}")
        return None

def add_ipv6(ipv6, interface="eth0"):
    system = platform.system()
    if system == "Linux":
        try:
            subprocess.run(["ip", "-6", "addr", "add", f"{ipv6}/128", "dev", interface], check=True)
            print(f"[+] Added IPv6: {ipv6}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[!] Failed to add IPv6 {ipv6}: {e}")
            return False
    elif system == "Windows":
        try:
            subprocess.run(["netsh", "interface", "ipv6", "add", "address", f"interface={interface}", f"address={ipv6}"], check=True)
            print(f"[+] Added IPv6: {ipv6}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[!] Failed to add IPv6 {ipv6}: {e}")
            return False
    return False

def remove_ipv6(ipv6, interface="eth0"):
    system = platform.system()
    if system == "Linux":
        try:
            subprocess.run(["ip", "-6", "addr", "del", f"{ipv6}/128", "dev", interface], check=True)
            print(f"[-] Removed IPv6: {ipv6}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[!] Failed to remove IPv6 {ipv6}: {e}")
            return False
    elif system == "Windows":
        try:
            subprocess.run(["netsh", "interface", "ipv6", "delete", "address", f"interface={interface}", f"address={ipv6}"], check=True)
            print(f"[-] Removed IPv6: {ipv6}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[!] Failed to remove IPv6 {ipv6}: {e}")
            return False
    return False

def get_public_ipv4():
    try:
        return requests.get("https://api.ipify.org", timeout=5).text
    except requests.RequestException as e:
        print(f"[!] Failed to get public IPv4: {e}")
        return "127.0.0.1"  # Fallback

def update_3proxy_config(ipv4, interface):
    cursor.execute("SELECT ipv6, port, user, pass FROM proxies WHERE status IN ('active','waiting')")
    rows = cursor.fetchall()
    config = "nscache 65536\nnserver 8.8.8.8\nnserver [2001:4860:4860::8888]\nsetgid 65535\nsetuid 65535\n\n"
    
    for row in rows:
        ipv6, port, user, password = row
        config += f"auth strong\n"
        config += f"users {user}:CL:{password}\n"
        config += f"allow {user}\n"
        config += f"proxy -6 -n -a -p{port} -i{ipv4} -e{ipv6}\n"
        config += "flush\n"

    config_path = "/etc/3proxy/3proxy.cfg" if platform.system() == "Linux" else "3proxy.cfg"
    try:
        with open(config_path, "w") as f:
            f.write(config)
        # Restart 3proxy
        system = platform.system()
        if system == "Linux":
            subprocess.run(["pkill", "3proxy"], check=False)
            subprocess.run(["3proxy", config_path], check=True)
        elif system == "Windows":
            subprocess.run(["taskkill", "/IM", "3proxy.exe", "/F"], shell=True, check=False)
            subprocess.run(["3proxy.exe", config_path], shell=True, check=True)
        print("[+] 3proxy configuration updated and restarted")
    except Exception as e:
        print(f"[!] Failed to update 3proxy config: {e}")

def check_expired_proxies():
    cursor.execute("SELECT ipv6, last_used_date FROM proxies WHERE status='active'")
    rows = cursor.fetchall()
    current_date = datetime.datetime.now()
    
    for ipv6, last_used in rows:
        if last_used:
            try:
                last_used_date = datetime.datetime.strptime(last_used, "%Y-%m-%d")
                if (current_date - last_used_date).days >= PROXY_TTL_DAYS:
                    remove_ipv6(ipv6)
                    cursor.execute("UPDATE proxies SET status='expired' WHERE ipv6=?", (ipv6,))
                    conn.commit()
                    print(f"[+] Expired proxy {ipv6}")
            except ValueError as e:
                print(f"[!] Invalid date format for proxy {ipv6}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Proxy Manager Bot is running.")

async def new_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    count = min(int(context.args[0]) if context.args else 1, 2000)
    proxies = []
    prefix = context.bot_data.get("prefix_ipv6")
    interface = context.bot_data.get("interface", "eth0")
    ipv4 = context.bot_data.get("ipv4")

    check_expired_proxies()

    for _ in range(count):
        port = generate_port()
        password = generate_password()
        ipv6 = generate_ipv6(prefix) if prefix else None
        if not ipv6:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Invalid IPv6 prefix.")
            return
        if add_ipv6(ipv6, interface):
            now = datetime.datetime.now().strftime("%Y-%m-%d")
            cursor.execute("INSERT OR IGNORE INTO proxies (ipv6, port, user, pass, created_date, status) VALUES (?, ?, ?, ?, ?, ?)",
                          (ipv6, port, DEFAULT_USER, password, now, "waiting"))
            conn.commit()
            proxies.append(f"{ipv4}:{port}:{DEFAULT_USER}:{password}")

    if proxies:
        update_3proxy_config(ipv4, interface)
        filename = f"proxy_new_{int(time.time())}.txt"
        with open(filename, "w") as f:
            for proxy in proxies:
                f.write(proxy + "\n")
        await context.bot.send_document(chat_id=update.effective_chat.id, document=open(filename, "rb"))
        os.remove(filename)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Created {len(proxies)} proxies.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Failed to create proxies.")

async def del_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Please provide IPv6 or 'all' to delete.")
        return
    interface = context.bot_data.get("interface", "eth0")
    ipv4 = context.bot_data.get("ipv4")

    if context.args[0].lower() == "all":
        cursor.execute("SELECT ipv6 FROM proxies WHERE status IN ('active','waiting')")
        for (ipv6,) in cursor.fetchall():
            remove_ipv6(ipv6, interface)
        cursor.execute("UPDATE proxies SET status='deleted' WHERE status IN ('active','waiting')")
        conn.commit()
        update_3proxy_config(ipv4, interface)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ All proxies deleted.")
    else:
        ipv6 = context.args[0]
        cursor.execute("SELECT port FROM proxies WHERE ipv6=? AND status IN ('active','waiting')", (ipv6,))
        if cursor.fetchone():
            remove_ipv6(ipv6, interface)
            cursor.execute("UPDATE proxies SET status='deleted' WHERE ipv6=?", (ipv6,))
            conn.commit()
            update_3proxy_config(ipv4, interface)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Deleted proxy {ipv6}.")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Proxy not found.")

async def list_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    page = max(1, int(context.args[0]) if context.args else 1)
    limit = 50
    offset = (page - 1) * limit
    cursor.execute("SELECT ipv6, port, user, pass, status FROM proxies WHERE status IN ('active','waiting') LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    ipv4 = context.bot_data.get("ipv4")
    
    if rows:
        msg = f"✅ Proxy page {page}:\n"
        for row in rows:
            ipv6, port, user, pass_, status = row
            msg += f"{ipv4}:{port}:{user}:{pass_} ({status})\n"
        msg += f"\nUse /list {{page_number}} to view other pages."
    else:
        msg = "❌ No proxies found on this page."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

async def main():
    system = platform.system()
    prefix_ipv6 = input("Enter your IPv6 prefix (e.g., 2401:2420:0:101e::/64): ").strip()
    if not prefix_ipv6.endswith("/64"):
        prefix_ipv6 += "/64"
        print(f"[+] Appended /64 to prefix: {prefix_ipv6}")
    interface = input("Enter network interface (e.g., eth0 for Linux, Ethernet for Windows): ").strip()
    ipv4 = get_public_ipv4()
    print(f"[+] Public IPv4: {ipv4}")

    try:
        application = ApplicationBuilder().token(API_TOKEN).build()
    except Exception as e:
        print(f"[!] Failed to initialize Telegram bot: {e}")
        return

    application.bot_data["prefix_ipv6"] = prefix_ipv6
    application.bot_data["ipv4"] = ipv4
    application.bot_data["interface"] = interface

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_proxy))
    application.add_handler(CommandHandler("del", del_proxy))
    application.add_handler(CommandHandler("list", list_proxy))

    # Periodic cleanup of expired proxies
    if application.job_queue:
        async def cleanup_job(context):
            check_expired_proxies()
            update_3proxy_config(ipv4, interface)
        application.job_queue.run_repeating(cleanup_job, interval=86400)  # Run daily
        print("[+] Periodic cleanup job scheduled.")
    else:
        print("[!] JobQueue not available. Install 'python-telegram-bot[job-queue]' to enable periodic cleanup. Use /del to manually clean up expired proxies.")

    await application.run_polling()

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[!] Fatal error: {e}")
