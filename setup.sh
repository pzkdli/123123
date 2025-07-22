#!/bin/bash

# --- Cảnh báo bảo mật ---
# Mở tất cả các cổng (10000-60000) và tắt SELinux có thể làm giảm đáng kể bảo mật của VPS của bạn.
# Hãy đảm bảo bạn hiểu rủi ro trước khi chạy tập lệnh này trên môi trường sản xuất.
# --- End cảnh báo ---

echo "--- Bắt đầu quá trình cài đặt và cấu hình ---"

# Đối với AlmaLinux 9, các kho lưu trữ mặc định nên hoạt động.
# Dọn dẹp cache DNF để đảm bảo sử dụng các nguồn mới nhất và cập nhật hệ thống.
echo "Dọn dẹp DNF cache và cập nhật hệ thống (cho AlmaLinux 9.4)..."
sudo dnf clean all
sudo dnf update -y
sudo dnf upgrade -y

# 2. Cài đặt các gói cần thiết (Python 3.6+, pip, git, curl, net-tools, wget, make, gcc)
echo "2. Cài đặt Python 3.6+, pip và các gói cần thiết cho biên dịch..."
sudo dnf install -y python3 python3-pip git curl net-tools wget systemd make gcc
# 'make' và 'gcc' được cài đặt trực tiếp, thay thế cho 'Development Tools' groupinstall phức tạp hơn.

# 3. Cài đặt thư viện Python cho bot Telegram
echo "3. Cài đặt thư viện Python Telegram Bot..."
# Đảm bảo pip3 hoạt động sau khi cài đặt gói python3-pip
sudo pip3 install python-telegram-bot==13.7 apscheduler==3.9.1

# 4. Tải xuống và biên dịch 3proxy
echo "4. Tải xuống và biên dịch 3proxy..."
THREEPROXY_VERSION="0.9.5" # Phiên bản cụ thể theo yêu cầu của bạn
THREEPROXY_DIR="/usr/local/3proxy"
mkdir -p "$THREEPROXY_DIR"
cd "$THREEPROXY_DIR"

# Sử dụng URL trực tiếp từ GitHub releases cho bản 0.9.5.tar.gz
# Đây là URL chính xác để tải về file nén, không phải trang HTML
THREEPROXY_TAR_URL="https://github.com/3proxy/3proxy/archive/refs/tags/${THREEPROXY_VERSION}.tar.gz"

# Xóa file tar.gz cũ nếu có để đảm bảo tải bản mới
rm -f "3proxy-${THREEPROXY_VERSION}.tar.gz"

echo "Đang tải 3proxy từ $THREEPROXY_TAR_URL..."
wget "$THREEPROXY_TAR_URL" -O "3proxy-${THREEPROXY_VERSION}.tar.gz"
if [ $? -ne 0 ]; then
    echo "Lỗi: Không thể tải file 3proxy-${THREEPROXY_VERSION}.tar.gz. Kiểm tra kết nối hoặc URL."
    exit 1
fi

echo "Đang giải nén 3proxy..."
tar -xzf "3proxy-${THREEPROXY_VERSION}.tar.gz"
if [ $? -ne 0 ]; then
    echo "Lỗi: Không thể giải nén file 3proxy-${THREEPROXY_VERSION}.tar.gz. Vui lòng kiểm tra file."
    exit 1
fi

# Thư mục giải nén sẽ là 3proxy-0.9.5
EXTRACTED_DIR="3proxy-${THREEPROXY_VERSION}"
if [ ! -d "$EXTRACTED_DIR" ]; then
    echo "Lỗi: Thư mục giải nén $EXTRACTED_DIR không tồn tại."
    exit 1
fi
cd "$EXTRACTED_DIR"

echo "Đang biên dịch 3proxy..."
make -f Makefile.Linux
if [ $? -ne 0 ]; then
    echo "Lỗi: Không thể biên dịch 3proxy. Vui lòng kiểm tra các thư viện cần thiết và lỗi trên."
    exit 1
fi

echo "Đang cài đặt 3proxy..."
# Di chuyển các file thực thi vào thư mục cài đặt
sudo cp src/3proxy src/dameon src/ftppr src/pop3p src/socks src/tcppm src/udppm src/webcache "$THREEPROXY_DIR/"
sudo chmod +x "$THREEPROXY_DIR/3proxy"

echo "3proxy đã được biên dịch và cài đặt vào $THREEPROXY_DIR/"

# 5. Cấu hình cơ bản cho 3proxy và tạo thư mục log
echo "5. Cấu hình cơ bản cho 3proxy và tạo thư mục log..."
sudo mkdir -p /etc/3proxy # Đảm bảo thư mục /etc/3proxy tồn tại
sudo mkdir -p /var/log/3proxy
sudo touch /var/log/3proxy/access.log
sudo chmod 666 /var/log/3proxy/access.log # Để 3proxy có thể ghi log

# Tạo file cấu hình 3proxy ban đầu
# Bot sẽ tự động cập nhật file này
sudo bash -c "cat > /etc/3proxy/3proxy.cfg <<EOL
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
logformat \"- +_L%t.%. %N.%p %E %U %C:%c %R:%r %O %I %h %T\"
rotate 30

# Các proxy được thêm vào bên dưới bởi bot:
EOL"

# 6. Tạo dịch vụ Systemd cho 3proxy
echo "6. Tạo dịch vụ Systemd cho 3proxy..."
sudo bash -c "cat > /etc/systemd/system/3proxy.service <<EOL
[Unit]
Description=3proxy Proxy Server
After=network.target

[Service]
Type=forking
ExecStart=${THREEPROXY_DIR}/3proxy /etc/3proxy/3proxy.cfg
ExecReload=/bin/kill -HUP \$MAINPID
PIDFile=/var/run/3proxy.pid
User=nobody # Chạy 3proxy với quyền user thấp hơn
Group=nobody
LimitNOFILE=512000

[Install]
WantedBy=multi-user.target
EOL"

sudo systemctl daemon-reload
sudo systemctl enable 3proxy.service
sudo systemctl start 3proxy.service
if [ $? -ne 0 ]; then
    echo "Lỗi: Không thể khởi động dịch vụ 3proxy. Kiểm tra nhật ký hệ thống bằng 'sudo journalctl -xeu 3proxy.service'."
    exit 1
fi
echo "Dịch vụ 3proxy đã được tạo và khởi động."

# 7. Cấu hình Firewall (Mở tất cả các cổng 10000-60000)
echo "7. Cấu hình Firewall (Mở các cổng 10000-60000) và tắt SELinux..."
# Dành cho AlmaLinux 9 (Firewalld)
if command -v firewall-cmd &>/dev/null; then
    sudo systemctl enable firewalld --now
    sudo firewall-cmd --zone=public --add-port=10000-60000/tcp --permanent
    sudo firewall-cmd --reload
    echo "Firewalld đã được cấu hình."
elif command -v iptables &>/dev/null; then
    # Dành cho các hệ thống cũ hơn dùng iptables
    sudo iptables -A INPUT -p tcp --dport 10000:60000 -j ACCEPT
    sudo service iptables save
    echo "Iptables đã được cấu hình."
fi

# 8. Tắt SELinux (thường gây ra lỗi với các dịch vụ proxy)
sudo setenforce 0
sudo sed -i 's/SELINUX=enforcing/SELINUX=permissive/g' /etc/selinux/config
echo "SELinux đã được chuyển sang chế độ Permissive (có hiệu lực sau khi khởi động lại)."

echo "--- Quá trình cài đặt và cấu hình hoàn tất ---"
echo "Bạn có thể cần khởi động lại VPS để các thay đổi của SELinux có hiệu lực đầy đủ."
echo "Bây giờ bạn có thể chạy file proxy.py."
