# Bot API: что подтверждено, что предполагается

Источник: `yandex_bot_example/yandex-messenger-plugin/reference/yandex_bot_api_full_docs.txt` и установленный Hermes
(commit `b200f5bc`). Эмпирическая проверка на реальном токене **не выполнялась** (токена в окружении не было) —
для неё есть `scripts/probe_api.py`; результаты нужно вписать в колонку «Факт».

## Сигнатуры Hermes (проверено по исходникам)

| Что | Факт |
|---|---|
| Подтверждение команд | `tools.approval.resolve_gateway_approval(session_key, choice, resolve_all=False, reason=None, request_id=None)`; `resolve_gateway_approval_card` / `approval_actor` в этой версии **отсутствуют** |
| Slash-confirm | `await tools.slash_confirm.resolve(session_key, confirm_id, choice)` → текст или `None` |
| Clarify | `tools.clarify_gateway.get_pending / mark_awaiting_text / resolve_gateway_clarify` |
| Плагин-платформа | `register_platform(...)` принимает kwargs `PlatformEntry` (проверено тестом, неизвестные ключи дают `TypeError`); пользовательский плагин: `~/.hermes/plugins/platforms/<name>/plugin.yaml`, ключ `platforms/<name>`, включается через `plugins.enabled` |
| `Platform("yandex_messenger")` | Резолвится только после регистрации в реестре → `gateway_env.platform()` создаёт динамический член сам |
| Token-lock | `_acquire_platform_lock(scope, identity, desc)`; тот же PID всегда повторно захватывает лок |
| `send_private_notice` | `(chat_id, user_id, content, reply_to, metadata)` |

## Поведение Bot API

Проверено вживую 2026-11-10 (личный чат, `scripts/probe_api.py`-аналог по шагам). Строки с «?» — ещё не проверены.

| Вопрос | Предположение плагина | Факт |
|---|---|---|
| Правка с пустой клавиатурой (`layout:"false"`, `buttons: []`) убирает кнопки? | Пробуем; при HTTP-ошибке повторяем правку без `suggest_buttons` | **Да**, текст меняется, кнопки исчезают |
| Правка карточки с новой клавиатурой заменяет кнопки? | Да; при ошибке карточка удаляется и отправляется заново | **Да**, в том числе возвращает кнопки на сообщение, с которого они были сняты |
| `message_id` / `chat` в update с `bot_request` | Не используются: плагин хранит id карточек сам | `message_id` = id сообщения с карточкой, `chat` и `from` заполнены, `element_id` = id кнопки, `errors: []` |
| Кто нажал | `update.from.login` | Подтверждено |
| `processing` typing в личных чатах | Принимается; в группах — только `text` | В личном чате принят (`{}`) |
| Имена реакций | Берутся из `getChat.available_reactions` | `eyes`, `like`, `dislike` доступны и ставятся; удаление (`reaction: null`) работает. В списке 36 имён (heart, fire, check, thinking, tada …) |
| Формат `chat.id` личного чата | `chat.id` годится как `chat_id` | **`<user-guid>_<bot-guid>`, без `/`**. `Target.parse` распознаёт этот формат как chat_id (раньше принял бы за логин) |
| Ответ `sendText` | `{ok, message_id}` | Дополнительно возвращает `chat{type,id,username}` и `suggest_buttons` (только `id` кнопок); `message_id` ~ 1.79e15 |
| Накопленные апдейты | — | До `getUpdates` с offset очередь хранит старые сообщения и нажатия (в том числе до остановки бота) |
| `getUpdates`: подтверждение | `offset = max(update_id)+1` подтверждает предыдущую пачку; offset хранится на диске | ? |
| Идемпотентность `sendText` | Клиент генерирует `payload_id`; повтор после обрыва переиспользует его | ? |
| Подпись у картинок | У `sendImage` нет caption → caption отправляется отдельным сообщением; у `sendGallery` есть `text` | по документации |
| `thread_id` в входящих update | Используется, если присутствует; в документации не описан | ? |

## Как проверить

```bash
export YANDEX_MESSENGER_BOT_TOKEN=...
python scripts/probe_api.py --chat <chat_id|login> --wait 90
```

Скрипт отвечает на вопросы из таблицы по порядку (часть — вопросом к вам «видны ли кнопки?») и пишет `docs/probe-output.json`.
