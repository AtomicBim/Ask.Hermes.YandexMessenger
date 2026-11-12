# Ask.Hermes.YandexMessenger

Платформенный плагин **Яндекс Мессенджера** (Bot API, Яндекс 360 для бизнеса) для [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Подключает бота как ещё один канал gateway — наравне с Telegram/Slack: тот же агент, те же сессии, инструменты и cron.

## Возможности

| Область | Что реализовано |
|---|---|
| Транспорт | `getUpdates` (polling, по умолчанию, offset сохраняется на диск) или webhook (если задан `YANDEX_MESSENGER_WEBHOOK_URL`) |
| Ответы | Markdown → разметка Яндекса (`**жирный**`, `__курсив__`, `~~зачёркнутый~~`, код, ссылки; заголовки и таблицы конвертируются), авто-разбиение > 6000 симв. с сохранением ``` блоков |
| Стриминг | Правка сообщения (`sendText` с `message_id`), бюджет 1 правка/сек на чат, обработка 429 и переполнения 6000 |
| Индикаторы | `sendTyping` (`text` в группах, `processing` + статус-текст в личных чатах), реакции «взял в работу / готово / ошибка» |
| Медиа | Вход: файлы и картинки (оригинал из вариантов), голос, пересланные сообщения, лимит размера. Выход: `MEDIA:` → `sendFile` / `sendImage` / `sendGallery` (альбомы до 10) |
| Кнопки | Подтверждение опасных команд, уточняющие вопросы (`clarify`), slash-confirm, выбор модели (с пагинацией и группами провайдеров), выбор значения, запрос обновления. Нажатия — `server_action`; на время обработки карточка блокируется `set_elements_state` |
| Группы | Реакция только на `@бота`, reply боту или `/команду` (`REQUIRE_MENTION`), фильтр `ALLOWED_CHATS` |
| Авторизация | По `from.login` через стандартные `YANDEX_MESSENGER_ALLOWED_USERS` / `ALLOW_ALL_USERS` и pairing Hermes; нажавший кнопку проверяется отдельно (fail-closed) |
| Cron / `send_message` | `standalone_sender_fn` — доставка без запущенного gateway |

## Требования

* Hermes Agent с plugin-платформами. Тесты проходят на двух версиях: хостовая установка (commit `b200f5bc`) и образ `nousresearch/hermes-agent:latest` (commit `3b421578`); обе на Python 3.14. Дополнительных pip-зависимостей нет: `aiohttp` уже в Hermes.
* Бот в Яндекс 360 (`admin.yandex.ru/bot-platform`) и его OAuth-токен.
* Логин пользователя в `YANDEX_MESSENGER_ALLOWED_USERS` — это его рабочая почта в Яндекс 360 (`name@company.ru`), регистр не важен.

## Статус проверки

| Проверено вживую (реальный бот, личный чат) | Покрыто только тестами |
|---|---|
| polling, диалог и стриминг, реакции, `/model` (кнопки), картинка и файл туда-обратно, карточка подтверждения команды, `processing`-typing | `clarify`, slash-confirm, запрос обновления, группы и упоминания, webhook, cron/`standalone_send`, галереи |

Поведение Bot API, подтверждённое экспериментально, — в `docs/API_FINDINGS.md`.

## Установка

```bash
# WSL / Linux, из корня проекта
python scripts/sync_client.py                      # (только при разработке) обновить vendored-клиент
bash scripts/install_wsl.sh                        # копирует плагин в ~/.hermes/plugins/platforms/ и делает `hermes plugins enable`
bash scripts/install_wsl.sh --link                 # то же, но симлинком (удобно при разработке)

echo 'YANDEX_MESSENGER_BOT_TOKEN=<токен>'         >> ~/.hermes/.env
echo 'YANDEX_MESSENGER_ALLOWED_USERS=login1,login2' >> ~/.hermes/.env
hermes gateway restart
```

Пользовательские плагины Hermes работают по принципу opt-in: ключ плагина — `platforms/yandex_messenger`
(`hermes plugins enable platforms/yandex_messenger`, либо `plugins.enabled` в `config.yaml`).
Токен скрипт нигде не сохраняет.

## Docker: Hermes + бот в одном контейнере

Образ собирается поверх официального `nousresearch/hermes-agent`; плагин запекается в образ и при каждом старте
контейнера ставится в `$HERMES_HOME` и включается (`docker/cont-init-yandex-messenger.sh`, s6 cont-init).
Состояние (config, сессии, память, skills) — в томе `hermes-data`, пересборка образа его не трогает.

```bash
cp .env.example .env            # YANDEX_MESSENGER_BOT_TOKEN, YANDEX_MESSENGER_ALLOWED_USERS, ключ LLM
docker compose up -d --build
docker compose logs -f hermes   # ждём: "yandex_messenger connected ... (polling mode)"
```

* **Входящие порты не нужны**: polling работает за NAT. Для webhook раскомментируйте `ports:` в `docker-compose.yml` и задайте `YANDEX_MESSENGER_WEBHOOK_URL` / `_SECRET`.
* **Основная модель.** По умолчанию — из `config/hermes.yaml` (см. ниже). Можно сменить вручную: `docker compose exec hermes hermes model`. Переменные `HERMES_PROVIDER` / `HERMES_MODEL` в `.env` — жёсткое переопределение на **каждом** старте (перекрывают и сид, и ручной выбор).
* **Минимум для старта:** `YANDEX_MESSENGER_BOT_TOKEN`, `YANDEX_MESSENGER_ALLOWED_USERS` и `OPENROUTER_API_KEY` (для голоса и озвучки ещё `GROQ_API_KEY`, `GEMINI_API_KEY`). Без `ALLOWED_USERS` (и без `ALLOW_ALL_USERS`) боту никто не сможет писать.
* **Один токен — один gateway.** Остановите всё, что уже опрашивает этого бота (старый RAG-бот, Hermes на хосте: `hermes gateway stop`), иначе процессы будут отнимать апдейты друг у друга.
* **Настройки Hermes (модели и пр.)** лежат в `config/hermes.yaml` и применяются при старте контейнера через `hermes config set`: основная модель и провайдер, `delegation` (субагенты), `stt` (Groq), `auxiliary.vision`, `image_gen`, `tts` (Gemini). Применяются при первом старте и **при каждом изменении файлов**; ключи, которых в файле нет, не трогаются, ручные правки (`hermes model`) переживают перезапуск, пока сид не менялся. Правка: изменить файл и `docker compose restart hermes` (каталог `config/` смонтирован, пересборка не нужна). `HERMES_CONFIG_RESEED=1` — принудительно применить заново.
* **Специфичные для организации значения** (`stt.prompt` со словарём имён и названий) — в `config/hermes.local.yaml`: он лежит в репозитории, запекается в образ и накладывается поверх `hermes.yaml`. **Репозиторий публичный — не кладите туда ничего секретного** (ключи API — только в `.env`).
* **Ключи для этих моделей** — в `.env`: `OPENROUTER_API_KEY` (чат, субагенты, vision, image_gen), `GROQ_API_KEY` (STT), `GEMINI_API_KEY` (TTS).
* **Прокси / VPN.** Инструменты Hermes (LLM, Groq, Gemini) учитывают `HTTP(S)_PROXY` / `ALL_PROXY` / `NO_PROXY` из `.env`. Для VPN-клиента на хосте: `HTTPS_PROXY=http://host.docker.internal:<порт>` (mapping `host.docker.internal` уже в `docker-compose.yml`; клиент должен слушать не только `127.0.0.1`). По умолчанию `NO_PROXY=localhost,127.0.0.1,.yandex.net,.yandex.ru`, поэтому **бот Яндекса ходит напрямую**, мимо VPN. Свой `NO_PROXY` из `.env` заменяет умолчание — оставьте в нём домены Яндекса. Отдельный прокси только для бота: `YANDEX_MESSENGER_PROXY` (приоритетнее `HTTPS_PROXY` и `NO_PROXY`); форматы `http(s)://[user:pass@]host:port` и `socks5(h)://`. Прокси касается API, скачивания файлов, CDN и cron-доставки; в логе адрес печатается без учётных данных.
* **Версия Hermes.** По умолчанию `nousresearch/hermes-agent:latest`. Для воспроизводимости закрепите тег: `HERMES_IMAGE=nousresearch/hermes-agent:<tag>` в `.env` (или `--build-arg`). Совместимость плагина с образом проверяется `bash scripts/run_tests_docker.sh`.
* **Обновление.** `docker compose build --pull && docker compose up -d` — плагин переустанавливается из образа; данные в томе сохраняются. Правки плагина внутри тома перезаписываются.
* **Команды Hermes внутри контейнера:** `docker compose exec hermes hermes config show`, `... hermes plugins list`, `... hermes gateway status`.
* **Резервная копия тома:** `docker run --rm -v hermes-data:/data -v "$PWD":/backup alpine tar czf /backup/hermes-data.tgz -C /data .`

Проверено: сборка, установка и включение плагина, выставление модели, подключение адаптера (на фиктивном токене получен корректный `Invalid token`), 99 тестов плагина на Hermes из образа (1 пропуск — проверка vendored-клиента, рядом нет `yandex_bot_example`). Живой диалог из контейнера с реальным токеном не прогонялся — для него нужно остановить gateway на хосте.

## Настройка (env или `platforms.yandex_messenger.extra` в `config.yaml`; env приоритетнее)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `YANDEX_MESSENGER_BOT_TOKEN` | — | OAuth-токен бота (обязательно) |
| `YANDEX_MESSENGER_ALLOWED_USERS` | — | Логины через запятую |
| `YANDEX_MESSENGER_ALLOW_ALL_USERS` | `false` | Разрешить всем (только для разработки) |
| `YANDEX_MESSENGER_ALLOWED_CHATS` | пусто = любые | ID групповых чатов, где бот отвечает |
| `YANDEX_MESSENGER_REQUIRE_MENTION` | `true` | В группах отвечать только на упоминание / reply / `/команду` |
| `YANDEX_MESSENGER_HOME_CHANNEL` | — | Чат для cron: `0/0/<uuid>`, `<guid>_<guid>` (личный чат), логин или `chat:<id>` / `login:<x>` / `user:<guid>` |
| `YANDEX_MESSENGER_HOME_CHANNEL_NAME` | `Home` | Отображаемое имя home channel |
| `YANDEX_MESSENGER_POLL_INTERVAL` | `1.0` | Пауза между пустыми опросами, сек (мин. 0.2) |
| `YANDEX_MESSENGER_WEBHOOK_URL` | пусто = polling | Публичный **базовый** URL (`https://host`) |
| `YANDEX_MESSENGER_WEBHOOK_SECRET` | — | Секрет в пути (`{path}/{secret}`); **обязателен** в режиме webhook |
| `YANDEX_MESSENGER_WEBHOOK_HOST` / `_PORT` / `_PATH` | пусто / `8647` / `/yandex-messenger/webhook` | Привязка listener-а |
| `YANDEX_MESSENGER_MAX_FILE_MB` | `50` | Лимит входящего файла |
| `YANDEX_MESSENGER_REACTIONS` | `true` | Реакции-индикаторы |
| `YANDEX_MESSENGER_REACTION_ACK` / `_OK` / `_FAIL` | `eyes` / `like` / `dislike` | Имена реакций; ставятся только если чат их предлагает (`getChat.available_reactions`) |

В `extra` (без env): `reply_in_dm: true` — цитировать вопрос в личных чатах (по умолчанию выключено; в группах цитата зависит от `reply_to_mode`), `edit_budget_secs`.

### Webhook

Bot API не подписывает вызовы вебхука, поэтому единственная защита — секрет в URL. Без `WEBHOOK_SECRET` gateway
отказывается стартовать в режиме webhook. Яндекс ждёт ответа ~1 с: listener отвечает `200` сразу, обработка идёт в фоне.
На старте плагин сам вызывает `self/update` с `https://host/yandex-messenger/webhook/<secret>`. При возврате в polling
плагин сбрасывает зарегистрированный webhook (иначе `getUpdates` пуст).

## Адресация

Ответы всегда идут в `chat.id` (по документации он валиден как `chat_id` и для личных чатов). Строки «снаружи»
(home channel, cron, `send_message`) разбирает `Target.parse`: префиксы `chat:` / `login:` / `user:` явные;
значение с `/` (группы, `0/0/<uuid>`) или вида `<guid>_<guid>` (личные чаты) — `chat_id`, всё остальное — `login`.

## Разработка и тесты

```bash
# библиотека (Windows, из ../yandex_bot_example): python -m pytest
# плагин на Hermes, установленном в WSL/Linux:
wsl -d Ubuntu-24.04 -- bash scripts/run_tests_wsl.sh -q
# плагин на Hermes из собранного Docker-образа (после `docker compose build`):
bash scripts/run_tests_docker.sh
```

* Тесты плагина используют реальные `gateway.*` из Hermes и поддельный Bot API на `aiohttp`; `HERMES_HOME` — временный каталог, `~/.hermes` не затрагивается. pytest ставится в `~/.cache/ym-plugin-test-deps` (нужен `uv` или `pip` на хосте), а не в venv Hermes. Без Hermes (например, на чистой Windows) тесты пропускаются.
* `tests/test_discovery.py` загружает плагин настоящим `PluginManager` из пользовательского каталога.
* Клиент `yandex_messenger_client` — общий с `../yandex_bot_example`; копия лежит в `plugins/platforms/yandex_messenger/_vendor/`. Обновление: `python scripts/sync_client.py` (проверка актуальности: `--check`, она же в тестах).
* `scripts/probe_api.py` — эмпирическая проверка поведения Bot API на реальном токене (см. `docs/API_FINDINGS.md`).

## Структура

```
plugins/platforms/yandex_messenger/
  plugin.yaml  __init__.py  adapter.py        # адаптер, register(ctx)
  interactive.py                              # кнопки и карточки (InteractiveMixin)
  transport.py                                # polling + webhook
  standalone.py                               # доставка для cron
  settings.py  gateway_env.py  adapter_hint.py
  _vendor/yandex_messenger_client/            # vendored HTTP-клиент (не править, см. sync_client.py)
Dockerfile  docker-compose.yml  .env.example  .dockerignore
config/    hermes.yaml  hermes.local.yaml             # настройки Hermes: сид для контейнера (local накладывается поверх)
docker/    cont-init-yandex-messenger.sh  apply_config.py   # s6-шаг: плагин + применение сида через `hermes config set`
scripts/   sync_client.py  install_wsl.sh  run_tests_wsl.sh  run_tests_docker.sh  probe_api.py
tests/     test_inbound / test_outbound / test_interactive / test_transport / test_registration / test_discovery  (+ conftest, fake_yandex)
docs/      API_FINDINGS.md
```
