# Telegram через Telethon (необовʼязково)

Основний збір текстових сигналів тепер у пакеті `src/airraid/text/` і не потребує акаунта Telegram: `python -m airraid.text.collect` читає публічну вебверсію каналів (https://t.me/s/<канал>) і складає все в point-in-time архів на гілці `text-data`. Опис джерел, правило `available_at`, ознаки й результат абляції: розділ «Text signals» у головному `README.md`.

У цій папці лишився тільки альтернативний збирач через Telegram API (Telethon), `collect_telegram.py`. Він потрібен, якщо вебверсію каналу закриють або заблокують. Пише в той самий архів і в тому ж форматі, тому ознаки, тести й живий прогноз не залежать від того, хто саме побачив пост.

## Правило, яке не можна порушувати

Ознаки в момент прогнозу T рахуються лише з постів, у яких `available_at < T`:

| що ми знаємо про пост | `available_at` |
|---|---|
| коли ми вперше його побачили, він не був відредагований | `date` (час публікації): текст, який ми маємо, і є початковий |
| коли ми вперше його побачили, він уже був відредагований | `first_seen_at`: коли саме його змінили, невідомо, тож рахуємо від моменту, коли ми його побачили |

Перший побачений текст (`text_first`) ніколи не перезаписується, наступні редагування потрапляють лише в `text_last`. Telethon знає точний `edit_date`, але, щоб правило було одне для всіх збирачів, використовується те саме правило, що й для вебверсії.

## Запуск

```bash
pip install -r text-features/requirements.txt
export TG_API_ID=...     # https://my.telegram.org -> API development tools
export TG_API_HASH=...   # ніколи не комітити
git worktree add text-data origin/text-data   # архів
PYTHONPATH=src python text-features/collect_telegram.py backfill --root text-data --since 2025-01-01
PYTHONPATH=src python text-features/collect_telegram.py follow --root text-data   # live, тримати запущеним
```

Перший запуск попросить номер телефону і код; сесія зберігається в `~/.airraid_telegram.session`, поза репозиторієм. Для GitHub Actions сесію передають рядком у секреті `TG_SESSION_STRING` (разом з `TG_API_ID`, `TG_API_HASH`):

```bash
python -c "import os; from telethon.sync import TelegramClient; from telethon.sessions import StringSession; c=TelegramClient(StringSession(), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']); c.start(); print(c.session.save())"
```

Цей рядок дає повний доступ до акаунта Telegram: тільки в секрети, ніколи в код чи логи. Зараз секрет не заданий і цей шлях не використовується.

## Готові публічні датасети (перевірено 2026-10-11)

- **Kyrychenko, Telegram channels and posts about the Russo-Ukrainian war** (Zenodo, CC BY 4.0): новинні та пропагандистські канали до 2024-03-26. Для поточного прогнозу не придатний. https://zenodo.org/records/16949193
- **Massive Missile Attacks on Ukraine** (Kaggle, piterfm): нічні звіти Повітряних сил (запущено/збито). Завантажується без ключа (`threat-type/fetch_sources.py`). Невідомо, коли саме кожен рядок зʼявився в датасеті, тому як ознаку його не беремо; кількість дронів за ніч береться з ранкових звітів у каналі ПС, де час публікації відомий (кореляція з Kaggle 0.92 на 554 ночах).
- **lbgos/AirAlerts** (GitHub, без ліцензії): той самий підхід через Telethon. Ідеї беремо, код ні.
