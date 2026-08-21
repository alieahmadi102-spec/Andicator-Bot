# راهنمای اتصال ربات به MetaTrader 5

دو چیز جدا هستند، اشتباهشان نگیر:

| | چیست | کجا اجرا می‌شود |
|---|---|---|
| **اندیکاتور** `mt5/SNRZ_Indicator.mq5` | فقط **نشان می‌دهد** — زون، فلش، Entry/SL/TP، آلارم | داخل خود متاتریدر |
| **ربات** `bot/mt5_runner.py` | **اردر می‌زند** | پایتون، بیرون متاتریدر، از طریق API |

---

## بخش ۱ — اندیکاتور در متاتریدر (ساده، بدون پایتون)

۱. متاتریدر ۵ را باز کن → `Tools` → `MetaQuotes Language Editor` (یا کلید `F4`).
۲. در MetaEditor: `File` → `Open Data Folder` → پوشه‌ی `MQL5\Indicators`.
۳. فایل `SNRZ_Indicator.mq5` را داخل همان پوشه کپی کن.
۴. برگرد به MetaEditor، فایل را باز کن و **`F7`** بزن (Compile).
   باید بنویسد `0 errors, 0 warnings`.
۵. در متاتریدر: `View` → `Navigator` (`Ctrl+N`) → `Indicators` → روی
   `SNRZ_Indicator` دوبار کلیک کن تا روی چارت بیفتد.
۶. برای آلارم موبایل: در تنظیمات اندیکاتور `InpAlertPush` را `true` کن، و در
   متاتریدر `Tools` → `Options` → `Notifications` شناسه‌ی متاتریدر موبایلت
   (MetaQuotes ID) را وارد کن.

**اگر کامپایل خطا داد** متن خطا را برایم بفرست — نه اسکرین‌شات کل صفحه، فقط
همان خط قرمزِ پایین MetaEditor.

---

## بخش ۲ — ربات پایتون (اردر می‌زند)

### پیش‌نیاز
- **ویندوز** (یا ویندوز روی VPS). پکیج `MetaTrader5` روی لینوکس/مک کار نمی‌کند.
- پایتون ۳٫۹ تا ۳٫۱۲ — نسخه‌ی ۶۴ بیتی.
- متاتریدر ۵ باید **باز و لاگین‌شده** بماند تا ربات کار کند.

### قدم‌به‌قدم

**۱) پایتون را نصب کن** از python.org و موقع نصب تیک
`Add Python to PATH` را بزن.

**۲) در متاتریدر اجازه‌ی الگو تریدینگ را بده:**
`Tools` → `Options` → تب `Expert Advisors` → تیک
`Allow algorithmic trading` را بزن.

**۳) پروژه را بگیر و پکیج‌ها را نصب کن** — در Command Prompt:
```bat
git clone https://github.com/alieahmadi102-spec/Andicator-Bot.git
cd Andicator-Bot
pip install -r bot\requirements.txt
```
اگر گیت نداری، از صفحه‌ی گیت‌هاب دکمه‌ی `Code` → `Download ZIP` و بعد
اکسترکت کن.

**۴) تنظیمات را باز کن:** فایل `bot\mt5_runner.py` را با Notepad باز کن و
بالای فایل این چند خط را ببین:

```python
SYMBOL = "XAUUSD"     # اسم نماد را دقیقاً مثل بروکر خودت بنویس
TIMEFRAME_MIN = 60    # تایم تحلیل: 60 یعنی ۱ ساعته
RISK_PCT = 1.0        # ریسک هر معامله، درصد حساب (قانون کتاب: حداکثر ۱٪)
DRY_RUN = True        # True یعنی هیچ اردری فرستاده نمی‌شود
```

> ⚠ **اسم نماد** در هر بروکر فرق دارد: `XAUUSD` یا `XAUUSD.m` یا `GOLD` یا
> `XAUUSDm`. اسم دقیق را از پنجره‌ی `Market Watch` متاتریدر بردار و همان را
> بنویس، وگرنه ربات دیتا پیدا نمی‌کند.

**۵) اول در حالت تست اجرا کن** (اردر نمی‌زند، فقط چاپ می‌کند):
```bat
python bot\mt5_runner.py
```
باید ببینی:
```
MT5 connected: account 12345678 (MyBroker-Demo), balance 10000.0 USD
symbol XAUUSD · analysis TF 60m · risk 1.0% · mode DRY RUN (no orders sent)
warmed up with 1000 candles, waiting for new bars…
```
بعد هر وقت سیگنالی بیاید این‌طور چاپ می‌شود:
```
SIGNAL: Signal(index=..., side='buy', kind='PO2', zone='I.VR', ...)
  DRY_RUN — would BUY 0.05 XAUUSD @ 4712.30  SL 4698.60  TP1 4726.00
```

**۶) چند روز همین‌طور نگاهش کن.** اگر سیگنال‌ها را قبول داشتی، برو سراغ
**حساب دمو** و در فایل `DRY_RUN = False` کن. موقع اجرا یک بار از تو تأیید
می‌گیرد:
```
DRY_RUN is off, real orders will be sent. Press Enter to go on, or Ctrl+C to stop:
```

**۷) پول واقعی فقط بعد از اینکه روی دمو نتیجه گرفتی** — و طبق خود کتاب
(ص۵۶): از ۱۰۰۰$ فقط ۱۰۰$ وارد حساب کن، و در تارگت اول پول را بردار.

### متوقف کردن
در همان پنجره `Ctrl+C` بزن. ربات پوزیشن‌های باز را نمی‌بندد — آن‌ها با
SL/TP خودشان در متاتریدر بسته می‌شوند.

---

## بخش ۳ — ربات صرافی کریپتو

```bat
python bot\exchange_runner.py
```
تنظیماتش بالای فایل `bot\exchange_runner.py` است: `EXCHANGE`، `SYMBOL`،
`TIMEFRAME`، `RISK_PCT` و `DRY_RUN` (پیش‌فرض `True`). برای معامله‌ی واقعی
باید `apiKey` و `secret` صرافی را هم در همان فایل بگذاری.

---

## بخش ۴ — بک‌تست قبل از هر چیز

```bat
python bot\backtest.py candles.csv
```
CSV را از TradingView بگیر: روی چارت XAUUSD، منوی `⋮` بالای چارت →
`Export chart data`. ستون‌های لازم: `time,open,high,low,close`.

خروجی به تو می‌گوید هر ستاپ چطور تمام شده:
```
candles.csv  signals=42  TP1=6 TP2=4 TP3=9 BE=11 SL=12  win=45.2%  E=+0.31R
```

---

## مشکلات رایج

| پیام | علت و راه‌حل |
|---|---|
| `MetaTrader5 package not installed` | `pip install MetaTrader5` — فقط ویندوز |
| `MT5 init failed` | متاتریدر باز نیست، یا لاگین نیستی، یا پایتون ۳۲ بیتی است |
| ربات هیچ کندلی نمی‌گیرد | اسم نماد غلط است — از Market Watch کپی کن |
| `order_send` جواب `retcode=10027` | Algorithmic trading در تنظیمات متاتریدر خاموش است |
| `retcode=10014` (invalid volume) | حجم از حداقل بروکر کمتر شد؛ حساب خیلی کوچک است یا استاپ خیلی دور |

---

## ⚠ هشدار

این کد آموزشی است و تضمین سود ندارد. ترتیب درست: **بک‌تست ← DRY_RUN ← دمو ←
پول کم واقعی**. ریسک هر معامله حداکثر **۱٪**. مسئولیت استفاده با خودت است.
