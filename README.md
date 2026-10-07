# Fridge Recipe

Веб-застосунок на Django, який знає, що лежить у твоєму холодильнику, і підказує, що з цього приготувати.

Додаєш продукти → бачиш рецепти, які можна приготувати зараз або яким бракує одного-двох інгредієнтів → натискаєш «Приготував це», і продукти списуються з холодильника, а калорії потрапляють у журнал. Будь-який рецепт з інтернету можна імпортувати за посиланням: його розбирає Gemini, а ти перевіряєш чернетку перед збереженням.

## Можливості

**Холодильник**
- продукти з кількістю й терміном придатності, підсвічування того, що скоро зіпсується
- пошук продукту з підказками, «часто додають», калорійність і БЖУ на 100 г

**Рецепти**
- статус кожного рецепта: «Можна приготувати», «Майже» (з переліком, чого бракує), «Бракує багато»
- сторінка «Що приготувати?» з підказкою, який продукт докупити, щоб відкрити найбільше рецептів
- калорійність і БЖУ порції, розраховані з даних USDA FoodData Central
- пошук і фільтри: можна приготувати, майже, до 500 ккал, мої

**Імпорт рецептів за посиланням**
- підтримує будь-який сайт: спершу шукає розмітку schema.org/Recipe (JSON-LD), інакше бере текст сторінки
- Gemini перекладає кроки українською, зіставляє інгредієнти з довідником і переводить побутові міри в грами («склянка гречки» → 165 г)
- форма перевірки чернетки: вибір продукту, кількість, «за смаком», попередження для сумнівних рядків
- продукти, яких немає в довіднику, автоматично додаються з USDA
- імпортовані рецепти приватні: їх бачить лише власник, можна редагувати й видаляти
- фото не копіюються: показуються з сайту-джерела з підписом «Фото: домен», можна завантажити своє

**Журнал**
- що й коли приготовано, калорії за день і графік за тиждень

## Технології

- Python 3.14, Django 5.2, SQLite
- Bootstrap 5.3 з власною темною темою, Tabler Icons, Tom Select
- USDA FoodData Central API — калорійність і БЖУ продуктів
- Google Gemini API (`google-genai`) — розбір рецептів зі структурованою JSON-відповіддю
- BeautifulSoup — витягування рецепта зі сторінки
- `python-decouple` — налаштування через `.env`

## Безпека імпорту

Завантаження чужих сторінок — потенційно небезпечна операція, тому:
- дозволені лише `http`/`https` і лише публічні адреси (захист від SSRF, кожна переадресація перевіряється заново)
- враховується `robots.txt`
- таймаут 10 с, розмір сторінки до 2 МБ, лише HTML
- текст сторінки передається Gemini як дані, інструкції всередині нього ігноруються
- ліміт імпортів на користувача за добу (`IMPORTS_PER_DAY`)

## Встановлення

Потрібні Python 3.14 і Git.

```bash
git clone https://github.com/GreedyClark/Fridge_Recipe_App.git
cd Fridge_Recipe_App
python -m venv venv
```

Активація віртуального оточення:

```bash
# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Linux / macOS
source venv/bin/activate
```

Якщо PowerShell забороняє запуск скриптів, один раз виконай `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

```bash
pip install -r requirements.txt
```

### Налаштування `.env`

Скопіюй приклад і заповни ключі:

```bash
cp .env.example .env          # Windows: Copy-Item .env.example .env
```

| Змінна | Опис |
|---|---|
| `SECRET_KEY` | секретний ключ Django, згенерувати: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DEBUG` | `True` для розробки |
| `ALLOWED_HOSTS` | через кому, наприклад `127.0.0.1,localhost` |
| `USDA_API_KEY` | ключ USDA FoodData Central |
| `GEMINI_API_KEY` | ключ Google Gemini; без нього імпорт вимкнений, решта застосунку працює |
| `GEMINI_MODEL` | основна модель, наприклад `gemini-3.8-flash` |
| `GEMINI_FALLBACK_MODELS` | запасні моделі через кому — використовуються, коли основна перевантажена (503) або вичерпано її ліміт (429) |
| `IMPORTS_PER_DAY` | скільки імпортів на добу дозволено одному користувачу (за замовчуванням 20) |

Файл називається саме `.env` — з крапкою на початку і без `.txt`.

### Як отримати ключі

**USDA FoodData Central** — безкоштовно: заповни форму на https://fdc.nal.usda.gov/api-key-signup.html, ключ прийде на пошту.

**Google Gemini** — безкоштовний тариф: увійди на https://aistudio.google.com з Google-акаунтом → **Get API key** → **Create API key**. Список доступних моделей для твого ключа:

```bash
python manage.py shell -c "from google import genai; from django.conf import settings; [print(m.name) for m in genai.Client(api_key=settings.GEMINI_API_KEY).models.list() if 'flash' in m.name]"
```

### База даних

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py import_products_from_usda   # довідник продуктів з USDA (потрібен USDA_API_KEY)
python manage.py loaddata initial_recipes    # 8 стартових рецептів
python manage.py runserver
```

Застосунок відкриється на http://127.0.0.1:8000/, адмінка — на `/admin/`.

## Тести

```bash
python manage.py test
```

Тести не звертаються до мережі: USDA, Gemini і завантаження сторінок підмінені.

## Структура

```
accounts/            реєстрація
fridge/              продукти й холодильник
  usda.py            клієнт USDA FoodData Central
recipes/             рецепти, «Що приготувати?», журнал
  services.py        підбір рецептів за вмістом холодильника, списання продуктів
  importing/
    fetch.py         безпечне завантаження сторінки
    extract.py       JSON-LD або текст сторінки
    gemini.py        розбір рецепта через Gemini
    pipeline.py      посилання → чернетка
    save.py          збереження й редагування рецепта
templates/           шаблони
static/css/          тема оформлення
```
