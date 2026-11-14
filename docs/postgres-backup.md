# مستند پشتیبان‌گیری شبانه پایگاه داده PostgreSQL (Database Backup Guide)

این سند راهنمای پشتیبان‌گیری خودکار، نگهداری دوره‌ای و بازیابی پایگاه داده PostgreSQL فروشگاه بنده است.

## سازوکار اسکریپت پشتیبان‌گیری

اسکریپت `scripts/backup_postgres.sh`:
1. تنظیمات نام کاربری، رمز عبور و نام پایگاه داده را از فایل `.env` دریافت می‌کند.
2. با استفاده از ابزار `pg_dump` داخل کانتینر داکر `fastapi-e-commerce-db` (یا کلاینت محلی)، یک نسخه خروجی کامل و تمیز تهیه می‌کند.
3. فایل خروجی را با سطح فشرده‌سازی حداکثری (`gzip -9`) فشرده کرده و در پوشه `backups/` با برچسب زمانی دقیق ذخیره می‌نماید:
   `backup_directus_db_YYYYMMDD_HHMMSS.sql.gz`
4. نسخه‌های پشتیبان قدیمی‌تر از ۱۴ روز را به صورت خودکار پاکسازی (Prune) می‌کند تا فضای دیسک پر نشود.

## نحوه اجرای دستی

```bash
./scripts/backup_postgres.sh
```

## زمان‌بندی شبانه با استفاده از Cron

برای اجرای منظم شبانه در ساعت ۰۳:۰۰ بامداد، دستور زیر را در crontab سرور وارد نمایید:

```bash
crontab -e
```

خط زیر را به انتهای فایل اضافه کنید:

```cron
0 3 * * * /home/mhb/app/coding/easy-auto/scripts/backup_postgres.sh >> /var/log/bandeh_db_backup.log 2>&1
```

## نحوه بازیابی اطلاعات از فایل پشتیبان (Restore)

در صورت بروز مشکل یا نیاز به بازیابی داده‌ها:

```bash
# ۱. متوقف کردن موقت سرویس‌های وابسته
docker compose stop web directus

# ۲. تزریق مستقیم فایل پشتیبان فشرده به دیتابیس
gunzip -c /home/mhb/app/coding/easy-auto/backups/backup_directus_db_YYYYMMDD_HHMMSS.sql.gz | \
  docker exec -i -e PGPASSWORD="your_postgres_password" fastapi-e-commerce-db psql -U directus -d directus_db

# ۳. راه‌اندازی مجدد سرویس‌ها
docker compose start web directus
```
