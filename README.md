# Vinted AI Deal Hunter v2

Telegram-бот для пошуку свіжих вигідних оголошень на Vinted у Польщі.

Ти пишеш простий запит:

```text
/add ipad 10gen до 1000
/add apple watch se до 400
/add redmi pad pro до 750
```

А бот сам:

1. створює AI-фільтр;
2. записує його в Supabase;
3. перевіряє Vinted;
4. відсікає сміття;
5. оцінює оферту через AI;
6. рахує `Deal score`;
7. присилає тобі оферту в Telegram;
8. вчиться на твоєму фідбеку.

---

## Що нового у v2

### 1. AI-фільтри в базі, а не в коді

Фільтри не треба прописувати вручну в `main.py`.

Коли ти пишеш:

```text
/add ipad 10gen до 1000
```

бот через AI створює `filter_json` і записує його в таблицю `searches` у Supabase.

Кожен запит має свій окремий фільтр. Наприклад:

```text
/add ipad 11 до 1200
/add ipad 10 до 1000
```

це будуть два різні записи в `searches`, кожен зі своїм:

```text
id
keyword
vinted_query
max_price
filter_json
filter_summary
min_ai_score
```

Тобто вони не конфліктують між собою.

---

### 2. Deal score

Кожна оферта тепер має дві оцінки:

```text
AI-оцінка: 0-10
Deal score: 0-10
```

`AI-оцінка` — наскільки товар підходить і виглядає безпечно.

`Deal score` — наскільки оферта виглядає вигідною по ціні/стану.

Наприклад, годинник із маленькими подряпинами може мати не ідеальну AI-оцінку, але хороший Deal score, якщо ціна дуже добра.

---

### 3. Фідбек-кнопки під офертами

Під кожною офертою будуть кнопки:

```text
👍 Хороша
👎 Погана
🚫 Не той товар
💸 Не вигідно
⚠️ Підозріло
```

Коли ти натискаєш кнопку, бот записує фідбек у Supabase.

Для негативного фідбеку бот може оновити `filter_json` саме для цього пошуку. Наприклад, якщо він прислав не той товар, AI додасть ознаки неправильного товару в `wrong_product_any` або `reject_any`.

---

### 4. Менше шансів пропустити оголошення

У цій версії за замовчуванням:

```env
SKIP_UNKNOWN_AGE=false
```

Це означає: якщо Vinted API не віддає точний час додавання оголошення, бот не буде автоматично його викидати. Він все одно бере `newest_first`, перевіряє ціну, AI-фільтр, AI-оцінку і deduplication.

Це зроблено, щоб не було ситуації, коли в додатку Vinted ти бачиш “uploaded 6 min ago”, а API не віддав час і бот мовчки пропустив товар.

---

## Файли в проєкті

```text
main.py              головний код Telegram-бота
supabase.sql         SQL для створення/оновлення таблиць у Supabase
requirements.txt     бібліотеки Python
.env.example         приклад змінних середовища
Procfile             команда запуску для Railway
runtime.txt          версія Python для Railway
.gitignore           щоб не залити .env на GitHub
```

---

## Локальний запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

На Windows замість `source .venv/bin/activate`:

```bash
.venv\Scripts\activate
```

Перед запуском треба заповнити `.env`.

---

## .env

Приклад:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_service_role_or_secret_key
GROQ_API_KEY=your_groq_api_key

GROQ_MODEL=llama-3.1-8b-instant
FILTER_GENERATION_MODEL=llama-3.1-8b-instant

VINTED_BASE_URL=https://www.vinted.pl
VINTED_LOCALE=pl
VINTED_CURRENCY=PLN

CHECK_INTERVAL_SECONDS=300
MAX_ITEMS_PER_SEARCH=20
ONLY_RECENT_MINUTES=10
SKIP_UNKNOWN_AGE=false

MIN_AI_SCORE_TO_SEND=4
MIN_DEAL_SCORE_TO_SEND=0
DYNAMIC_AI_FILTERS_ENABLED=true
FEEDBACK_LEARNING_ENABLED=true
```

Не заливай реальний `.env` на GitHub.

---

## Supabase — як це працює

Важливе пояснення: Supabase — це не папка з SQL-файлами, з яких бот щось вибирає.

`supabase.sql` — це просто інструкція для бази даних.

Коли ти вставляєш текст у Supabase SQL Editor і натискаєш `Run`, Supabase виконує ці SQL-команди і створює/оновлює реальні таблиці в базі.

Після цього бот працює не з SQL-файлом, а з таблицями в базі.

Наприклад у коді є такі звернення:

```python
supabase.table("searches").select("*")
supabase.table("sent_items").insert(...)
supabase.table("offer_feedback").upsert(...)
```

Тобто бот бере інформацію не з “якогось одного SQL-файлу”, а з конкретних таблиць:

```text
users
searches
sent_items
offer_feedback
filter_learning_logs
```

---

## Що робить supabase.sql

У файлі є команди типу:

```sql
create table if not exists searches (...)
```

Це означає: “створи таблицю `searches`, якщо її ще немає”.

Є команди:

```sql
alter table searches add column if not exists filter_json jsonb;
```

Це означає: “додай колонку `filter_json`, якщо її ще немає”.

Є команди:

```sql
create index if not exists ...
```

Це означає: “створи індекс, якщо його ще немає”.

Тому `supabase.sql` можна запускати повторно. Він не повинен видалити старі дані, бо там стоїть `if not exists`.

---

## Таблиці

### `users`

Зберігає Telegram ID користувача.

### `searches`

Тут лежать твої активні пошуки.

Найважливіші поля:

```text
keyword         твій оригінальний запит
vinted_query    короткий запит, який бот дає Vinted
max_price       максимальна ціна
active          чи пошук активний
filter_json     AI-фільтр для цього конкретного пошуку
filter_summary  короткий опис фільтра
min_ai_score    мінімальна AI-оцінка для відправки
```

### `sent_items`

Історія вже відправлених оферт. Потрібна, щоб бот не спамив одне й те саме оголошення багато разів.

У v2 тут також зберігається:

```text
item_json
ai_json
ai_score
deal_score
```

### `offer_feedback`

Тут зберігаються твої натискання кнопок:

```text
👍 Хороша
👎 Погана
🚫 Не той товар
💸 Не вигідно
⚠️ Підозріло
```

### `filter_learning_logs`

Тут зберігається історія того, як AI змінював фільтр після твого фідбеку.

---

## Якщо хочеш почати з чистої бази

Якщо ти видаляєш старий GitHub-проєкт, але залишаєш той самий Supabase, старі записи в базі залишаться.

Щоб почати повністю з нуля, можна виконати в SQL Editor:

```sql
truncate table filter_learning_logs restart identity cascade;
truncate table offer_feedback restart identity cascade;
truncate table sent_items restart identity cascade;
truncate table searches restart identity cascade;
```

Це видалить старі пошуки, історію відправлених оферт і фідбек.

`users` можна не чистити. Але якщо хочеш повний reset:

```sql
truncate table users restart identity cascade;
```

---

## Команди Telegram

```text
/start
/help
/add ipad 10gen до 1000
/list
/filter ID
/refreshfilter ID
/delete ID
/clear
/check
/debug ipad
```

### `/add`

Додає пошук і створює AI-фільтр.

```text
/add apple watch se до 400
```

### `/list`

Показує активні пошуки.

### `/filter ID`

Показує AI-фільтр, який записаний у Supabase для конкретного пошуку.

### `/refreshfilter ID`

Перегенеровує AI-фільтр для існуючого пошуку.

### `/clear`

Деактивує всі активні пошуки. Історію відправлених оферт не чіпає.

### `/check`

Ручна перевірка Vinted.

### `/debug keyword`

Тестує, що Vinted API повертає по конкретному запиту.

---

## Як працює навчання на фідбеку

Приклад: бот прислав оферту для `#21 ipad 10gen`.

Ти натиснув:

```text
🚫 Не той товар
```

Бот робить так:

1. знаходить оферту в `sent_items`;
2. записує фідбек в `offer_feedback`;
3. бере пошук `#21` з `searches`;
4. бере старий `filter_json`;
5. відправляє в Groq AI: старий фільтр + оферта + твій фідбек;
6. отримує оновлений `filter_json`;
7. записує його назад у `searches` тільки для пошуку `#21`;
8. зберігає лог у `filter_learning_logs`.

Тобто фідбек по `#21` не змінює інші пошуки.

---

## Railway deploy

1. Створи новий GitHub repo.
2. Завантаж тільки файли з цієї папки.
3. Створи Railway project з GitHub repo.
4. Додай environment variables з `.env.example`.
5. Railway запустить бота через `Procfile`:

```text
worker: python main.py
```

---

## Рекомендовані налаштування для тесту

```env
CHECK_INTERVAL_SECONDS=300
ONLY_RECENT_MINUTES=10
SKIP_UNKNOWN_AGE=false
MIN_AI_SCORE_TO_SEND=4
MIN_DEAL_SCORE_TO_SEND=0
FEEDBACK_LEARNING_ENABLED=true
```

`MIN_DEAL_SCORE_TO_SEND=0` означає, що Deal score тільки показується, але не блокує оферти.

Коли бот буде стабільно добре працювати, можна поставити, наприклад:

```env
MIN_DEAL_SCORE_TO_SEND=6
```

Тоді бот буде присилати тільки більш вигідні оферти.
Vinted AI Deal Hunter v4 — Catch More Offers
Це чиста збірка тільки Vinted-агента. Версія v4 зроблена так, щоб бот менше пропускав хороші офери і не був заточений тільки під техніку.
Що є
Telegram-бот для Vinted.
`/add ipad 10gen до 1000` — ти пишеш людський запит.
AI сам створює `filter_json` і зберігає його в Supabase.
Підтримка різних категорій: техніка, Apple Watch, кросівки, одяг, Funko Pop, LEGO, фігурки, книги, сумки, косметика, домашні речі тощо.
Deal score для кожної оферти.
Кнопки фідбеку: 👍 Хороша, 👎 Погана, 🚫 Не той товар, 💸 Не вигідно, ⚠️ Підозріло.
Навчання фільтра після фідбеку.
`/debugsearch ID` — показує, чи бот бачить raw-офери з Vinted і чому вони проходять або відсікаються.
Що змінено у v4
Попередня версія могла нічого не скидати, бо була занадто обережна. У v4:
`ONLY_RECENT_MINUTES=60` замість короткого вікна 5–10 хв.
`MAX_ITEMS_PER_SEARCH=50`, щоб бот бачив більше raw-оголошень.
`MIN_AI_SCORE_TO_SEND=3`, щоб сумнівні, але потенційно вигідні офери не зникали мовчки.
`ryski`, `ślady użytkowania`, `drobne ślady`, `bez pudełka` не блокують офер одразу. Вони йдуть у ризики, а AI оцінює їх у повідомленні.
Жорстко відсікаються тільки очевидно неправильні речі: аксесуари замість товару, інші моделі, явні блокування/поломки, якщо AI-фільтр так вирішив.
Файли
`main.py` — основний бот.
`supabase.sql` — створює/оновлює таблиці в Supabase.
`.env.example` — приклад змінних середовища.
`requirements.txt` — бібліотеки.
`Procfile` — для Railway.
`runtime.txt` — версія Python.
Швидкий старт
Створи новий GitHub repo.
Закинь усі файли з цієї папки.
У Supabase відкрий SQL Editor.
Встав весь текст із `supabase.sql` і натисни `Run`.
У Railway додай змінні з `.env.example`.
Запусти deploy.
Команди Telegram
```text
/start
/help
/add ipad 10gen до 1000
/add apple watch se 2 до 400
/add nike dunk low до 150
/add funko pop harry potter до 60
/list
/filter ID
/refreshfilter ID
/debugsearch ID
/check
/delete ID
/clear
```
Як працює Supabase SQL Editor
SQL Editor — це не папка з файлами для бота. Коли ти вставляєш туди `supabase.sql` і натискаєш `Run`, Supabase виконує команди:
створює таблиці, якщо їх ще нема;
додає нові колонки, якщо їх ще нема;
створює індекси;
оновлює структуру бази.
Після цього бот не читає файл `supabase.sql`. Бот працює з реальними таблицями в базі через `SUPABASE_URL` і `SUPABASE_KEY`.
Тобто якщо в SQL Editor видно старі запити або старі файли — це не значить, що бот їх читає. Це просто історія або вкладки редактора. Важливі тільки таблиці в розділі Table Editor.
Основні таблиці:
`searches` — твої активні пошуки і AI-фільтри.
`sent_items` — що бот уже скидав, щоб не дублювати.
`offer_feedback` — твій фідбек по оферах.
`filter_learning_logs` — історія автооновлень фільтра.
Чистий старт у Supabase
Якщо хочеш очистити старі пошуки і sent history, після запуску `supabase.sql` можеш виконати:
```sql
truncate table filter_learning_logs restart identity cascade;
truncate table offer_feedback restart identity cascade;
truncate table sent_items restart identity cascade;
truncate table searches restart identity cascade;
```
Це видалить старі пошуки, старі офери і старий фідбек. Структура таблиць залишиться.
Важливі env-змінні
```env
ONLY_RECENT_MINUTES=60
MAX_ITEMS_PER_SEARCH=50
MIN_AI_SCORE_TO_SEND=3
MIN_DEAL_SCORE_TO_SEND=0
SKIP_UNKNOWN_AGE=false
REJECT_BAD_CONDITIONS=false
SOFTEN_MINOR_WEAR_WORDS=true
```
Якщо бот буде слати занадто багато сміття, можна зробити суворіше:
```env
MIN_AI_SCORE_TO_SEND=4
ONLY_RECENT_MINUTES=30
```
Якщо бот все ще пропускає офери, можна зробити ширше:
```env
ONLY_RECENT_MINUTES=120
MAX_ITEMS_PER_SEARCH=80
MIN_AI_SCORE_TO_SEND=3
```
Як перевірити, чому офер не прийшов
В Telegram:
```text
/debugsearch 21
```
Бот покаже перші raw-офери з Vinted і причину:
старіше ніж `ONLY_RECENT_MINUTES`;
пройде на AI;
відсіяно product filter;
відсіяно quality filter;
Vinted повернув 0 raw items.
Це потрібно саме для шліфування, щоб не гадати, бот не побачив офер чи сам його відкинув.
