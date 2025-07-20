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
API_TOKEN = "7022711443:AAG2kU-TWDskXqFxCjap1DGw2jjji2HE2Ac"  # Thay bằng token bot Telegram của bạn
TELEGRAM_USER_ID = 7550813603  # Thay bằng ID người dùng Telegram của bạn
PORT_MIN = 20000
PORT_MAX = 60000
DEFAULT_USER = "vtoan"
PROXY_TTL_DAYS = 30
DB_NAME = "proxy.db"
DEFAULT_INTERFACE = "eth0"
CONFIG_PATH = "/etc/3proxy/3proxy.cfg"

# Database setup
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
cursor = conn.cursor()

# Create table if not exists
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

def get_ipv6_prefix(interface="eth0"):
    """Lấy prefix IPv6 từ giao diện mạng"""
    try:
        result = subprocess.run(["ip", "-6", "addr", "show", interface], capture_output=True, text=True, check=True)
        lines = result.stdout.splitlines()
        for line in lines:
            if "inet6" in line and "scope global" in line:
                addr_part = line.strip().split()[1]
                ip, prefix_len = addr_part.split("/")
                # Lấy 64 bit đầu tiên của địa chỉ IPv6
                try:
                    network = IPv6Network(f"{ip}/64", strict=False)
                    return str(network)
                except ValueError:
                    print(f"[!] Địa chỉ IPv6 {ip} không hợp lệ để tạo prefix")
        print(f"[!] Không tìm thấy địa chỉ IPv6 hợp lệ trên giao diện {interface}")
        return None
    except subprocess.CalledProcessError as e:
        print(f"[!] Lỗi khi lấy prefix IPv6: {e}")
        return None

def check_3proxy_status():
    """Kiểm tra trạng thái 3proxy"""
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, check=True)
        if "3proxy" in result.stdout:
            return True
        return False
    except subprocess.CalledProcessError as e:
        print(f"[!] Lỗi khi kiểm tra trạng thái 3proxy: {e}")
        return False

def check_ipv6_exists(ipv6, interface="eth0"):
    """Kiểm tra xem địa chỉ IPv6 đã được thêm vào giao diện chưa"""
    try:
        result = subprocess.run(["ip", "-6", "addr", "show", interface], capture_output=True, text=True, check=True)
        return ipv6 in result.stdout
    except subprocess.CalledProcessError as e:
        print(f"[!] Lỗi khi kiểm tra IPv6 {ipv6}: {e}")
        return False

def ensure_config_file():
    """Đảm bảo CONFIG_PATH là file, không phải thư mục"""
    if os.path.isdir(CONFIG_PATH):
        print(f"[!] {CONFIG_PATH} là thư mục, đang xóa...")
        subprocess.run(["rm", "-rf", CONFIG_PATH], check=True)
    if not os.path.exists(CONFIG_PATH):
        print(f"[+] Tạo file cấu hình {CONFIG_PATH}")
        subprocess.run(["touch", CONFIG_PATH], check=True)
        subprocess.run(["chmod", "644", CONFIG_PATH], check=True)
        subprocess.run(["chown", "nobody:nogroup", CONFIG_PATH], check=True)

def generate_password():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=6))

def generate_port():
    while True:
        port = random.randint(PORT_MIN, PORT_MAX)
        cursor.execute("SELECT port FROM proxies WHERE port=? AND status IN ('active','waiting')", (port,))
        if not cursor.fetchone():
            try:
                result = subprocess.run(["netstat", "-tulnp"], capture_output=True, text=True, check=False)
                if f":{port}" not in result.stdout:
                    return port
            except subprocess.CalledProcessError:
                return port
    return port

def generate_ipv6(prefix):
    try:
        network = IPv6Network(prefix, strict=False)
        random_addr = IPv6Address(network.network_address + random.randint(0, network.num_addresses - 1))
        return str(random_addr)
    except ValueError as e:
        print(f"[!] Prefix IPv6 không hợp lệ: {e}")
        return None

def add_ipv6(ipv6, interface="eth0"):
    system = platform.system()
    if system == "Linux":
        try:
            subprocess.run(["ip", "-6", "addr", "add", f"{ipv6}/128", "dev", interface], check=True)
            print(f"[+] Đã thêm IPv6: {ipv6}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[!] Lỗi khi thêm IPv6 {ipv6}: {e}")
            return False
    return False

def remove_ipv6(ipv6, interface="eth0"):
    system = platform.system()
    if system == "Linux":
        try:
            subprocess.run(["ip", "-6", "addr", "del", f"{ipv6}/128", "dev", interface], check=True)
            print(f"[-] Đã xóa IPv6: {ipv6}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[!] Lỗi khi xóa IPv6 {ipv6}: {e}")
            return False
    return False

def get_public_ipv4():
    try:
        return requests.get("https://api.ipify.org", timeout=5).text
    except requests.RequestException as e:
        print(f"[!] Lỗi khi lấy public IPv4: {e}")
        return "127.0.0.1"  # Fallback

def update_3proxy_config(ipv4, interface):
    ensure_config_file()
    cursor.execute("SELECT ipv6, port, user, pass FROM proxies WHERE status IN ('active','waiting')")
    rows = cursor.fetchall()
    config = "daemon\nnscache 65536\nnserver 8.8.8.8\nnserver [2001:4860:4860::8888]\nsetgid 65535\nsetuid 65535\nlog /var/log/3proxy.log D\n\n"
    
    for row in rows:
        ipv6, port, user, password = row
        if check_ipv6_exists(ipv6, interface):
            config += f"auth strong\n"
            config += f"users {user}:CL:{password}\n"
            config += f"allow {user}\n"
            config += f"proxy -6 -n -a -p{port} -i{ipv4} -e{ipv6}\n"
            config += "flush\n"
        else:
            print(f"[!] Bỏ qua proxy với IPv6 {ipv6} vì không tồn tại trên {interface}")

    try:
        with open(CONFIG_PATH, "w") as f:
            f.write(config)
        subprocess.run(["chown", "nobody:nogroup", CONFIG_PATH], check=True)
        subprocess.run(["chmod", "644", CONFIG_PATH], check=True)
        # Restart 3proxy
        subprocess.run(["pkill", "-f", "3proxy"], check=False)
        result = subprocess.run(["3proxy", CONFIG_PATH], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[!] Lỗi khi khởi động 3proxy: {result.stderr}")
            raise Exception(f"3proxy failed to start: {result.stderr}")
        print("[+] Đã cập nhật và khởi động lại cấu hình 3proxy")
        if not check_3proxy_status():
            print("[!] Cảnh báo: 3proxy không chạy sau khi khởi động lại")
    except Exception as e:
        print(f"[!] Lỗi khi cập nhật cấu hình 3proxy: {e}")

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
                    print(f"[+] Đã hết hạn proxy {ipv6}")
            except ValueError as e:
                print(f"[!] Lỗi định dạng ngày cho proxy {ipv6}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Proxy Manager Bot đang hoạt động.")

async def new_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    try:
        count = min(int(context.args[0]) if context.args else 1, 2000)
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Số lượng proxy phải là số nguyên.")
        return
    proxies = []
    prefix = context.bot_data.get("prefix_ipv6")
    interface = context.bot_data.get("interface", "eth0")
    ipv4 = context.bot_data.get("ipv4")

    try:
        check_expired_proxies()
    except Exception as e:
        print(f"[!] Lỗi khi kiểm tra proxy hết hạn: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Lỗi khi kiểm tra proxy hết hạn.")

    for _ in range(count):
        port = generate_port()
        password = generate_password()
        ipv6 = generate_ipv6(prefix) if prefix else None
        if not ipv6:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Prefix IPv6 không hợp lệ.")
            return
        if add_ipv6(ipv6, interface) and check_ipv6_exists(ipv6, interface):
            now = datetime.datetime.now().strftime("%Y-%m-%d")
            try:
                cursor.execute("INSERT OR IGNORE INTO proxies (ipv6, port, user, pass, created_date, status) VALUES (?, ?, ?, ?, ?, ?)",
                              (ipv6, port, DEFAULT_USER, password, now, "waiting"))
                conn.commit()
                proxies.append(f"{ipv4}:{port}:{DEFAULT_USER}:{password}")
            except sqlite3.Error as e:
                print(f"[!] Lỗi khi chèn proxy vào cơ sở dữ liệu: {e}")
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Lỗi khi tạo proxy {ipv6}: {e}")
                continue
        else:
            print(f"[!] Không thể thêm IPv6 {ipv6} vào {interface}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Không thể thêm IPv6 {ipv6}")

    if proxies:
        try:
            update_3proxy_config(ipv4, interface)
            filename = f"proxy_new_{int(time.time())}.txt"
            with open(filename, "w") as f:
                for proxy in proxies:
                    f.write(proxy + "\n")
            await context.bot.send_document(chat_id=update.effective_chat.id, document=open(filename, "rb"))
            os.remove(filename)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Đã tạo {len(proxies)} proxy.")
            if not check_3proxy_status():
                await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Cảnh báo: 3proxy không chạy. Vui lòng kiểm tra trạng thái dịch vụ.")
        except Exception as e:
            print(f"[!] Lỗi khi cập nhật 3proxy hoặc gửi file: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Lỗi khi tạo file proxy hoặc cập nhật 3proxy: {e}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Không tạo được proxy nào.")

async def del_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Vui lòng cung cấp IPv6 hoặc 'all' để xóa.")
        return
    interface = context.bot_data.get("interface", "eth0")
    ipv4 = context.bot_data.get("ipv4")

    try:
        if context.args[0].lower() == "all":
            cursor.execute("SELECT ipv6 FROM proxies WHERE status IN ('active','waiting')")
            for (ipv6,) in cursor.fetchall():
                remove_ipv6(ipv6, interface)
            cursor.execute("UPDATE proxies SET status='deleted' WHERE status IN ('active','waiting')")
            conn.commit()
            update_3proxy_config(ipv4, interface)
            await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Đã xóa tất cả proxy.")
        else:
            ipv6 = context.args[0]
            cursor.execute("SELECT port FROM proxies WHERE ipv6=? AND status IN ('active','waiting')", (ipv6,))
            if cursor.fetchone():
                remove_ipv6(ipv6, interface)
                cursor.execute("UPDATE proxies SET status='deleted' WHERE ipv6=?", (ipv6,))
                conn.commit()
                update_3proxy_config(ipv4, interface)
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Đã xóa proxy {ipv6}.")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Proxy không tồn tại.")
    except Exception as e:
        print(f"[!] Lỗi khi xóa proxy: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Lỗi khi xóa proxy: {e}")

async def list_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    try:
        page = max(1, int(context.args[0]) if context.args else 1)
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Số trang phải là số nguyên.")
        return
    limit = 50
    offset = (page - 1) * limit
    try:
        cursor.execute("SELECT ipv6, port, user, pass, status FROM proxies WHERE status IN ('active','waiting') LIMIT ? OFFSET ?", (limit, offset))
        rows = cursor.fetchall()
        ipv4 = context.bot_data.get("ipv4")
        
        if rows:
            msg = f"✅ Danh sách proxy trang {page}:\n"
            for row in rows:
                ipv6, port, user, pass_, status = row
                msg += f"{ipv4}:{port}:{user}:{pass_} ({status})\n"
            msg += f"\nSử dụng /list {{số_trang}} để xem trang khác."
        else:
            msg = "❌ Không có proxy nào trên trang này."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
    except Exception as e:
        print(f"[!] Lỗi khi liệt kê proxy: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Lỗi khi liệt kê proxy.")

async def main():
    system = platform.system()
    if system != "Linux":
        print("[!] Script chỉ hỗ trợ Linux (Ubuntu).")
        return

    ensure_config_file()
    prefix_ipv6 = get_ipv6_prefix(DEFAULT_INTERFACE)
    if not prefix_ipv6:
        while True:
            prefix_ipv6 = input("Không tìm thấy prefix IPv6 tự động. Nhập thủ công (ví dụ: 2401:2420:0:101e::/64): ").strip()
            try:
                IPv6Network(prefix_ipv6, strict=False)
                if not prefix_ipv6.endswith("/64"):
                    prefix_ipv6 += "/64"
                    print(f"[+] Đã thêm /64 vào prefix: {prefix_ipv6}")
                break
            except ValueError as e:
                print(f"[!] Prefix IPv6 không hợp lệ: {e}")
    else:
        print(f"[+] Đã tìm thấy prefix IPv6: {prefix_ipv6}")
    
    interface = input(f"Nhập giao diện mạng (mặc định: {DEFAULT_INTERFACE}): ").strip() or DEFAULT_INTERFACE
    ipv4 = get_public_ipv4()
    print(f"[+] Public IPv4: {ipv4}")

    try:
        application = ApplicationBuilder().token(API_TOKEN).build()
    except Exception as e:
        print(f"[!] Không thể khởi tạo bot Telegram: {e}")
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
            try:
                check_expired_proxies()
                update_3proxy_config(ipv4, interface)
            except Exception as e:
                print(f"[!] Lỗi trong công việc dọn dẹp định kỳ: {e}")
        application.job_queue.run_repeating(cleanup_job, interval=86400)  # Chạy hàng ngày
        print("[+] Công việc dọn dẹp định kỳ đã được lên lịch.")
    else:
        print("[!] JobQueue không khả dụng. Cài đặt 'python-telegram-bot[job-queue]' để bật dọn dẹp định kỳ. Sử dụng /del để dọn dẹp thủ công.")

    await application.run_polling()

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[!] Lỗi nghiêm trọng: {e}")
