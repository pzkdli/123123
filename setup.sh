#!/bin/bash

# --- Cảnh báo bảo mật ---
# Mở tất cả các cổng (10000-60000) và tắt SELinux có thể làm giảm đáng kể bảo mật của VPS của bạn.
# Hãy đảm bảo bạn hiểu rủi ro trước khi chạy tập lệnh này trên môi trường sản xuất.
# --- End cảnh báo ---

echo "--- Bắt đầu quá trình cài đặt và cấu hình ---"

# 1. Cập nhật hệ thống
echo "1. Cập nhật hệ thống..."
sudo yum update -y
sudo yum upgrade -y

# 2. Cài đặt các gói cần thiết (Python 3.6+, git, curl, net-tools, wget)
echo "2. Cài đặt Python 3.6+ và các gói cần thiết..."
# Đối với CentOS 7, Python 3 có thể là python36, python3, hoặc từ EPEL
# Đối với CentOS 8, python3 là mặc định
if command -v python3 &>/dev/null; then
    echo "Python 3 đã được cài đặt."
else
    echo "Cài đặt Python 3 và pip..."
    sudo yum install -y python3 python3-pip
fi
sudo yum install -y curl git net-tools wget systemd

# Cài đặt các công cụ phát triển để biên dịch phần mềm (bao gồm 'make')
echo "Cài đặt các công cụ phát triển (Development Tools)..."
sudo yum groupinstall "Development Tools" -y

# 3. Cài đặt thư viện Python cho bot Telegram
echo "3. Cài đặt thư viện Python Telegram Bot..."
# Sử dụng pip3 vì python3-pip đã được cài đặt
sudo pip3 install python-telegram-bot==13.7 apscheduler==3.9.1

# 4. Tải xuống và biên dịch 3proxy
echo "4. Tải xuống và biên dịch 3proxy..."
# 3proxy thường không có sẵn trong các kho mặc định, nên ta sẽ biên dịch từ mã nguồn
THREEPROXY_VERSION="0.9.5" # Đã cập nhật lên phiên bản 0.9.5
THREEPROXY_DIR="/usr/local/3proxy"
mkdir -p "$THREEPROXY_DIR"
cd "$THREEPROXY_DIR"

# Sử dụng URL chính xác từ GitHub releases (refs/tags/)
THREEPROXY_TAR_URL="https://github.com/3proxy/3proxy/archive/refs/tags/${THREEPROXY_VERSION}.tar.gz"

if [ ! -f "3proxy-${THREEPROXY_VERSION}.tar.gz" ]; then
    wget "$THREEPROXY_TAR_URL" -O "3proxy-${THREEPROXY_VERSION}.tar.gz"
fi

tar -xzf "3proxy-${THREEPROXY_VERSION}.tar.gz"
if [ $? -ne 0 ]; then
    echo "Lỗi: Không thể giải nén file 3proxy.tar.gz. Vui lòng kiểm tra file."
    exit 1
fi

# Tìm tên thư mục đã giải nén (thường là 3proxy-0.9.5 hoặc 3proxy-VERSION)
EXTRACTED_DIR=$(tar -tzf "3proxy-${THREEPROXY_VERSION}.tar.gz" | head -1 | cut -f1 -d"/")
if [ -z "$EXTRACTED_DIR" ]; then
    echo "Lỗi: Không thể xác định tên thư mục giải nén của 3proxy."
    exit 1
fi
cd "$EXTRACTED_DIR"

# Biên dịch 3proxy
make -f Makefile.Linux
if [ $? -ne 0 ]; then
    echo "Lỗi: Không thể biên dịch 3proxy. Kiểm tra lỗi trên."
    exit 1
fi

# Di chuyển các file thực thi vào thư mục cài đặt
sudo cp src/3proxy src/dameon src/ftppr src/pop3p src/socks src/tcppm src/udppm src/webcache "$THREEPROXY_DIR/"
sudo chmod +x "$THREEPROXY_DIR/3proxy"

echo "3proxy đã được biên dịch và cài đặt vào $THREEPROXY_DIR/"

# 5. Cấu hình cơ bản cho 3proxy và tạo thư mục log
echo "5. Cấu hình cơ bản cho 3proxy và tạo thư mục log..."
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
# Dành cho CentOS 7/8 (Firewalld)
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
