# Ask.Hermes.YandexMessenger

Платформенный плагин **Яндекс Мессенджера** (Bot API, Яндекс 360 для бизнеса) для [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Подключает бота как ещё один канал gateway — наравне с Telegram/Slack: тот же агент, те же сессии, инструменты и cron.

* [Быстрый старт на Ubuntu (Docker)](#быстрый-старт-на-ubuntu-docker)
* [Docker: настройка и эксплуатация](#docker-настройка-и-эксплуатация)
* [Обновление плагина и Hermes (для администратора)](#обновление-плагина-и-hermes-для-администратора)
* [Установка без Docker](#установка-без-docker)
* [Возможности](#возможности) · [Переменные](#переменные-плагина) · [Разработка](#разработка-и-тесты)

## Быстрый старт на Ubuntu (Docker)

Репозиторий копируется командой `git clone` (она сама создаёт папку), дальше всё запускается из неё. Ставить Hermes
на сам сервер **не нужно**: он уже внутри Docker-образа вместе с плагином и настройками.

### Что нужно заранее

| Что | Как проверить |
|---|---|
| Ubuntu 22.04 / 24.04, свободно **~6 ГБ** на диске (образ ~4,6 ГБ) | `df -h /var/lib/docker` |
| Docker Engine и плагин Compose v2 | `docker --version && docker compose version` |
| `git` | `git --version` |
| Исходящий интернет: Docker Hub, Яндекс, OpenRouter (и Groq / Google, если нужны голос и озвучка) | — |
| Токен бота Яндекс, логин (почта) пользователя, ключ OpenRouter | бот создаётся в `admin.yandex.ru/bot-platform` |

Если `docker compose version` не находит команду — поставьте Docker по [официальной инструкции](https://docs.docker.com/engine/install/ubuntu/)
(пакет `docker-ce` вместе с `docker-compose-plugin`). Если `docker` просит `sudo`:
`sudo usermod -aG docker $USER && newgrp docker`.

### Шаги

```bash
# 1. Скопировать репозиторий и зайти в него
git clone https://github.com/AtomicBim/Ask.Hermes.YandexMessenger.git
cd Ask.Hermes.YandexMessenger

# 2. Создать файл настроек и заполнить его (токены хранятся только здесь)
cp .env.example .env
chmod 600 .env
nano .env

# 3. Собрать образ и запустить (первая сборка скачивает ~4,5 ГБ)
docker compose up -d --build

# 4. Проверить запуск (выйти из просмотра: Ctrl+C, контейнер продолжит работать)
docker compose logs -f hermes
```

### Что вписать в `.env`

| Переменная | Что это |
|---|---|
| `YANDEX_MESSENGER_BOT_TOKEN` | OAuth-токен бота Яндекс (**обязательно**) |
| `YANDEX_MESSENGER_ALLOWED_USERS` | рабочая почта пользователя; несколько — через запятую (**обязательно**: иначе боту никто не сможет писать) |
| `YANDEX_MESSENGER_ADMIN_USERS` | почта администратора (несколько — через запятую). **Только он может менять модель и настройки**; без этой переменной ограничений нет и модель может сменить любой пользователь — см. [Администраторы и пользователи](#администраторы-и-пользователи) |
| `OPENROUTER_API_KEY` | ключ OpenRouter: основная модель, субагенты, зрение, генерация картинок (**обязательно**) |
| `GROQ_API_KEY` | распознавание голоса (нужен, если включён `stt`) |
| `GEMINI_API_KEY` | озвучка ответов (нужен, если включён `tts`) |
| `HERMES_IMAGE` | версия Hermes; **уже закреплена** в `.env.example`, не меняйте без необходимости — см. [Обновление](#обновление-плагина-и-hermes-для-администратора) |
| `HTTPS_PROXY`, `NO_PROXY` | если выход в интернет идёт через VPN/прокси — см. [Прокси](#прокси--vpn) |

Модели и остальные настройки Hermes уже заданы в `config/hermes.yaml` (+ `config/hermes.local.yaml`) и применяются сами.

### Как понять, что всё работает

В `docker compose logs hermes` должны появиться строки:

```
[yandex-messenger] plugin installed ...
[yandex-messenger] plugin enabled (platforms/yandex_messenger)
[apply-config] model.default = ...
[yandex-messenger] seed config applied from /opt/hermes-seed
... yandex_messenger connected as <логин-бота> (polling mode)
```

После этого напишите боту в Мессенджере с разрешённой почты — он ответит. Входящие порты открывать **не нужно**:
бот сам опрашивает Яндекс (polling), это работает за NAT и файрволом.

> **Один токен — один запущенный gateway.** Остановите всё, что уже опрашивает этого бота (старый бот, Hermes на другой
> машине или в WSL: `hermes gateway stop`), иначе процессы будут отнимать друг у друга сообщения.

### Если что-то не так

| Симптом | Причина и что делать |
|---|---|
| `env file ... .env not found` | не создан `.env`: `cp .env.example .env` |
| `permission denied ... docker.sock` | `sudo usermod -aG docker $USER && newgrp docker` |
| В логе `Yandex Messenger rejected the bot token ... Invalid token` | неверный токен; поправьте `.env` и `docker compose up -d` (контейнер пересоздастся с новыми значениями) |
| Бот молчит, в логе нет ошибок | почта не в `YANDEX_MESSENGER_ALLOWED_USERS` (должна совпадать с логином в Яндекс 360); либо этого бота ещё кто-то опрашивает |
| Бот отвечает ошибкой модели | нет/неверный `OPENROUTER_API_KEY`; проверьте `docker compose exec hermes hermes config show` |
| Голос / озвучка не работают | нет `GROQ_API_KEY` / `GEMINI_API_KEY` или нужен прокси к этим сервисам |
| Изменил `config/hermes.yaml`, ничего не поменялось | `docker compose restart hermes`; принудительно: `HERMES_CONFIG_RESEED=1` в `.env` |

## Docker: настройка и эксплуатация

Образ собирается поверх официального `nousresearch/hermes-agent`; плагин запекается в образ и при каждом старте
контейнера ставится в `$HERMES_HOME` и включается (`docker/cont-init-yandex-messenger.sh`, s6 cont-init).
Состояние (config, сессии, память, skills) — в томе `hermes-data`, пересборка образа его не трогает.

### Повседневные команды (из папки репозитория)

```bash
docker compose ps                        # состояние
docker compose logs -f hermes            # логи
docker compose restart hermes            # перезапуск (например, после правки config/)
docker compose stop                      # остановить (данные сохраняются)
docker compose up -d                     # запустить снова / применить изменения .env
docker compose exec hermes hermes config show      # настройки Hermes внутри контейнера
docker compose exec hermes hermes model            # сменить модель вручную (администратор, на сервере)
```

Обновление плагина и Hermes — в отдельном разделе [Обновление](#обновление-плагина-и-hermes-для-администратора).

**Резервная копия тома** и **полное удаление**:

```bash
docker run --rm --volumes-from hermes-yandex -v "$PWD":/backup alpine tar czf /backup/hermes-data.tgz -C /opt/data .
docker compose down            # удалить контейнер, данные остаются
docker compose down -v         # удалить и данные (сессии, память, skills)
```

(Копия содержит `.env`-подобные данные Hermes — `auth.json`, `.env`, сессии: храните её как секрет.)

### Администраторы и пользователи

Задайте `YANDEX_MESSENGER_ADMIN_USERS=admin@company.ru` в `.env` — включается разграничение команд Hermes
(`gateway/slash_access.py`), одно и то же в личных чатах и группах:

| | Администратор | Обычный пользователь (из `ALLOWED_USERS`) |
|---|---|---|
| Обычный диалог, файлы, голос, картинки | да | да |
| `/model`, `/config`, `/reasoning`, `/fast`, `/personality`, `/tools`, `/skills`, `/yolo`, `/approvals`, `/plugins`, `/reload*`, `/restart`, `/update`, `/sethome`, `/cron`, `/login` и все остальные команды | да | **нет** |
| `/help`, `/whoami` | да | да |
| `/new`, `/stop`, `/status`, `/retry`, `/undo`, `/history`, `/title`, `/resume`, `/sessions`, `/queue`, `/usage`, `/approve`, `/deny`, `/version` | да | да (список меняется переменной `YANDEX_MESSENGER_USER_COMMANDS`) |
| Кнопки выбора модели и значений (`/model`, `/reasoning`) | да | нет |
| Кнопки подтверждения опасной команды | да | да, если пользователь авторизован в боте |

Пользователь, набравший `/model`, получает отказ от Hermes. Логин администратора пишется так же, как в Яндекс 360;
регистр сравнивается точно.

Администратор меняет модель: через `/model` в чате, либо правкой `config/hermes.yaml` и `docker compose restart hermes`,
либо `docker compose exec hermes hermes model` на сервере. Без `ADMIN_USERS` ограничений нет, и при старте в
`docker compose logs` будет предупреждение `slash-command gating OFF`. Что разграничение включено, видно в файле журнала
(в консоль контейнера строка не попадает):

```bash
docker compose exec hermes grep "slash-command gating" /opt/data/logs/gateway.log
# ... slash-command gating ON: 1 admin(s); other users may run /help /whoami and /approve, /deny, ...
```

> Запрет касается команд и кнопок. Это не изоляция: обычный пользователь по-прежнему просит агента выполнять задачи
> с правами контейнера. Опасные команды требуют подтверждения, но при `approvals.mode: smart` (режим Hermes по умолчанию
> в некоторых установках) часть из них подтверждается автоматически; `manual` просит подтверждение для каждой.

### Настройки Hermes (модели и пр.)

Лежат в `config/hermes.yaml` и применяются при старте контейнера через `hermes config set`: основная модель и провайдер,
`delegation` (субагенты), `stt` (Groq), `auxiliary.vision`, `image_gen`, `tts` (Gemini).

* Применяются при первом старте и **при каждом изменении файлов** в `config/`; ключи, которых в файлах нет, не трогаются;
  ручные правки (`hermes model`) переживают перезапуск, пока сид не менялся. `HERMES_CONFIG_RESEED=1` — применить заново принудительно.
* Правка: изменить файл и `docker compose restart hermes` (каталог `config/` смонтирован, пересборка не нужна).
* `config/hermes.local.yaml` (словарь `stt.prompt`) накладывается поверх `hermes.yaml`; он лежит в репозитории и запечён в образ.
  **Репозиторий публичный — не кладите в `config/` ничего секретного**; ключи API — только в `.env`.
* `HERMES_PROVIDER` / `HERMES_MODEL` в `.env` — жёсткое переопределение основной модели на **каждом** старте (перекрывает и сид, и ручной выбор).

### Прокси / VPN

Инструменты Hermes (LLM, Groq, Gemini) учитывают `HTTP(S)_PROXY` / `ALL_PROXY` / `NO_PROXY` из `.env`.

* VPN-клиент на самом сервере: `HTTPS_PROXY=http://host.docker.internal:<порт>` (имя `host.docker.internal` уже прописано в
  `docker-compose.yml`); клиент должен слушать не только `127.0.0.1`, а адрес, доступный из docker-сети.
* По умолчанию `NO_PROXY=localhost,127.0.0.1,.yandex.net,.yandex.ru`, поэтому **бот Яндекса ходит напрямую**, мимо VPN.
  Свой `NO_PROXY` из `.env` заменяет умолчание — оставьте в нём домены Яндекса.
* Отдельный прокси только для бота: `YANDEX_MESSENGER_PROXY` (приоритетнее `HTTPS_PROXY` и `NO_PROXY`); форматы
  `http(s)://[user:pass@]host:port` и `socks5(h)://`. Прокси касается API, скачивания файлов, CDN и cron-доставки;
  в логе адрес печатается без учётных данных.

### Прочее

* **Webhook вместо polling.** Нужен публичный HTTPS-адрес: раскомментируйте `ports:` в `docker-compose.yml`, задайте
  `YANDEX_MESSENGER_WEBHOOK_URL` и `YANDEX_MESSENGER_WEBHOOK_SECRET` (см. [Webhook](#webhook)).
* **Несколько пользователей.** Один контейнер = один бот; у каждого пользователя из `ALLOWED_USERS` своя сессия, но память,
  skills, настройки и ключи общие. Для изоляции нужен отдельный бот (токен), отдельный `.env` и отдельный том на каждого
  (`docker compose -p <имя> ...`; для этого уберите `container_name` из `docker-compose.yml`).

## Обновление плагина и Hermes (для администратора)

### Версия Hermes закреплена

Плагин опирается на внутренние механизмы Hermes (подтверждение команд, кнопки, медиа-кэш), а Hermes выпускает версии
часто (`v0.23.0` — 5 ноября, `v0.23.1` — 10 ноября). Поэтому образ закреплён на проверенной версии:

* в `.env.example` — `HERMES_IMAGE=nousresearch/hermes-agent:v0.23.1` (копируется в ваш `.env`);
* те же значения по умолчанию в `docker-compose.yml` и `Dockerfile`, так что сборка без `.env` тоже не уйдёт на новую версию.

Пересборка и `docker compose build --pull` **не** переходят на новый Hermes сами: тег конкретного релиза (`v0.23.1`) указывает
на один образ (теги `latest` и `stable` двигаются на каждый новый релиз — не используйте их). Новая версия появляется у вас
только когда вы сами меняете тег. Если нужна абсолютная строгость, закрепите образ по digest:
`HERMES_IMAGE=nousresearch/hermes-agent@sha256:<digest>` (digest виден в `docker images --digests`).

Проверено: плагин проходит тесты на `v0.23.0` и `v0.23.1`.

### Обновить только плагин (`git pull`)

```bash
git pull
docker compose up -d --build        # Hermes остаётся прежним; данные в томе сохраняются
docker compose logs -f hermes
```

### Подтянуть новую версию Hermes

Текущий контейнер **не затрагивается**, пока вы не выполните `up -d`: сборка и тесты идут отдельно. Откат — возврат тега.

```bash
# 1. Какие релизы вышли (только стабильные теги) и какой сейчас закреплён
curl -fsS 'https://hub.docker.com/v2/repositories/nousresearch/hermes-agent/tags?page_size=100&ordering=last_updated' \
  | grep -o '"name":"v[0-9][0-9.]*"' | cut -d'"' -f4 | sort -uV | tail -5
grep ^HERMES_IMAGE .env

# 2. Резервная копия данных (команда из раздела «Docker», храните как секрет)

# 3. Собрать на новой версии (тег впишите в .env: HERMES_IMAGE=nousresearch/hermes-agent:v0.24.0)
docker compose build --pull

# 4. Проверить совместимость плагина с новым Hermes, не трогая работающий контейнер
bash scripts/run_tests_docker.sh        # нужны uv или pip на хосте; ожидаем: passed, 0 failed

# 5. Если тесты зелёные — запустить и проверить вручную
docker compose up -d
docker compose logs -f hermes           # ждём "yandex_messenger connected ... (polling mode)"
docker compose exec hermes hermes --version
```

После запуска проверьте в чате: обычный ответ, карточку подтверждения опасной команды (например `rm -rf /tmp/x`), `/model`
под администратором и отказ `/model` у обычного пользователя. Тесты не видят стриминг и вид карточек в клиенте.

**Откат**, если что-то сломалось: верните прежний тег в `.env` и выполните `docker compose up -d --build` (слои в кэше,
это быстро). Если новая версия успела изменить формат данных в томе — восстановите том из копии шага 2.

**Если тесты упали:** оставайтесь на старом теге (контейнер работает как работал) и отправьте вывод тестов
разработчику плагина: обычно это правка одного-двух мест в адаптере. Не переходите на `latest` «в обход».

Прочее:

* `hermes update` внутри контейнера не нужен и не поможет: образ неизменяемый, обновление — это пересборка.
* Закрепив новый тег, закоммитьте его в `.env.example` (и в `docker-compose.yml` / `Dockerfile`, если меняли умолчание),
  чтобы следующая установка сразу брала проверенную версию.
* Мигрирует ли Hermes сам старые данные в томе при старте новой версии — не проверялось; поэтому копия тома в шаге 2 обязательна.

### Статус проверки

| Проверено | Покрыто только тестами |
|---|---|
| Вживую (реальный бот, личный чат, Hermes на хосте): polling, диалог и стриминг, реакции, `/model`, картинка и файл туда-обратно, карточка подтверждения команды, `processing`-typing | `clarify`, slash-confirm, запрос обновления, группы и упоминания, webhook, cron, галереи, SOCKS-прокси |
| Docker: установка с нуля из GitHub (`git clone` → `.env` → `docker compose up`), плагин и сид применяются, перезапуск не применяет сид повторно, тесты плагина на Hermes из образа | Живой диалог из контейнера с реальным токеном не прогонялся |

Поведение Bot API, подтверждённое экспериментально, — в `docs/API_FINDINGS.md`. Проверялось на Ubuntu 24.04 (WSL2) с Docker 29.

## Установка без Docker

Для машины, где Hermes уже установлен (`~/.hermes`, команда `hermes`):

```bash
# из корня репозитория
bash scripts/install_wsl.sh                        # копирует плагин в ~/.hermes/plugins/platforms/ и делает `hermes plugins enable`
bash scripts/install_wsl.sh --link                 # то же, но симлинком (удобно при разработке)

echo 'YANDEX_MESSENGER_BOT_TOKEN=<токен>'           >> ~/.hermes/.env
echo 'YANDEX_MESSENGER_ALLOWED_USERS=login1,login2' >> ~/.hermes/.env
hermes gateway restart
```

Пользовательские плагины Hermes работают по принципу opt-in: ключ плагина — `platforms/yandex_messenger`
(`hermes plugins enable platforms/yandex_messenger`, либо `plugins.enabled` в `config.yaml`). Токен скрипт нигде не сохраняет.
Настройки моделей из `config/hermes.yaml` в этом режиме сами не применяются: задайте их через `hermes model` / `hermes config set`.

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
| Администраторы | `YANDEX_MESSENGER_ADMIN_USERS`: только админ меняет модель и настройки (команды и кнопки-пикеры), остальным — безопасный набор команд |
| Прокси | `YANDEX_MESSENGER_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` с учётом `NO_PROXY`, HTTP(S) и SOCKS |
| Cron / `send_message` | `standalone_sender_fn` — доставка без запущенного gateway |

Требования к Hermes: версия с plugin-платформами. Тесты проходят на хостовой установке (commit `b200f5bc`) и на образах
`nousresearch/hermes-agent:v0.23.1` (commit `3b421578`, закреплён) и `v0.23.0`; везде Python 3.14. Дополнительных pip-зависимостей нет: `aiohttp`
уже в Hermes (для SOCKS-прокси нужен `aiohttp_socks`, в образе он есть).

## Переменные плагина

Задаются в `.env` (Docker: `~/.hermes/.env` без Docker) или в `platforms.yandex_messenger.extra` в `config.yaml`; env приоритетнее.

| Переменная | По умолчанию | Описание |
|---|---|---|
| `YANDEX_MESSENGER_BOT_TOKEN` | — | OAuth-токен бота (обязательно) |
| `YANDEX_MESSENGER_ALLOWED_USERS` | — | Логины (рабочая почта в Яндекс 360) через запятую, регистр не важен |
| `YANDEX_MESSENGER_ADMIN_USERS` | пусто = без ограничений | Логины администраторов: только им доступны `/model` и прочие служебные команды (см. [Администраторы и пользователи](#администраторы-и-пользователи)) |
| `YANDEX_MESSENGER_USER_COMMANDS` | `new,stop,status,retry,undo,history,title,resume,sessions,queue,usage,approve,deny,version` | Команды обычных пользователей при заданном `ADMIN_USERS` (`/help` и `/whoami` всегда) |
| `YANDEX_MESSENGER_ALLOW_ALL_USERS` | `false` | Разрешить всем (только для разработки) |
| `YANDEX_MESSENGER_ALLOWED_CHATS` | пусто = любые | ID групповых чатов, где бот отвечает |
| `YANDEX_MESSENGER_REQUIRE_MENTION` | `true` | В группах отвечать только на упоминание / reply / `/команду` |
| `YANDEX_MESSENGER_HOME_CHANNEL` | — | Чат для cron: `0/0/<uuid>`, `<guid>_<guid>` (личный чат), логин или `chat:<id>` / `login:<x>` / `user:<guid>` |
| `YANDEX_MESSENGER_HOME_CHANNEL_NAME` | `Home` | Отображаемое имя home channel |
| `YANDEX_MESSENGER_POLL_INTERVAL` | `1.0` | Пауза между пустыми опросами, сек (мин. 0.2) |
| `YANDEX_MESSENGER_PROXY` | — | Прокси только для бота (см. [Прокси](#прокси--vpn)) |
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

### Адресация

Ответы всегда идут в `chat.id` (он валиден как `chat_id` и для личных чатов). Строки «снаружи» (home channel, cron,
`send_message`) разбирает `Target.parse`: префиксы `chat:` / `login:` / `user:` явные; значение с `/` (группы,
`0/0/<uuid>`) или вида `<guid>_<guid>` (личные чаты) — `chat_id`, всё остальное — `login`.

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

### Структура

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
tests/     test_inbound / test_outbound / test_interactive / test_transport / test_registration / test_discovery / test_seed_config  (+ conftest, fake_yandex)
docs/      API_FINDINGS.md
```
