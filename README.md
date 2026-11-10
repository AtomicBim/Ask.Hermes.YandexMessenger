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

* Hermes Agent с plugin-платформами (проверено на commit `b200f5bc`, Python 3.14 venv Hermes). Дополнительных pip-зависимостей нет: `aiohttp` уже в Hermes.
* Бот в Яндекс 360 (`admin.yandex.ru/bot-platform`) и его OAuth-токен.

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
* **Модель.** Либо в `.env` (`HERMES_PROVIDER`, `HERMES_MODEL` — применяются на каждом старте), либо один раз вручную: `docker compose exec hermes hermes model`. Ключ провайдера (`OPENROUTER_API_KEY` и т.п.) — в `.env`.
* **Один токен — один gateway.** Остановите всё, что уже опрашивает этого бота (старый RAG-бот, Hermes на хосте: `hermes gateway stop`), иначе процессы будут отнимать апдейты друг у друга.
* **Версия Hermes.** По умолчанию `nousresearch/hermes-agent:latest`. Для воспроизводимости закрепите тег: `HERMES_IMAGE=nousresearch/hermes-agent:<tag>` в `.env` (или `--build-arg`). Совместимость плагина с образом проверяется `bash scripts/run_tests_docker.sh`.
* **Обновление.** `docker compose build --pull && docker compose up -d` — плагин переустанавливается из образа; данные в томе сохраняются. Правки плагина внутри тома перезаписываются.
* **Команды Hermes внутри контейнера:** `docker compose exec hermes hermes config show`, `... hermes plugins list`, `... hermes gateway status`.
* **Резервная копия тома:** `docker run --rm -v hermes-data:/data -v "$PWD":/backup alpine tar czf /backup/hermes-data.tgz -C /data .`

Проверено: сборка, установка и включение плагина, выставление модели, подключение адаптера (на фиктивном токене получен корректный `Invalid token`), 99 тестов плагина на Hermes из образа. Живой диалог из контейнера с реальным токеном не прогонялся — для него нужно остановить gateway на хосте.

## Настройка (env или `platforms.yandex_messenger.extra` в `config.yaml`; env приоритетнее)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `YANDEX_MESSENGER_BOT_TOKEN` | — | OAuth-токен бота (обязательно) |
| `YANDEX_MESSENGER_ALLOWED_USERS` | — | Логины через запятую |
| `YANDEX_MESSENGER_ALLOW_ALL_USERS` | `false` | Разрешить всем (только для разработки) |
| `YANDEX_MESSENGER_ALLOWED_CHATS` | пусто = любые | ID групповых чатов, где бот отвечает |
| `YANDEX_MESSENGER_REQUIRE_MENTION` | `true` | В группах отвечать только на упоминание / reply / `/команду` |
| `YANDEX_MESSENGER_HOME_CHANNEL` | — | Чат для cron: `0/0/<uuid>`, логин или `chat:<id>` / `login:<x>` / `user:<guid>` |
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
# плагин (WSL, на реальном коде Hermes):
wsl -d Ubuntu-24.04 -- bash scripts/run_tests_wsl.sh -q
```

* Тесты плагина используют реальные `gateway.*` из установленного Hermes и поддельный Bot API на `aiohttp`; `HERMES_HOME` — временный каталог, `~/.hermes` не затрагивается. pytest ставится в `~/.cache/ym-plugin-test-deps`, а не в venv Hermes.
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
scripts/   sync_client.py  install_wsl.sh  run_tests_wsl.sh  probe_api.py
tests/     inbound / outbound / interactive / transport / registration / discovery
docs/      API_FINDINGS.md
```
