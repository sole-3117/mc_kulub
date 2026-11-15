# MC Club Nazoratchi Bot

Python + python-telegram-bot + SQLite + APScheduler.

## Ishga tushirish

```bash
pip install -r requirements.txt
cp .env.example .env      # BOT_TOKEN va boshqalarni to'ldiring
python bot.py
```

1. Botni guruhga qo'shing va **admin** qiling: *a'zolarni cheklash/chiqarish* va *xabarlarni o'chirish* huquqlari bilan.
2. `GROUP_ID` bilmasangiz, uni bo'sh qoldirib ishga tushiring va guruhda `/id` yozing. Chiqqan ID ni `.env` ga qo'ying va qayta ishga tushiring.
3. Mentorlar — guruh adminlari (qo'shimchasi `MENTOR_IDS` orqali).

Koyeb/Render'da `DB_PATH` doimiy (persistent) volume ichida bo'lsin, aks holda qayta deploy qilinganda baza o'chadi. Bot polling rejimida ishlaydi (worker).

## Buyruqlar

| Kim | Buyruq | Vazifasi |
|---|---|---|
| Mentor (shaxsiy chat) | `/yangi` | Yangi mashg'ulot: nom, matn, havola, dedlayn → guruhga e'lon |
| Mentor | `/vazifalar` | Ochiq mashg'ulotlar ro'yxati |
| Mentor | `/bekor <raqam>` | Ochiq mashg'ulotni bekor qilish |
| Mentor (guruhda, reply) | `/read` yoki `/doc` | "Javob qo'llanmada bor" deb javob berish |
| A'zo (shaxsiy chat) | `/uzr sabab` | Sababli kechikish so'rovi |
| Hamma | `/id` | Chat va foydalanuvchi ID |

## Topshirish usullari
- botga shaxsiy xabar (havola, fayl, skrinshot);
- guruhda e'longa reply qilib;
- guruhda `#topshiriq` yoki `#topshiriq3` xeshtegi bilan.

## Qoidalar (kodda qabul qilingan qarorlar)
- Dedlaynda **topshirmagan yoki "Qayta ishlang" deb rad etilgan** a'zoga strike yoziladi. Hali ko'rib chiqilmagan (kutilayotgan) topshiriq topshirilgan hisoblanadi.
- Strike faqat mashg'ulot e'lon qilinishidan **oldin** ro'yxatdan o'tgan a'zolarga yoziladi. Mentor/adminlarga yozilmaydi.
- Tasdiqlangan uzr bo'lsa, strike yozilmaydi. Uzr dedlayndan kamida `EXCUSE_HOURS` soat oldin yuborilishi kerak.
- Dedlayn o'tgach topshirish yopiladi. Dedlaynlar har 30 soniyada tekshiriladi (bot o'chiq bo'lsa, yoqilganda darhol bajariladi).
- Guruhdan chiqarilgan a'zo qayta ro'yxatdan o'tsa, strikelari nolga tushadi.
- Faqat tugmani bosib rozilik bildirgan a'zolar hisobga olinadi; botdan oldin guruhda bo'lganlar avtomatik ro'yxatga olinmaydi.
