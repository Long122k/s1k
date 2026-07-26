# Triển khai Gia1K trên VPS (thay cho CI cũ)

Trước đây: CI (GitLab/GitHub) chạy crawler → commit `data.json` → GitHub Pages phục vụ, Cloudflare trỏ vào Pages.

Bây giờ: **VPS tự crawl mỗi giờ + nginx phục vụ site**, Cloudflare trỏ thẳng vào IP VPS.

```
Người dùng ──► Cloudflare (proxied) ──► VPS: nginx  ─┬─ index.html
                                                     └─ data.json  ◄── systemd timer chạy getdeal1k.py mỗi giờ
```

Giả định VPS chạy **Ubuntu 22.04+ / Debian 12+**, có quyền `sudo`. Thư mục chuẩn: `/var/www/gia1k`.

---

## 1. Đưa code lên VPS

SSH vào VPS rồi clone repo vào đúng `/var/www/gia1k`:

```bash
sudo mkdir -p /var/www/gia1k
sudo chown "$USER" /var/www/gia1k
git clone <URL_REPO_CUA_BAN> /var/www/gia1k
```

> Không có git remote? Dùng `scp -r ./s1k/* user@VPS_IP:/tmp/gia1k/` rồi `sudo mv /tmp/gia1k/* /var/www/gia1k/`.

## 2. Cài đặt (1 lệnh)

```bash
sudo bash /var/www/gia1k/deploy/setup_vps.sh
```

Script sẽ: cài `python3-venv/pip/nginx`, đặt timezone VN, tạo user `gia1k`, tạo venv + cài deps (chỉ `requests`), bật **systemd timer crawl mỗi giờ (phút :45)**, chạy crawl lần đầu, và cấu hình nginx.

> Nguồn crawl là **shopee1k.com + nghienshopee.net** (không cần trình duyệt/Playwright). VPS chỉ cần Python + `requests`.

Kiểm tra:

```bash
journalctl -u gia1k-crawl.service -n 50 --no-pager   # log lần crawl
systemctl list-timers gia1k-crawl.timer              # lịch chạy kế tiếp
curl -s http://127.0.0.1/data.json | head -c 200     # đã có data chưa
```

## 3. Trỏ domain Cloudflare về VPS

Vào **Cloudflare → DNS → Records** của `gia1k.com`, sửa/tạo:

| Type  | Name  | Content            | Proxy         |
|-------|-------|--------------------|---------------|
| `A`   | `@`   | `IP_VPS_CUA_BAN`   | 🟠 Proxied    |
| `CNAME` | `www` | `gia1k.com`      | 🟠 Proxied    |

Xoá các record A/CNAME cũ trỏ về GitHub Pages (185.199.108.153…). Giữ **Proxied** (đám mây cam) để được CDN + che IP gốc.

## 4. Bật HTTPS (khuyến nghị — Full strict)

1. Cloudflare → **SSL/TLS → Origin Server → Create Certificate** (để mặc định), tạo cho `gia1k.com, *.gia1k.com`.
2. Trên VPS lưu 2 phần vừa tạo:
   ```bash
   sudo mkdir -p /etc/ssl/cloudflare
   sudo nano /etc/ssl/cloudflare/gia1k.com.pem   # dán "Origin Certificate"
   sudo nano /etc/ssl/cloudflare/gia1k.com.key   # dán "Private Key"
   sudo chmod 600 /etc/ssl/cloudflare/gia1k.com.key
   ```
3. Sửa `/etc/nginx/sites-available/gia1k`: bỏ comment block `server { listen 443 ssl; ... }` ở cuối file, và đổi block `:80` thành redirect:
   ```nginx
   server {
       listen 80; listen [::]:80;
       server_name gia1k.com www.gia1k.com;
       return 301 https://$host$request_uri;
   }
   ```
4. Áp dụng: `sudo nginx -t && sudo systemctl reload nginx`
5. Cloudflare → **SSL/TLS → Overview** → chọn **Full (strict)**.

> Muốn nhanh gọn không cài cert origin: để nguyên nginx HTTP và đặt SSL mode **Flexible**. Kém an toàn hơn (CF↔VPS không mã hoá) — chỉ nên dùng tạm.

## 5. Tắt luồng CI cũ

- **GitHub Actions:** workflow crawl đã được gỡ khỏi repo (`.github/workflows/`). Commit & push để nó ngừng chạy trên GitHub.
- **GitLab CI:** nếu còn `.gitlab-ci.yml` ở remote GitLab, xoá file đó (hoặc tắt CI/CD trong Settings → CI/CD) để không crawl trùng.
- **GitHub Pages:** vào repo Settings → Pages → tắt, tránh domain vẫn phân giải về Pages.
- File `CNAME` là của GitHub Pages, VPS không dùng — có thể để lại hoặc xoá.

---

## Vận hành

| Việc                     | Lệnh                                                        |
|--------------------------|-------------------------------------------------------------|
| Crawl thủ công ngay      | `sudo systemctl start gia1k-crawl.service`                  |
| Xem log crawl            | `journalctl -u gia1k-crawl.service -f`                      |
| Đổi lịch crawl           | sửa `OnCalendar` trong `/etc/systemd/system/gia1k-crawl.timer` → `sudo systemctl daemon-reload && sudo systemctl restart gia1k-crawl.timer` |
| Cập nhật code            | `cd /var/www/gia1k && sudo -u gia1k git pull` → `sudo systemctl reload nginx` |

### Thay systemd bằng crontab (nếu thích)
```cron
# crontab của user gia1k — crawl mỗi giờ phút 45
45 * * * * OUTPUT_FILE=/var/www/gia1k/data.json /var/www/gia1k/.venv/bin/python /var/www/gia1k/getdeal1k.py >> /var/log/gia1k-crawl.log 2>&1
```

## Xử lý sự cố nhanh
- **Web trắng / “Chưa tải được dữ liệu”:** chưa có `data.json`. Chạy `sudo systemctl start gia1k-crawl.service` rồi xem log.
- **Crawl không ra deal:** nguồn đổi cấu trúc hoặc chặn IP VPS → xem log; thử `curl -A "Mozilla/5.0" https://shopee1k.com/ | grep -c __next_f` (phải > 0). Crawler bỏ qua nguồn lỗi và vẫn ghi từ nguồn còn lại; nếu cả hai rỗng thì GIỮ data.json cũ.
- **502/522 trên Cloudflare:** nginx chưa chạy hoặc firewall chặn 80/443 → `sudo systemctl status nginx`, mở port: `sudo ufw allow 80,443/tcp`.
