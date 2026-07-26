#!/usr/bin/env bash
#
# setup_vps.sh — Cài đặt Gia1K trên VPS Ubuntu/Debian.
# Chạy 1 lần với quyền root:  sudo bash deploy/setup_vps.sh
#
# Giả định: code đã được đặt tại /var/www/gia1k (xem DEPLOY.md).
set -euo pipefail

APP_DIR=/var/www/gia1k
APP_USER=gia1k

if [[ $EUID -ne 0 ]]; then
  echo "❌ Cần chạy bằng root:  sudo bash deploy/setup_vps.sh" >&2
  exit 1
fi

if [[ ! -f "$APP_DIR/getdeal1k.py" ]]; then
  echo "❌ Không thấy $APP_DIR/getdeal1k.py — hãy clone code vào $APP_DIR trước (xem DEPLOY.md)." >&2
  exit 1
fi

echo "▶ 1/7  Cài gói hệ thống ..."
apt-get update -y
apt-get install -y python3-venv python3-pip nginx git

echo "▶ 2/7  Đặt timezone = Asia/Ho_Chi_Minh ..."
timedatectl set-timezone Asia/Ho_Chi_Minh || true

echo "▶ 3/7  Tạo user hệ thống '$APP_USER' ..."
id -u "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "▶ 4/7  Tạo virtualenv + cài Python deps (chỉ requests) ..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "▶ 5/7  Cài systemd service + timer (crawl mỗi giờ) ..."
cp "$APP_DIR/deploy/gia1k-crawl.service" /etc/systemd/system/gia1k-crawl.service
cp "$APP_DIR/deploy/gia1k-crawl.timer"   /etc/systemd/system/gia1k-crawl.timer
systemctl daemon-reload
systemctl enable --now gia1k-crawl.timer

echo "▶ 6/7  Chạy crawl lần đầu để có data.json ngay ..."
systemctl start gia1k-crawl.service || echo "  (lần đầu có thể lỗi nếu nguồn chặn — timer sẽ thử lại mỗi giờ)"

echo "▶ 7/7  Cấu hình nginx ..."
cp "$APP_DIR/deploy/nginx-gia1k.conf" /etc/nginx/sites-available/gia1k
ln -sf /etc/nginx/sites-available/gia1k /etc/nginx/sites-enabled/gia1k
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo
echo "✅ Xong. Kiểm tra:"
echo "   - Log crawl:   journalctl -u gia1k-crawl.service -n 50 --no-pager"
echo "   - Lịch chạy:   systemctl list-timers gia1k-crawl.timer"
echo "   - data.json:   ls -l $APP_DIR/data.json"
echo "   - Web local:   curl -I http://127.0.0.1/"
echo
echo "Tiếp theo: trỏ DNS Cloudflare về IP VPS này và (khuyến nghị) bật HTTPS — xem DEPLOY.md."
