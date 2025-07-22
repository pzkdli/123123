import os
import json
import random
import string
import subprocess
import datetime
import telebot
from threading import Lock

# --- ⚙️ CẤU HÌNH HỆ THỐNG ---
BOT_TOKEN = "7022711443:AAHPixbTjnocW3LWgpW6gsGep-mCScOzJvM"
ADMIN_ID = 7550813603
IPV6_SUBNET = "2401:2420:0:102f"
NETWORK_INTERFACE = "eth0"
PROXY_USERNAME = "vtoan5516"
PORT_RANGE_START = 10000
PORT_RANGE_END = 60000
PROXY_LIFETIME_DAYS = 30
REGENERATE_THRESHOLD = 200 # Số proxy hết hạn để kích hoạt tạo mới

# --- 📂 ĐƯỜNG DẪN FILE ---
DATA_DIR = "/opt/proxy_manager"
PROXY_DATA_FILE = os.path.join(DATA_DIR, "proxy_data.json")
PROXY_CONFIG_FILE = "/etc/3proxy/3proxy.cfg"
LOG_FILE = "/var/log/3proxy/3proxy.log"

# Khởi tạo bot và Lock để tránh xung đột dữ liệu
bot = telebot.TeleBot(BOT_TOKEN)
data_lock = Lock()

# --- 🕵️ HÀM KIỂM TRA ADMIN ---
def is_admin(message):
    return message.from_user.id == ADMIN_ID

# --- 📦 HÀM QUẢN LÝ DỮ LIỆU PROXY (JSON) ---
def load_proxy_data():
    with data_lock:
        if not os.path.exists(PROXY_DATA_FILE):
            return {"proxies": [], "used_ports": []}
        try:
            with open(PROXY_DATA_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"proxies": [], "used_ports": []}

def save_proxy_data(data):
    with data_lock:
        with open(PROXY_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)

# --- 🌍 HÀM MẠNG & HỆ THỐNG ---
def get_public_ipv4():
    try:
        # Lấy IP public của VPS
        result = subprocess.run(['curl', '-s', 'ifconfig.me'], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception as e:
        print(f"Lỗi khi lấy IPv4 public: {e}")
        return "127.0.0.1" # IP dự phòng nếu lỗi

PUBLIC_IPV4 = get_public_ipv4()

def generate_random_ipv6():
    # Tạo ngẫu nhiên 4 khối cuối của địa chỉ IPv6
    return f"{IPV6_SUBNET}:{random.randint(0, 0xffff):04x}:{random.randint(0, 0xffff):04x}:{random.randint(0, 0xffff):04x}:{random.randint(0, 0xffff):04x}"

def generate_random_password(length=2):
    # Tạo mật khẩu ngẫu nhiên 2 chữ cái thường
    return ''.join(random.choice(string.ascii_lowercase) for _ in range(length))

def update_system_ips(proxies_to_add, proxies_to_remove):
    print(f"Adding {len(proxies_to_add)} IPs, Removing {len(proxies_to_remove)} IPs.")
    # Chạy lệnh hệ thống để xóa IP cũ
    for proxy in proxies_to_remove:
        subprocess.run(['ip', '-6', 'addr', 'del', f"{proxy['ipv6']}/128", 'dev', NETWORK_INTERFACE], capture_output=True)
    
    # Chạy lệnh hệ thống để thêm IP mới
    for proxy in proxies_to_add:
        subprocess.run(['ip', '-6', 'addr', 'add', f"{proxy['ipv6']}/128", 'dev', NETWORK_INTERFACE], capture_output=True)

def generate_3proxy_config_and_reload():
    data = load_proxy_data()
    # Các dòng cấu hình cơ bản cho 3proxy
    config_lines = [
        "nserver 8.8.8.8",
        "nserver 8.8.4.4",
        "nscache 65536",
        "timeouts 1 5 30 60 180 1800 15 60",
        "daemon",
        f"log {LOG_FILE}",
        "logformat \"-L%t.%.%N -p%p -u%U -c%C -r%R -e%E\"", # Format log để parse
        "auth strong",
    ]
    
    # Lọc ra các proxy chưa hết hạn để đưa vào config
    active_proxies = [p for p in data['proxies'] if p['status'] != 'expired']

    for proxy in active_proxies:
        config_lines.append(f"users {proxy['username']}:CL:{proxy['password']}")
        config_lines.append(f"allow {proxy['username']}")
        # Dòng quan trọng: ánh xạ port và IP vào/ra
        config_lines.append(f"proxy -6 -s0 -n -a -p{proxy['port']} -i{PUBLIC_IPV4} -e{proxy['ipv6']}")

    with open(PROXY_CONFIG_FILE, 'w') as f:
        f.write("\n".join(config_lines))
        
    print("Reloading 3proxy service...")
    # Giết tiến trình cũ và khởi động lại với config mới
    subprocess.run(['killall', '3proxy'], capture_output=True)
    subprocess.run(['/usr/local/bin/3proxy', PROXY_CONFIG_FILE], check=True)
    print("3proxy reloaded.")

# --- ✨ HÀM LOGIC CHÍNH ---
def create_new_proxies(quantity: int):
    data = load_proxy_data()
    newly_created_proxies = []
    
    for _ in range(quantity):
        # Tìm một port chưa được sử dụng
        while True:
            port = random.randint(PORT_RANGE_START, PORT_RANGE_END)
            if port not in data['used_ports']:
                data['used_ports'].append(port)
                break
        
        proxy_info = {
            "ipv4": PUBLIC_IPV4,
            "port": port,
            "ipv6": generate_random_ipv6(),
            "username": PROXY_USERNAME,
            "password": generate_random_password(),
            "status": "unused", # Trạng thái ban đầu: chưa dùng
            "creation_time": datetime.datetime.now().isoformat(),
            "first_used_time": None
        }
        data['proxies'].append(proxy_info)
        newly_created_proxies.append(proxy_info)

    save_proxy_data(data)
    
    # Cập nhật hệ thống (thêm IP, reload 3proxy)
    update_system_ips(newly_created_proxies, [])
    generate_3proxy_config_and_reload()
    
    return newly_created_proxies

def check_proxies_and_regenerate():
    print(f"Running hourly check at {datetime.datetime.now()}...")
    data = load_proxy_data()
    
    # 1. Đọc log để phát hiện "lần dùng đầu tiên"
    try:
        with open(LOG_FILE, 'r') as f:
            logs = f.readlines()
        
        # Tạo một map để tra cứu nhanh các proxy chưa active
        unused_proxies_map = {str(p['port']): p for p in data['proxies'] if p['status'] == 'unused'}
        if unused_proxies_map:
            for log_line in logs:
                # Parse log để tìm port được sử dụng
                port_used = None
                if "-p" in log_line:
                    try:
                        port_used = log_line.split("-p")[1].split(" ")[0]
                    except IndexError:
                        continue
                
                if port_used and port_used in unused_proxies_map:
                    print(f"Proxy on port {port_used} detected first use. Updating status.")
                    unused_proxies_map[port_used]['status'] = 'active'
                    unused_proxies_map[port_used]['first_used_time'] = datetime.datetime.now().isoformat()
    except FileNotFoundError:
        print("Log file not found, skipping first-use detection.")
        
    # 2. Kiểm tra và đánh dấu các proxy đã hết hạn 30 ngày
    now = datetime.datetime.now()
    proxies_to_remove_from_iface = []

    for proxy in data['proxies']:
        if proxy['status'] == 'active' and proxy['first_used_time']:
            first_used_time = datetime.datetime.fromisoformat(proxy['first_used_time'])
            if now > first_used_time + datetime.timedelta(days=PROXY_LIFETIME_DAYS):
                if proxy['status'] != 'expired':
                    print(f"Proxy on port {proxy['port']} has expired.")
                    proxy['status'] = 'expired'
                    proxies_to_remove_from_iface.append(proxy)

    # Nếu có proxy mới hết hạn, xóa IP của nó khỏi card mạng
    if proxies_to_remove_from_iface:
        update_system_ips([], proxies_to_remove_from_iface)

    # 3. Tự động tái tạo nếu số proxy hết hạn đạt ngưỡng
    expired_proxies = [p for p in data['proxies'] if p['status'] == 'expired']
    if len(expired_proxies) >= REGENERATE_THRESHOLD:
        print(f"Expired proxy count ({len(expired_proxies)}) reached threshold ({REGENERATE_THRESHOLD}). Regenerating...")
        
        # Dọn dẹp: Xóa hẳn các proxy hết hạn khỏi CSDL
        data['proxies'] = [p for p in data['proxies'] if p['status'] != 'expired']
        expired_ports = {p['port'] for p in expired_proxies}
        data['used_ports'] = [p for p in data['used_ports'] if p not in expired_ports]
        
        save_proxy_data(data)
        
        # Tạo 2000 proxy mới
        new_proxies = create_new_proxies(2000)
        
        # Gửi thông báo và file cho admin
        bot.send_message(ADMIN_ID, f"♻️ Hệ thống đã tự động tái tạo 2000 proxy mới do có {len(expired_proxies)} proxy hết hạn.")
        send_proxy_list_to_admin(ADMIN_ID, new_proxies, "new_proxy_list.txt")

    save_proxy_data(data)
    # Xoá trắng file log sau mỗi lần kiểm tra để tránh file quá lớn
    open(LOG_FILE, 'w').close()
    
    print("Hourly check finished.")

def send_proxy_list_to_admin(chat_id, proxies, filename):
    if not proxies:
        bot.send_message(chat_id, "Không có proxy nào để hiển thị.")
        return
        
    list_content = "\n".join([f"{p['ipv4']}:{p['port']}:{p['username']}:{p['password']}" for p in proxies])
    
    with open(filename, "w") as f:
        f.write(list_content)
    
    with open(filename, "rb") as f:
        bot.send_document(chat_id, f)
    
    os.remove(filename)

# --- 🤖 TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not is_admin(message): return
    help_text = (
        "Chào Admin! Tôi là bot quản lý Proxy IPv6.\n\n"
        "Các lệnh có sẵn:\n"
        "🔹 `/tao <số lượng>` - Tạo số lượng proxy mới.\n"
        "   *Ví dụ:* `/tao 2000`\n"
        "🔹 `/dashboard` - Hiển thị bảng điều khiển trạng thái.\n"
        "🔹 `/ds_proxy` - Gửi file danh sách tất cả proxy đang hoạt động.\n"
        "🔹 `/check` - Chạy kiểm tra và tái tạo thủ công."
    )
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['tao'])
def handle_create_proxy(message):
    if not is_admin(message): return
    try:
        quantity = int(message.text.split()[1])
        if not 1 <= quantity <= 10000: raise ValueError("Số lượng không hợp lệ.")
            
        bot.reply_to(message, f"🚀 Bắt đầu tạo {quantity} proxy... Vui lòng chờ trong giây lát.")
        new_proxies = create_new_proxies(quantity)
        bot.send_message(message.chat.id, f"✅ Đã tạo thành công {quantity} proxy.")
        send_proxy_list_to_admin(message.chat.id, new_proxies, f"proxies_{quantity}.txt")
        
    except (IndexError, ValueError):
        bot.reply_to(message, "Lỗi cú pháp. Vui lòng sử dụng: `/tao <số lượng>`")

@bot.message_handler(commands=['dashboard'])
def handle_dashboard(message):
    if not is_admin(message): return
    data = load_proxy_data()
    proxies = data.get('proxies', [])
    total = len(proxies)
    unused = len([p for p in proxies if p['status'] == 'unused'])
    active = len([p for p in proxies if p['status'] == 'active'])
    expired = len([p for p in proxies if p['status'] == 'expired'])
    
    last_check_time = datetime.datetime.now().strftime("%d/%m/%Y - %H:%M")
    
    dashboard_text = (
        f"📊 **Tình trạng Proxy hiện tại:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
