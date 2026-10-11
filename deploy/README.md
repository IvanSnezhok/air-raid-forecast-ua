# Запуск без ноутбука: GitHub Actions + демо-сторінка

Workflow лежать у `.github/workflows/`, генератор сторінки в `demo/make_site.py`.
`telegram.yml` пропускає роботу (лишаючись зеленим), поки в репо немає
`text-features/` або не задано секрет `TG_SESSION_STRING`.

## Що робить

| Workflow | Розклад | Що робить | Секрети |
|---|---|---|---|
| `forecast.yml` | щогодини (можна вручну) | качає свіжий датасет, `predict_now.py`, будує `site/index.html` і кладе його як артефакт запуску (на Pages публікує `pages.yml`, вебінтерфейс) | `ALERTS_IN_UA_TOKEN` (необовʼязково) |
| `telegram.yml` | кожні 30 хв | `collect_telegram.py backfill` дозбирує нові пости і комітить їх у гілку `telegram-data` | `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_STRING` |

Один прогноз займає близько 1 хв на 4 ядрах (заміряно), на раннері GitHub
з установкою залежностей очікуємо 2-3 хв.

## Хвилини і де дивитися демо

* Публічний репозиторій: Actions і Pages безкоштовні без ліміту, прогноз можна
  ганяти щогодини (`cron: "7 * * * *"`). Секрети лишаються прихованими.
* Приватний на Free: 2000 хв/міс. Прогноз кожні 3 год ~720 хв, Telegram кожні
  30 хв ~1500 хв, разом впритул до ліміту. Pages для приватного репо недоступні
  без Pro (Pro безкоштовний у GitHub Student Developer Pack).
* Без Pages сторінку можна скачати з артефакту запуску (`forecast` -> `site/index.html`).

Pages публікує `pages.yml` (вебінтерфейс): Settings -> Pages -> Source: GitHub Actions.
Токен alerts.in.ua для живого стану: секрет репозиторію `ALERTS_IN_UA_TOKEN`
(Settings -> Secrets and variables -> Actions -> Secrets).

## Telegram

`TG_SESSION_STRING` створюється один раз локально: `python deploy/tg_session_string.py`
(з `TG_API_ID`/`TG_API_HASH` у середовищі). Це повний вхід у Telegram-акаунт,
краще окремий акаунт. Колектор уже читає цю змінну.
