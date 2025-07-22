import random
import string
import subprocess
import datetime
import os
import threading
import time
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
import sqlite3
import ipaddress
import json

# Thiết lập logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Cấu hình Bot và 3proxy ---
BOT_TOKEN = "7022711443:AAG2kU-TWDskXqFxCjap1DGw2jjji2HE2Ac" # TOKEN CỦA BẠN
ADMIN_ID = 7550813603 # ID ADMIN CỦA BẠN
THREEPROXY_CONFIG_PATH = "/etc/3proxy/3proxy.cfg"
THREEPROXY_SERVICE_NAME = "3proxy"
DATABASE_NAME = "proxies.db"
# Đường dẫn đến file thực thi 3proxy (sau khi biên dịch bởi setup.sh)
THREEPROXY_EXEC_PATH = "/usr/local/3proxy/3proxy"

# Cấu hình 3proxy cơ bản cho mỗi lần ghi lại (chứa DNS server, log, timeout)
# Phần proxy cụ thể sẽ được thêm/bớt bởi bot
BASE_THREEPROXY_CONFIG_TEMPLATE = """
# Cấu hình 3proxy mặc định, được quản lý bởi bot proxy.py

# Máy chủ DNS (Cloudflare, Google) - ưu tiên IPv6
nserver [2606:4700:4700::1111]
nserver [2606:4700:4700::1001]
nserver [2001:4860:4860::8888]
nserver [2001:4860:4860::8844]

# Thời gian chờ mặc định (giây)
timeout 1200

# Cấu hình ghi nhật ký
log /var/log/3proxy/access.log D
logformat "- +_L%t.%. %N.%p %E %U %C:%c %R:%r %O %I %h %T"
rotate 30

# Các proxy được thêm vào bên dưới bởi bot:
"""

# Kết nối cơ sở dữ liệu SQLite
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    # Thêm cột 'started_using_at' để tính thời gian sử dụng
    c.execute('''CREATE TABLE IF NOT EXISTS proxies
                 (ipv4_vps TEXT, port INTEGER, user TEXT, password TEXT, ipv6 TEXT, 
                 expiry_days INTEGER, is_used INTEGER, started_using_at TEXT)''')
    conn.commit()
    conn.close()

# Tạo user ngẫu nhiên (vtoanXXXY)
def generate_user():
    numbers = ''.join(random.choices(string.digits, k=3))
    letter = random.choice(string.ascii_uppercase)
    return f"vtoan{numbers}{letter}"

# Tạo mật khẩu ngẫu nhiên (2 chữ cái in hoa)
def generate_password():
    return ''.join(random.choices(string.ascii_uppercase, k=2))

# Kiểm tra định dạng prefix IPv6
def validate_ipv6_prefix(prefix):
    try:
        ipaddress.IPv6Network(prefix, strict=False)
        return True
    except ValueError:
        logger.error(f"Prefix IPv6 không hợp lệ: {prefix}")
        return False

# Kiểm tra IPv6 có hoạt động trên VPS
def check_ipv6_support():
    try:
        result = subprocess.run(['ping6', '-c', '3', 'ipv6.google.com'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        if result.returncode == 0:
            logger.info("IPv6 hoạt động trên VPS")
            return True
        else:
            logger.error(f"IPv6 không hoạt động: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Lỗi khi kiểm tra IPv6: {e}")
        return False

# Tạo địa chỉ IPv6 ngẫu nhiên từ prefix
def generate_ipv6_from_prefix(prefix, num_addresses):
    try:
        network = ipaddress.IPv6Network(prefix, strict=False)
        
        conn = sqlite3.connect(DATABASE_NAME)
        c = conn.cursor()
        c.execute("SELECT ipv6 FROM proxies")
        used_ipv6 = [row[0] for row in c.fetchall()]
        conn.close()
        
        ipv6_addresses = []
        for _ in range(num_addresses):
            attempts = 0
            while attempts < 100: # Giới hạn số lần thử để tránh vòng lặp vô hạn
                random_host_id = random.getrandbits(64)
                new_ipv6_int = (int(network.network_address) & ( (2**128 - 1) << 64) ) | random_host_id
                ipv6 = str(ipaddress.IPv6Address(new_ipv6_int))
                
                # Tránh địa chỉ mạng và địa chỉ broadcast (nếu có)
                if ipv6 not in used_ipv6 and ipv6 != str(network.network_address) and ipv6 != str(network.broadcast_address):
                    ipv6_addresses.append(ipv6)
                    used_ipv6.append(ipv6)
                    break
                attempts += 1
            if attempts == 100:
                logger.warning(f"Không thể tạo thêm địa chỉ IPv6 duy nhất trong prefix {prefix}.")
                break
        
        return ipv6_addresses
    except Exception as e:
        logger.error(f"Lỗi khi tạo IPv6 từ prefix {prefix}: {e}")
        raise

# Viết lại toàn bộ file cấu hình 3proxy
def write_3proxy_config(proxies_data):
    try:
        with open(THREEPROXY_CONFIG_PATH, 'w') as f:
            f.write(BASE_THREEPROXY_CONFIG_TEMPLATE) # Ghi lại phần cấu hình cơ bản

            for proxy in proxies_data:
                ipv4_vps, port, user, password, ipv6 = proxy[0], proxy[1], proxy[2], proxy[3], proxy[4]
                f.write(f"\n# Proxy cho user: {user}\n")
                f.write(f"auth iponly,strong\n") # Chỉ chấp nhận auth user/pass
                f.write(f"users {user}:CL:{password}\n")
                f.write(f"proxy -6 -n -a -p{port} -i{ipv4_vps} -e{ipv6}\n")
        logger.info(f"Đã ghi lại cấu hình 3proxy vào {THREEPROXY_CONFIG_PATH}")
    except Exception as e:
        logger.error(f"Lỗi khi ghi cấu hình 3proxy: {e}")
        raise

# Khởi động lại dịch vụ 3proxy
def restart_3proxy_service():
    try:
        logger.info(f"Khởi động lại dịch vụ {THREEPROXY_SERVICE_NAME} để áp dụng cấu hình...")
        result = subprocess.run(['sudo', 'systemctl', 'restart', THREEPROXY_SERVICE_NAME], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"Dịch vụ {THREEPROXY_SERVICE_NAME} đã khởi động lại thành công.")
            return True
        else:
            logger.error(f"Lỗi khi khởi động lại {THREEPROXY_SERVICE_NAME}: {result.stderr}")
            # Kiểm tra trạng thái để debug sâu hơn nếu lỗi
            status_result = subprocess.run(['sudo', 'systemctl', 'status', THREEPROXY_SERVICE_NAME], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
            logger.error(f"Trạng thái {THREEPROXY_SERVICE_NAME}: {status_result.stdout}\n{status_result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Lỗi không mong muốn khi khởi động lại {THREEPROXY_SERVICE_NAME}: {e}")
        return False

# Kiểm tra kết nối proxy thực tế và cập nhật trạng thái sử dụng
def check_proxy_usage(ipv4_vps, port, user, password, expected_ipv6):
    try:
        cmd = f'curl -6 --proxy-anyauth --proxy http://{user}:{password}@{ipv4_vps}:{port} --connect-timeout 5 --max-time 10 https://api64.ipify.org?format=json'
        
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=15)

        if result.returncode == 0:
            try:
                response = json.loads(result.stdout)
                ip = response.get('ip', '')
                
                logger.info(f"Proxy {user}:{password}@{ipv4_vps}:{port} (Expected IPv6: {expected_ipv6}) - Returned IP from api64.ipify.org: {ip}")
                
                try:
                    returned_ipv6_obj = ipaddress.IPv6Address(ip)
                    if str(returned_ipv6_obj) == expected_ipv6:
                        logger.info(f"Proxy {user}:{password}@{ipv4_vps}:{port} hoạt động và trả về IPv6 mong muốn: {ip}")
                        return True, ip # Proxy hoạt động và trả về đúng IPv6
                    else:
                        logger.warning(f"Proxy {user}:{password}@{ipv4_vps}:{port} trả về IPv6: {ip} KHÔNG khớp với IPv6 mong muốn: {expected_ipv6}.")
                        return False, ip # Proxy hoạt động nhưng không trả về đúng IPv6
                except ipaddress.AddressValueError:
                    logger.warning(f"Proxy {user}:{password}@{ipv4_vps}:{port} trả về IP: {ip} KHÔNG phải là IPv6.")
                    return False, ip # Proxy hoạt động nhưng trả về IPv4
            except json.JSONDecodeError:
                logger.warning(f"Proxy {user}:{password}@{ipv4_vps}:{port} trả về nội dung không phải JSON: {result.stdout}.")
                return False, None
        else:
            logger.error(f"Proxy {user}:{password}@{ipv4_vps}:{port} không kết nối được hoặc lỗi curl (Exit Code {result.returncode}): {result.stderr}.")
            return False, None
    except subprocess.TimeoutExpired:
        logger.error(f"Lỗi: Lệnh kiểm tra proxy {user}:{password}@{ipv4_vps}:{port} đã hết thời gian.")
        return False, None
    except Exception as e:
        logger.error(f"Lỗi khi kiểm tra proxy {user}:{password}@{ipv4_vps}:{port}: {e}")
        return False, None

# Tự động kiểm tra proxy và xử lý hạn sử dụng
def auto_check_proxies(bot_data):
    while True:
        try:
            vps_ipv4 = bot_data.get('vps_ipv4')
            if not vps_ipv4:
                logger.warning("Không tìm thấy VPS_IPV4 trong auto_check_proxies. Bỏ qua kiểm tra.")
                time.sleep(60)
                continue

            conn = sqlite3.connect(DATABASE_NAME)
            c = conn.cursor()
            c.execute("SELECT ipv4_vps, port, user, password, ipv6, expiry_days, is_used, started_using_at FROM proxies")
            proxies_data = c.fetchall()
            
            proxies_to_remove = []

            for proxy_info in proxies_data:
                ipv4_vps_db, port, user, password, ipv6, expiry_days, is_used, started_using_at_str = proxy_info

                # Kiểm tra kết nối proxy
                is_working, _ = check_proxy_usage(ipv4_vps_db, port, user, password, ipv6)

                # Cập nhật trạng thái 'is_used' và 'started_using_at'
                if is_working and is_used == 0:
                    logger.info(f"Proxy {user} hoạt động lần đầu tiên. Bắt đầu tính thời gian.")
                    started_using_at = datetime.datetime.now()
                    c.execute("UPDATE proxies SET is_used=?, started_using_at=? WHERE user=?",
                              (1, started_using_at.strftime('%Y-%m-%d %H:%M:%S'), user))
                    conn.commit()
                
                # Kiểm tra hạn sử dụng chỉ khi proxy đã được sử dụng
                if is_used == 1 and started_using_at_str:
                    started_using_at = datetime.datetime.strptime(started_using_at_str, '%Y-%m-%d %H:%M:%S')
                    expiry_date = started_using_at + datetime.timedelta(days=expiry_days)
                    
                    if datetime.datetime.now() > expiry_date:
                        logger.info(f"Proxy {user} đã hết hạn. Thêm vào danh sách xóa.")
                        proxies_to_remove.append(proxy_info) # Lưu toàn bộ thông tin proxy để xóa
            
            # Xóa các proxy đã hết hạn
            for proxy_info in proxies_to_remove:
                ipv4_vps_db, port, user, password, ipv6, _, _, _ = proxy_info
                try:
                    remove_proxy_from_system(ipv6, user, password, port)
                    c.execute("DELETE FROM proxies WHERE user=?", (user,))
                    conn.commit()
                    logger.info(f"Đã xóa proxy hết hạn: {user}")
                except Exception as e:
                    logger.error(f"Lỗi khi xóa proxy hết hạn {user}: {e}")
                    
            conn.close()
        except Exception as e:
            logger.error(f"Lỗi khi kiểm tra proxy tự động: {e}")
        time.sleep(60) # Kiểm tra mỗi 60 giây

# Hàm để xóa proxy khỏi hệ thống (IPv6 và cấu hình 3proxy)
def remove_proxy_from_system(ipv6_to_delete, user_to_delete, password_to_delete, port_to_delete):
    try:
        # Xóa địa chỉ IPv6 khỏi card mạng
        logger.info(f"Xóa địa chỉ IPv6 {ipv6_to_delete}/64 khỏi eth0.")
        subprocess.run(['sudo', 'ip', '-6', 'addr', 'del', f'{ipv6_to_delete}/64', 'dev', 'eth0'], 
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

        # Đọc tất cả proxy từ DB để xây dựng lại file cấu hình 3proxy
        conn = sqlite3.connect(DATABASE_NAME)
        c = conn.cursor()
        c.execute("SELECT ipv4_vps, port, user, password, ipv6 FROM proxies WHERE user != ?", (user_to_delete,))
        remaining_proxies = c.fetchall()
        conn.close()
        
        # Ghi lại cấu hình 3proxy mà không có proxy đã xóa
        write_3proxy_config(remaining_proxies)
        
        # Khởi động lại dịch vụ 3proxy
        if not restart_3proxy_service():
            raise Exception("Không thể khởi động lại 3proxy sau khi cập nhật cấu hình.")

        logger.info(f"Đã xóa proxy {user_to_delete} khỏi hệ thống và 3proxy.")
    except Exception as e:
        logger.error(f"Lỗi khi xóa proxy {user_to_delete} khỏi hệ thống: {e}")
        raise

# Tạo proxy mới
def create_proxy(ipv4_vps, ipv6_addresses, days):
    try:
        if not check_ipv6_support():
            raise Exception("IPv6 không hoạt động trên VPS. Vui lòng kiểm tra cấu hình mạng.")
        
        conn = sqlite3.connect(DATABASE_NAME)
        c = conn.cursor()
        
        c.execute("SELECT port FROM proxies")
        used_ports = [row[0] for row in c.fetchall()]
        
        proxies_output = []
        
        current_proxies_in_db = []
        c.execute("SELECT ipv4_vps, port, user, password, ipv6, expiry_days, is_used, started_using_at FROM proxies")
        for row in c.fetchall():
            current_proxies_in_db.append(row)

        for ipv6 in ipv6_addresses:
            logger.info(f"Gán địa chỉ IPv6 {ipv6}/64 cho eth0.")
            result = subprocess.run(['sudo', 'ip', '-6', 'addr', 'add', f'{ipv6}/64', 'dev', 'eth0'], 
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            if result.returncode != 0:
                logger.error(f"Lỗi khi gán IPv6 {ipv6}: {result.stderr}")
                continue # Bỏ qua IPv6 này nếu không thể gán

            while True:
                port = random.randint(10000, 60000)
                if port not in used_ports:
                    used_ports.append(port)
                    break
            
            user = generate_user()
            password = generate_password()
            
            # Thêm vào DB với is_used = 0 và started_using_at = NULL
            c.execute("INSERT INTO proxies (ipv4_vps, port, user, password, ipv6, expiry_days, is_used, started_using_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (ipv4_vps, port, user, password, ipv6, days, 0, None))
            
            current_proxies_in_db.append((ipv4_vps, port, user, password, ipv6, days, 0, None))
            
            proxies_output.append(f"{ipv4_vps}:{port}:{user}:{password}")
        
        conn.commit()
        conn.close()

        # Ghi lại toàn bộ cấu hình 3proxy với các proxy mới
        write_3proxy_config(current_proxies_in_db)
        
        # Khởi động lại dịch vụ 3proxy
        if not restart_3proxy_service():
            raise Exception("Không thể khởi động lại 3proxy sau khi thêm proxy mới.")
        
        logger.info(f"Đã tạo {len(proxies_output)} proxy với IPv6")
        
        return proxies_output
    except Exception as e:
        logger.error(f"Lỗi khi tạo proxy: {e}")
        raise

# --- Telegram bot commands ---
def start(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID:
        update.message.reply_text("Bạn không có quyền sử dụng bot này!")
        return
    
    # Bắt đầu bằng việc hỏi IPv4 của VPS
    update.message.reply_text("Chào bạn! Vui lòng nhập địa chỉ IPv4 của VPS của bạn (ví dụ: 103.1.2.3):")
    context.user_data['state'] = 'ipv4_input'

def button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    # Kiểm tra xem VPS_IPV4 đã được thiết lập chưa
    vps_ipv4 = context.bot_data.get('vps_ipv4')
    if not vps_ipv4:
        query.message.reply_text("Vui lòng nhập địa chỉ IPv4 của VPS trước bằng lệnh /start!")
        context.user_data['state'] = 'ipv4_input'
        return

    if query.data == 'new':
        if 'prefix' not in context.user_data: 
            query.message.reply_text("Vui lòng nhập prefix IPv6 trước bằng lệnh /start!")
            return
        query.message.reply_text("Nhập số lượng proxy và số ngày (định dạng: số_lượng số_ngày, ví dụ: 5 7):")
        context.user_data['state'] = 'new'
    elif query.data == 'xoa':
        keyboard = [
            [InlineKeyboardButton("Xóa proxy lẻ", callback_data='xoa_le'),
             InlineKeyboardButton("Xóa hàng loạt", callback_data='xoa_all')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.message.reply_text("Chọn kiểu xóa:", reply_markup=reply_markup)
    elif query.data == 'check':
        conn = sqlite3.connect(DATABASE_NAME)
        c = conn.cursor()
        c.execute("SELECT ipv4_vps, port, user, password, ipv6, is_used FROM proxies")
        proxies = c.fetchall()
        conn.close()
        
        waiting = [p for p in proxies if p[5] == 0] # is_used == 0
        used = [p for p in proxies if p[5] == 1]   # is_used == 1
        
        vps_ipv4_for_output = context.bot_data.get('vps_ipv4', 'N/A')

        waiting_str = []
        for p in waiting:
            waiting_str.append(f"{vps_ipv4_for_output}:{p[1]}:{p[2]}:{p[3]}")
        
        used_str = []
        for p in used:
            used_str.append(f"{vps_ipv4_for_output}:{p[1]}:{p[2]}:{p[3]}")

        with open('waiting.txt', 'w') as f:
            f.write("\n".join(waiting_str))
        with open('used.txt', 'w') as f:
            f.write("\n".join(used_str))
        
        try:
            context.bot.send_document(chat_id=update.effective_chat.id, document=open('waiting.txt', 'rb'), caption="Danh sách proxy chờ sử dụng:")
            context.bot.send_document(chat_id=update.effective_chat.id, document=open('used.txt', 'rb'), caption="Danh sách proxy đã sử dụng:")
            query.message.reply_text(f"Tổng số proxy chờ: {len(waiting)}\nTổng số proxy đã sử dụng: {len(used)}")
        except Exception as e:
            logger.error(f"Lỗi khi gửi file waiting.txt/used.txt: {e}")
            query.message.reply_text(f"Tổng số proxy chờ: {len(waiting)}\nTổng số proxy đã sử dụng: {len(used)}\nLỗi khi gửi file: {e}")
    elif query.data == 'giahan':
        query.message.reply_text("Nhập proxy và số ngày gia hạn (định dạng: ipv4_vps:port:user:pass số_ngày):")
        context.user_data['state'] = 'giahan'
    elif query.data == 'xoa_le':
        query.message.reply_text("Nhập proxy cần xóa (định dạng: ipv4_vps:port:user:pass):")
        context.user_data['state'] = 'xoa_le'
    elif query.data == 'xoa_all':
        query.message.reply_text("Xác nhận xóa tất cả proxy? (Nhập: Xac_nhan_xoa_all)")
        context.user_data['state'] = 'xoa_all'

def message_handler(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID:
        update.message.reply_text("Bạn không có quyền sử dụng bot này!")
        return
    
    state = context.user_data.get('state')
    text = update.message.text.strip()

    if state == 'ipv4_input':
        try:
            ipaddress.IPv4Address(text)
            context.bot_data['vps_ipv4'] = text
            update.message.reply_text("Địa chỉ IPv4 của VPS đã được lưu. Bây giờ, vui lòng nhập prefix IPv6 (định dạng: 2401:2420:0:102f::/64):")
            context.user_data['state'] = 'prefix'
        except ipaddress.AddressValueError:
            update.message.reply_text("Địa chỉ IPv4 không hợp lệ! Vui lòng nhập lại:")
        return
    
    vps_ipv4 = context.bot_data.get('vps_ipv4')
    if not vps_ipv4:
        update.message.reply_text("Vui lòng nhập địa chỉ IPv4 của VPS trước bằng lệnh /start!")
        context.user_data['state'] = 'ipv4_input'
        return

    if state == 'prefix':
        if validate_ipv6_prefix(text):
            context.user_data['prefix'] = text
            
            # Lấy địa chỉ IPv6 chính của VPS (chỉ để hiển thị cho người dùng)
            detected_vps_ipv6_main = context.bot_data.get('vps_ipv6_main_addr')
            
            keyboard = [
                [InlineKeyboardButton("/New", callback_data='new'),
                 InlineKeyboardButton("/Xoa", callback_data='xoa')],
                [InlineKeyboardButton("/Check", callback_data='check'),
                 InlineKeyboardButton("/Giahan", callback_data='giahan')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text(f"Prefix IPv6 đã được lưu. IPv4 của VPS: {vps_ipv4}.\n"
                                      f"IPv6 chính của VPS (đã phát hiện): {detected_vps_ipv6_main if detected_vps_ipv6_main else 'Không tìm thấy'}\n"
                                      f"Chọn lệnh:", reply_markup=reply_markup)
            context.user_data['state'] = None
        else:
            update.message.reply_text("Prefix IPv6 không hợp lệ! Vui lòng nhập lại:")
    elif state == 'new':
        try:
            num_proxies, days = map(int, text.split())
            if num_proxies <= 0 or days <= 0:
                update.message.reply_text("Số lượng và số ngày phải lớn hơn 0!")
                return
            prefix = context.user_data.get('prefix')
            if not prefix:
                update.message.reply_text("Vui lòng nhập prefix IPv6 trước bằng lệnh /start!")
                return
            
            ipv6_addresses = generate_ipv6_from_prefix(prefix, num_proxies)
            if not ipv6_addresses:
                update.message.reply_text("Không thể tạo địa chỉ IPv6. Vui lòng kiểm tra prefix hoặc số lượng đã tạo.")
                return

            proxies = create_proxy(vps_ipv4, ipv6_addresses, days)
            
            if not proxies:
                update.message.reply_text("Không có proxy nào được tạo thành công. Vui lòng kiểm tra nhật ký lỗi.")
                return

            if len(proxies) < 5: # Chỉ gửi trực tiếp nếu số lượng ít
                update.message.reply_text("Proxy đã tạo:\n" + "\n".join(proxies))
            else:
                with open('proxies.txt', 'w') as f:
                    for proxy in proxies:
                        f.write(f"{proxy}\n")
                try:
                    context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=open('proxies.txt', 'rb'),
                        caption=f"Đã tạo {len(proxies)} proxy",
                        timeout=30
                    )
                except Exception as e:
                    logger.error(f"Lỗi khi gửi file proxies.txt: {e}")
                    update.message.reply_text(f"Đã tạo {len(proxies)} proxy nhưng lỗi khi gửi file: {e}\nFile proxies.txt đã được lưu trên hệ thống.")
            
            context.user_data['state'] = None
        except Exception as e:
            logger.error(f"Lỗi khi xử lý lệnh /New: {e}")
            update.message.reply_text(f"Định dạng không hợp lệ hoặc lỗi: {e}")
    elif state == 'giahan':
        try:
            proxy_str, days_str = text.rsplit(' ', 1)
            ipv4_from_input, port_str, user, password = proxy_str.split(':')
            port = int(port_str)
            days = int(days_str)

            if ipv4_from_input != vps_ipv4:
                update.message.reply_text(f"Địa chỉ IPv4 trong proxy ({ipv4_from_input}) không khớp với IPv4 của VPS đã lưu ({vps_ipv4}). Vui lòng kiểm tra lại.")
                return

            conn = sqlite3.connect(DATABASE_NAME)
            c = conn.cursor()
            c.execute("SELECT ipv6 FROM proxies WHERE port=? AND user=? AND password=?",
                      (port, user, password))
            result = c.fetchone()
            
            if result:
                ipv6_found = result[0]
                # Gia hạn chỉ bằng cách cập nhật expiry_days
                c.execute("UPDATE proxies SET expiry_days=? WHERE ipv6=? AND port=? AND user=?",
                          (days, ipv6_found, port, user))
                conn.commit()
                update.message.reply_text(f"Đã gia hạn proxy {proxy_str} thêm {days} ngày. IPv6 gốc: {ipv6_found}")
            else:
                update.message.reply_text("Proxy không tồn tại! Vui lòng kiểm tra lại IPv4 của VPS, Port, User, Pass.")
            conn.close()
            context.user_data['state'] = None
        except Exception as e:
            logger.error(f"Lỗi khi gia hạn proxy: {e}")
            update.message.reply_text(f"Định dạng không hợp lệ hoặc lỗi: {e}\nVui lòng nhập: ipv4_vps:port:user:pass số_ngày")

    elif state == 'xoa_le':
        try:
            proxy_str = text
            ipv4_from_input, port_str, user, password = proxy_str.split(':')
            port = int(port_str)
            
            if ipv4_from_input != vps_ipv4:
                update.message.reply_text(f"Địa chỉ IPv4 trong proxy ({ipv4_from_input}) không khớp với IPv4 của VPS đã lưu ({vps_ipv4}). Vui lòng kiểm tra lại.")
                return

            conn = sqlite3.connect(DATABASE_NAME)
            c = conn.cursor()
            c.execute("SELECT ipv6 FROM proxies WHERE port=? AND user=? AND password=?",
                      (port, user, password))
            result = c.fetchone()
            
            if result:
                ipv6_to_delete = result[0]
                remove_proxy_from_system(ipv6_to_delete, user, password, port)
                c.execute("DELETE FROM proxies WHERE ipv6=? AND port=? AND user=?", (ipv6_to_delete, port, user))
                conn.commit()
                update.message.reply_text(f"Đã xóa proxy {proxy_str} (IPv6: {ipv6_to_delete})")
            else:
                update.message.reply_text("Proxy không tồn tại! Vui lòng kiểm tra lại IPv4 của VPS, Port, User, Pass.")
            conn.close()
            context.user_data['state'] = None
        except Exception as e:
            logger.error(f"Lỗi khi xóa proxy: {e}")
            update.message.reply_text(f"Định dạng không hợp lệ hoặc lỗi: {e}\nVui lòng nhập: ipv4_vps:port:user:pass")
    elif state == 'xoa_all':
        if text == 'Xac_nhan_xoa_all':
            try:
                conn = sqlite3.connect(DATABASE_NAME)
                c = conn.cursor()
                c.execute("SELECT ipv6, user, password, port FROM proxies")
                all_proxies_info = c.fetchall()
                
                for ipv6, user, password, port in all_proxies_info:
                    try:
                        subprocess.run(['sudo', 'ip', '-6', 'addr', 'del', f'{ipv6}/64', 'dev', 'eth0'], 
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
                    except Exception as e:
                        logger.warning(f"Lỗi khi xóa IPv6 {ipv6} khỏi hệ thống: {e}")

                c.execute("DELETE FROM proxies")
                conn.commit()
                conn.close()
                
                # Ghi lại cấu hình 3proxy chỉ với BASE_THREEPROXY_CONFIG_TEMPLATE
                with open(THREEPROXY_CONFIG_PATH, 'w') as f:
                    f.write(BASE_THREEPROXY_CONFIG_TEMPLATE)
                
                # Khởi động lại dịch vụ 3proxy
                if not restart_3proxy_service():
                    raise Exception("Không thể khởi động lại 3proxy sau khi xóa tất cả proxy.")
                
                update.message.reply_text("Đã xóa tất cả proxy!")
                context.user_data['state'] = None
            except Exception as e:
                logger.error(f"Lỗi khi xóa tất cả proxy: {e}")
                update.message.reply_text(f"Lỗi khi xóa tất cả proxy: {e}")
        else:
            update.message.reply_text("Vui lòng nhập: Xac_nhan_xoa_all")

def main():
    init_db()
    
    # Phát hiện địa chỉ IPv6 chính của VPS để hiển thị cho người dùng
    detected_vps_ipv6_main = None
    try:
        result_show_ipv6 = subprocess.run(['ip', '-6', 'addr', 'show', 'dev', 'eth0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True, timeout=10)
        for line in result_show_ipv6.stdout.splitlines():
            if 'inet6' in line and 'scope global' in line and 'dynamic' not in line: # Tránh các IP động tạm thời
                parts = line.split()
                for part in parts:
                    if ':' in part and '/' in part:
                        try:
                            ipaddress.IPv6Network(part, strict=False)
                            detected_vps_ipv6_main = part
                            break
                        except ValueError:
                            continue
                if detected_vps_ipv6_main:
                    break
        
        if not detected_vps_ipv6_main:
            logger.warning("Không thể tự động phát hiện địa chỉ IPv6 chính của VPS (scope global, không động).")
    except Exception as e:
        logger.error(f"Lỗi khi cố gắng tự động phát hiện IPv6 chính của VPS: {e}")
        detected_vps_ipv6_main = None
    
    updater = Updater(BOT_TOKEN, use_context=True, request_kwargs={'read_timeout': 6, 'connect_timeout': 7, 'con_pool_size': 1})
    dp = updater.dispatcher

    dp.bot_data['vps_ipv6_main_addr'] = detected_vps_ipv6_main # Lưu để hiển thị trong bot

    # Khởi tạo hoặc ghi lại cấu hình 3proxy ban đầu (chỉ với BASE_THREEPROXY_CONFIG_TEMPLATE)
    # Điều này đảm bảo file 3proxy.cfg có cấu trúc đúng khi bot khởi động
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        c = conn.cursor()
        c.execute("SELECT ipv4_vps, port, user, password, ipv6, expiry_days, is_used, started_using_at FROM proxies")
        all_proxies_from_db = c.fetchall()
        conn.close()
        
        # Chuyển đổi tuple từ DB thành định dạng list of tuples cho write_3proxy_config
        proxies_for_config = [(p[0], p[1], p[2], p[3], p[4]) for p in all_proxies_from_db]
        write_3proxy_config(proxies_for_config)
        restart_3proxy_service() # Khởi động lại để đảm bảo cấu hình đúng
    except Exception as e:
        logger.error(f"Lỗi khi khởi tạo/ghi lại cấu hình 3proxy ban đầu: {e}")


    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(button))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, message_handler))
    
    # Khởi động luồng kiểm tra proxy tự động
    threading.Thread(target=auto_check_proxies, args=(updater.dispatcher.bot_data,), daemon=True).start()
    
    logger.info("Bot Telegram đã khởi động.")
    updater.start_polling(poll_interval=1.0)
    updater.idle()

if __name__ == '__main__':
    main()
