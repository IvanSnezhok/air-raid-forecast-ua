# Запуск без ноутбука: GitHub Actions, GitHub Pages, Hugging Face

Уся автоматизація лежить у `.github/workflows/`, логіка у `scripts/` і `.github/scripts/`.
Докладно (схема, що де зберігається, заміри часу): README, розділ «Automation».

| Workflow | Коли | Що робить | Секрети / змінні |
|---|---|---|---|
| `pipeline.yml` | кожні 10 хв (:03, :13, …) + keep-alive + ручний запуск / пінгер | опитування alerts.in.ua → нові пости Telegram і новини (гілка `text-data`) → прогноз (моделі з кешу: історія або історія + Telegram) → тип загрози → перевірка минулих прогнозів → статичний сайт з районним шаром → GitHub Pages; усе зберігається в гілках `live-data` і `text-data` | `ALERTS_IN_UA_TOKEN`; `PIPELINE_KEEPALIVE`, `PIPELINE_KEEPALIVE_MAX_HOPS` (необовʼязково) |
| `train.yml` | щодня, коли датасет посунувся на 6 год, вручну | навчає моделі областей (з відкладеним тижнем), районів і з Telegram-сигналами та кладе їх одним набором у кеш Actions | – |
| `text-collect.yml` | лише вручну | історія новин GDELT, дозбирання архіву Telegram (щоразу його поповнює `pipeline.yml`) | – |
| `collect.yml` | щодня 02:41 UTC | історія alerts.in.ua за місяць (API зберігає ~30 днів) у `live-data` | `ALERTS_IN_UA_TOKEN` |
| `threat-labels.yml` | щодня | пости Повітряних сил для моделі типу загрози (`threat-labels-data`) | – |
| `deploy-space.yml` | пуш у `main`, вручну | сервер FastAPI на Hugging Face Spaces (необовʼязково) | `HF_TOKEN`, змінна `HF_SPACE` |
| `ci.yml` | пуш у `main`, PR | тести | – |

## Що треба налаштувати власнику (один раз)

1. **Pages:** Settings → Pages → Build and deployment → Source: **GitHub Actions**.
   Середовище `github-pages` за замовчуванням приймає лише `main`; конвеєр і так публікує
   тільки з `main` (з інших гілок сайт лишається артефактом запуску).
2. **Секрет** `ALERTS_IN_UA_TOKEN`: Settings → Secrets and variables → Actions → Secrets.
   Без нього прогноз робиться станом на кінець волонтерського датасету.
3. **Keep-alive** (увімкнений за замовчуванням): змінна `PIPELINE_KEEPALIVE=off` його вимикає,
   `PIPELINE_KEEPALIVE_MAX_HOPS` (типово 72, близько 12 год) обмежує ланцюжок запусків без
   розкладу. Settings → Secrets and variables → Actions → Variables.
4. **Зовнішній пінгер (найнадійніше, рекомендовано).** Розклад GitHub працює «як вийде»:
   у цьому репозиторії щогодинний cron спрацював двічі за 14 год. Безкоштовний
   https://cron-job.org може викликати конвеєр точно кожні 10 хв:
   * GitHub → Settings (профілю) → Developer settings → Fine-grained personal access tokens →
     Generate: Repository access: Only select repositories → `air-raid-forecast-ua`;
     Permissions → Repository → **Actions: Read and write** (більше нічого). Строк дії: рік.
   * cron-job.org → Create cronjob:
     URL `https://api.github.com/repos/IvanSnezhok/air-raid-forecast-ua/actions/workflows/pipeline.yml/dispatches`,
     розклад: кожні 10 хв (наприклад хвилини 1, 11, 21, …),
     Advanced → Request method **POST**, заголовки
     `Authorization: Bearer <токен>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`, тіло
     `{"ref":"main","inputs":{"source":"pinger"}}`. Відповідь 204 означає успіх.
   * Тоді keep-alive можна вимкнути (`PIPELINE_KEEPALIVE=off`): пінгер його замінює. Подвійні
     запуски (cron + пінгер в один слот) коштують ~10 с: їх відсіює job `gate`.
5. **Hugging Face Space** (необовʼязково, сервер з API): секрет `HF_TOKEN` (токен з правом
   запису) і змінна `HF_SPACE` (наприклад `ivansnezhok/air-raid-forecast`), потім
   Actions → deploy-space → Run workflow. Space створюється автоматично, секрет
   `ALERTS_IN_UA_TOKEN` копіюється туди сам.

## Хвилини

Публічний репозиторій: стандартні раннери Actions і Pages безкоштовні без ліміту хвилин.
Один запуск конвеєра займає кілька хвилин (заміри в README), job не довше 30 хв
(ліміт GitHub 6 год), одночасно працює не більше одного запуску (`concurrency`).
Приватний репозиторій на Free має 2000 хв/міс: тоді розклад треба рідшати (наприклад
`*/30`) і вимкнути keep-alive.

## Telegram

Старий `telegram.yml` (Telethon, потребував `TG_SESSION_STRING`, жодного разу не відпрацював і
нічим не споживався) прибрано. Публічні канали збирає без секретів `airraid.text` у кожному
запуску конвеєра (`.github/scripts/text-data.sh`). `tg_session_string.py` лишається для
локальних експериментів.
