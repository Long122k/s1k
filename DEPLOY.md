# Triển khai Gia1K trên VPS (thay cho CI cũ)

Trước đây: CI (GitLab/GitHub) chạy crawler → commit `data.json` → GitHub Pages phục vụ, Cloudflare trỏ vào Pages.

Bây giờ: **VPS tự crawl mỗi giờ + nginx phục vụ site**, Cloudflare trỏ thẳng vào IP VPS.

```
Người dùng ──► Cloudflare (proxied) ──► VPS: nginx  ─┬─ index.html
                                                     └─ data.json  ◄── systemd timer chạy getdeal1k.py mỗi giờ
```

Giả định VPS chạy **Ubuntu 22.04+ / Debian 12+**, có quyền `sudo`. Thư mục chuẩn: `/home/ubuntu/s1k` (trong home của user `ubuntu`).

---

## 1. Đưa code lên VPS

SSH vào VPS (user `ubuntu`) rồi clone repo vào `/home/ubuntu/s1k`:

```bash
git clone https://github.com/Long122k/s1k.git /home/ubuntu/s1k
```

> Repo private? Clone kèm token đọc: `git clone https://<TOKEN>@github.com/Long122k/s1k.git /home/ubuntu/s1k`
> Không dùng git? Từ máy bạn: `scp -r ./s1k ubuntu@VPS_IP:/home/ubuntu/`

## 2. Cài đặt (1 lệnh)

```bash
sudo bash /home/ubuntu/s1k/deploy/setup_vps.sh
```

Script sẽ: cài `python3-venv/pip/nginx`, đặt timezone VN, mở quyền cho nginx đọc site trong home, tạo venv + cài deps (chỉ `requests`), bật **systemd timer crawl mỗi giờ (phút :45)** (chạy dưới user `ubuntu`), chạy crawl lần đầu, và cấu hình nginx.

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

1. **Tạo cert origin:** Cloudflare → **SSL/TLS → Origin Server → Create Certificate** (để mặc định) → tạo cho `gia1k.com, *.gia1k.com`. Cloudflare hiện 2 ô: *Origin Certificate* và *Private Key*.
2. **Lưu cert lên VPS:**
   ```bash
   sudo mkdir -p /etc/ssl/cloudflare
   sudo nano /etc/ssl/cloudflare/gia1k.com.pem   # dán "Origin Certificate" rồi lưu
   sudo nano /etc/ssl/cloudflare/gia1k.com.key   # dán "Private Key" rồi lưu
   sudo chmod 600 /etc/ssl/cloudflare/gia1k.com.key
   ```
3. **Thay config nginx bằng bản HTTPS có sẵn** (đã kèm redirect 80→443):
   ```bash
   sudo cp /home/ubuntu/s1k/deploy/nginx-gia1k-https.conf /etc/nginx/sites-available/gia1k
   sudo nginx -t && sudo systemctl reload nginx
   ```
4. **Cloudflare → SSL/TLS → Overview → chọn `Full (strict)`.** ⚠️ Đừng để `Flexible` — sẽ lặp redirect vô hạn.

> Chưa muốn cài cert? Cứ để nginx HTTP (sau bước 2 của mục Cài đặt) và đặt Cloudflare SSL mode **Flexible** để người dùng vẫn thấy ổ khoá HTTPS. Đoạn CF↔VPS chưa mã hoá — chỉ nên dùng tạm.

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
| Cập nhật code            | `cd /home/ubuntu/s1k && git pull` (nginx tự phục vụ bản mới)  |

### Thay systemd bằng crontab (nếu thích)
```cron
# crontab của user ubuntu (chạy: crontab -e) — crawl mỗi giờ phút 45
45 * * * * OUTPUT_FILE=/home/ubuntu/s1k/data.json /home/ubuntu/s1k/.venv/bin/python /home/ubuntu/s1k/getdeal1k.py >> /home/ubuntu/s1k/crawl.log 2>&1
```

## Xử lý sự cố nhanh
- **Web trắng / “Chưa tải được dữ liệu”:** chưa có `data.json`. Chạy `sudo systemctl start gia1k-crawl.service` rồi xem log.
- **Crawl không ra deal:** nguồn đổi cấu trúc hoặc chặn IP VPS → xem log; thử `curl -A "Mozilla/5.0" https://shopee1k.com/ | grep -c __next_f` (phải > 0). Crawler bỏ qua nguồn lỗi và vẫn ghi từ nguồn còn lại; nếu cả hai rỗng thì GIỮ data.json cũ.
- **502/522 trên Cloudflare:** nginx chưa chạy hoặc firewall chặn 80/443 → `sudo systemctl status nginx`, mở port: `sudo ufw allow 80,443/tcp`.
