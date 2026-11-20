# Выгрузка настроек агента в репозиторий (для пользователей и администратора)

Зачем: скиллы и MCP-подключения (Яндекс Tracker, Диск, Календарь и т. д.), которые пользователь настроил своему агенту,
лежат только в Docker-томе `hermes-data` на его сервере. Их нужно вынести в репозиторий, чтобы общее попало в `master`
и стало у всех. Память и история диалогов **не выгружаются**.

Каждый пользователь работает в своей ветке (`baranova`, `grigoriev`, `lebedev`) на своём сервере и выгружает результат
в `agents/<user>/` этой же ветки. Слияние в `master` делает администратор.

## Что выгружается

| Выгружается | Не выгружается никогда |
|---|---|
| `skills/`: только скиллы, созданные пользователем (поставляемые с Hermes пропускаются по имени) | `memories/`, `sessions/`, `state*`, `.env`, `auth*.json`, логи |
| `mcp_servers.yaml`: MCP-серверы из `config.yaml`, секреты заменены на `${ИМЯ}` | значения токенов и паролей |
| `env.required`: список переменных `${ИМЯ}`, которые нужно задать в `.env` | |

## Часть 1. Пользователю: выгрузить свой агент

Выполняйте на сервере, где запущен ваш контейнер, из папки репозитория. Ниже `<user>` это имя вашей ветки, а `<container>`
имя вашего контейнера (`docker ps`; у `grigoriev` это `hermes-grigoriev`, по умолчанию `hermes-yandex`).

**Шаг 1. Обновить ветку.** Нужен свежий скрипт выгрузки.
```bash
cd ~/hermes/<user>
git fetch origin
git checkout <user>
git merge origin/master
```

**Шаг 2. Убедиться, что на сервере есть PyYAML.** Если команда падает, выполните `pip install pyyaml` (или `sudo apt install python3-yaml`).
```bash
python3 -c "import yaml; print('ok')"
```

**Шаг 3. Выгрузить.**
```bash
bash scripts/export_agent.sh <user> <container>
```
В конце будет строка вида `exported to agents/<user>: N skill(s), M MCP server(s), K secret variable(s)`.

Если скрипт написал `ERROR: secret-looking values remain in the export`, выгрузка удалена, ничего коммитить не нужно.
Пришлите администратору список строк из ошибки и **не пушьте** ничего из `agents/`.

Если скрипт написал `warning: bundled Hermes skills not found`, он выгрузил все скиллы, включая поставляемые с Hermes. Пришлите
администратору вывод, не пушьте.

**Шаг 4. Просмотреть результат глазами.** Это обязательный шаг: автоматическая очистка ловит типовые форматы токенов,
но нестандартный секрет может пропустить.
```bash
find agents/<user> -type f
cat agents/<user>/mcp_servers.yaml 2>/dev/null     # вместо токенов должны быть только ${...}
cat agents/<user>/env.required 2>/dev/null
grep -rniE 'y0_|AQAAA|token|password|secret|bearer' agents/<user> | cut -c1-160
```
Проверьте, что:
- в `mcp_servers.yaml` нет настоящих токенов, паролей и личных адресов;
- в `skills/` нет логинов, внутренних адресов, номеров телефонов, ФИО, паролей. Если нашли, удалите строку и в самом скилле тоже.

**Шаг 5. Отправить в свою ветку.**
```bash
git add agents/<user>
git commit -m "Export agent <user>"
git push origin <user>
```
После этого сообщите администратору, что выгрузка готова.

Выгрузку можно безопасно повторять: каталог `agents/<user>/` каждый раз пересоздаётся.

## Часть 2. Администратору: собрать выгрузки в master

1. Дождаться выгрузок от всех трёх пользователей и просмотреть каждую:
   ```bash
   git fetch --all
   git diff origin/master origin/<user> -- agents/
   ```
2. Общие скиллы перенести в `skills/<имя>/` в корне репозитория. При совпадении имён выбрать лучшую версию или объединить.
   Скиллы, привязанные к окружению одного пользователя (прокси, личные маршруты), сначала обезличить или оставить в его ветке.
3. MCP-серверы Яндекса объединить в секцию `mcp_servers` файла `config/hermes.yaml`. Секреты только через `${ИМЯ}`,
   новые переменные добавить в `.env.example` с комментарием и **без значений**.
4. Проверить, что `docker/apply_config.py` переносит `mcp_servers` из seed в `config.yaml` вместе с `${ИМЯ}`.
   Если нет, доработать скрипт.
5. Скопировать `skills/` в образ (`Dockerfile`) и добавить в `docker/cont-init-yandex-messenger.sh` шаг, который при старте
   синхронизирует скиллы из образа в `$HERMES_HOME/skills/` (перезаписываются только скиллы из репозитория, пользовательские
   не трогаются), затем `chown hermes:hermes`.
6. Личные скиллы пользователей лежат в `master` в `agents/<user>/skills/` и **не должны повторять** общие: у каждого
   скилла одна каноническая копия (общий: `skills/`, необязательный: `skills-optional/`, личный: `agents/<user>/skills/`).
   Тест `test_users_do_not_keep_copies_of_shared_skills` падает, если в `agents/` оказалась копия общего скилла.
   После слияния пересоздать ветки пользователей от нового `master`.

### Повторная выгрузка и копии

`export_agent.sh` выгружает все пользовательские скиллы, в том числе те, что уже стали общими и потому лежат в образе: при каждом
старте они перезаписываются версией из репозитория. Если пользователь допишет в такой скилл свои заметки, они придут в
`agents/<user>/skills/<имя>/` как копия. Администратор при сборке:
1. сравнивает копию с `skills/<имя>/`, влитые различия переносит в общий скилл (без личных данных), копию **удаляет**;
2. личные данные (логины, ФИО, адреса, номера писем и договоров) в общий скилл не переносит.

## Что уже перенесено в master

Первая выгрузка трёх пользователей и вторая выгрузка (aog, baranova, fathullin, grigoriev, lebedev, manchilina, osipov,
paramonova, zhukova) разобраны.

**Общие скиллы (`skills/`, ставятся всем при старте контейнера):**
- `yandex-disk`: версия master + MOVE/423/429, защита от бесконечного обхода папок (manchilina, baranova);
- `yandex-calendar`: версия master + приглашение участников, CardDAV-контакты, особенности UID (zhukova, grigoriev);
- `imap-mail-search`: версия master + разбор вложенных HTML-сущностей, письма самому себе, обход обрывов соединения,
  сверка исходящих и входящих (lebedev, paramonova);
- `security-recon`: версия osipov (аудит прав и привилегий, Prometheus, скрипты `scripts/`);
- `yandex-360-mcp`: MCP Яндекс 360, Телемост и Трекер (grigoriev, обезличен, ключи в `.secrets/`).

**Необязательные скиллы (`skills-optional/`):** `digital-twin` ставится только там, где в `.env` задано
`HERMES_OPTIONAL_SKILLS=digital-twin` (отдельный экземпляр, который создаёт аватары сотрудников).

**Личные скиллы остаются в `agents/<user>/skills/`:** `baranova` (`audio-transcription`, `creative/raster-to-vector`,
`real-estate/*`, `real-estate-product-concept`, `project-folder-structure`), `grigoriev` (`mcp-builder`,
`russian-rail-tickets`), `osipov` (`research/malware-analysis`), `paramonova` (`productivity/grand-smeta`,
`productivity/skany-processing`). У `aog`, `fathullin`, `lebedev`, `manchilina`, `zhukova` личных скиллов нет.

MCP-серверов в выгрузках нет: подключения к Яндексу сделаны скиллами по WebDAV, CalDAV, IMAP и прямыми запросами к MCP.

После обновления `master` каждому пользователю нужно пересоздать свою ветку от него и пересобрать контейнер:
```bash
git fetch origin && git checkout <user> && git reset --hard origin/master   # личные скиллы лежат в истории ветки; сначала сохраните agents/<user>/, если он нужен
docker compose up -d --build
docker compose logs --tail=40 hermes | grep -E "shared skills|optional skills"  # shared skills synced: 5 / optional skills enabled: 0
```

## Модель агента

Модель задаётся только в `.env` (`HERMES_MODEL`, `HERMES_PROVIDER`) и применяется при каждом старте контейнера. Команда `/model`
в чате отключена для всех. Подробности в [README](../README.md#настройки-hermes-модели-и-пр).
