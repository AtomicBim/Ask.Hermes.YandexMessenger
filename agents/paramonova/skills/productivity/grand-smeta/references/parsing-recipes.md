# Код-рецепты извлечения ГРАНД-Смета

Проверенный рабочий код. Строки/типы подставь под свой файл.

## 1. xlsx: определить колонки по подписям (объединённые ячейки сдвигают индексы)

```python
import openpyxl, re
ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
colmap = {}
for r in range(1, 40):
    for c in range(1, 25):
        v = ws.cell(row=r, column=c).value
        s = str(v).strip() if v else ""
        for label, key in [("№ п/п","num"), ("Обоснование","ob"),
                           ("Наименование работ и затрат","name"),
                           ("Единица измерения","unit"), ("Количество","qty")]:
            if s == label: colmap.setdefault(key, c)
# типично: num=1 ob=2 name=3 unit=8 qty=9
```

Фильтр верхних позиций: номер — число (`int/float` или `re.fullmatch(r"\d+(\.\d+)?", str)`) И обоснование матчится `ГЭСН|ФСБЦ|ФСЭМ|ТЦ_|ТЕР|ФЕР|^\d{2}-`. Ресурсы (`91.01`, `1-100-`, `4-100-`, `Пр/`, `02-15-`) отсекаются отсутствием числового №.

## 2. PDF: разделить листы и достать позиции (pymupdf)

```python
import pymupdf, re
doc = pymupdf.open(path)
txt = "".join(p.get_text() for p in doc)
marks = [m.start() for m in re.finditer(r"ЛОКАЛЬНЫЙ СМЕТНЫЙ РАСЧЕТ \(СМЕТА\) № (\S+)", txt)]
# лист i = txt[marks[i] : marks[i+1] или len]

OB = r"(ГЭСН\d{2}[\d\-]+|ФСБЦ[\w.\-]+|ФСЭМ[\w.\-]+|ТЦ_[\w.\-]+)"
UNITS = r"(?:1000\s+м3|100\s+м3|100\s+м2|10\s+м3|10\s+м2|10\s+шт|маш\.-ч|чел\.-ч|кВт-ч|м3|м2|шт|кг|км|м\b|т\b)"

def tofloat(s):
    s = str(s).replace(",",".").replace(" ","").replace("\xa0","")
    try: return float(s)
    except: return None

def sheet_positions(sheet_text):
    s = re.sub(r"\s+"," ", sheet_text)
    out = {}
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*\|?\s*("+OB+r")\s+", s):
        num, ob = m.group(1), m.group(2)
        if ob.startswith(("ФСБЦ","ФСЭМ")) and "_" in ob:  # транспортные ресурсы
            continue
        rest = s[m.end():m.end()+140]
        um = re.search(r"\b("+UNITS+r")\s+(-?\d+(?:[.,]\d+)?)", rest)
        if um and num not in out:
            q = tofloat(um.group(2))
            if q is not None:
                out[num] = (ob, um.group(1), q)   # (код, ед.изм, объём)
    return out
```

Ключевые пункты, без которых парсится мусор:
- объём — строго `-?\d+(?:[.,]\d+)?` (одно число, а не `[\d ]+` — иначе сцепляются стоимости);
- единицы — упорядоченно (длинные раньше);
- `_` в ФСБЦ/ФСЭМ = ресурс → пропуск; в `ТЦ_` — оставить;
- дедуп по `num` (первое вхождение = заголовок позиции).

## 3. Сопоставление дельт (`.1`, `.2 доп.`) к базе

- Матч **по номеру позиции** (код обоснования не уникален).
- Исходная ПД = значение базы − дельта (если дельта уже учтена в редакции `Изм.1`).
- увеличение = `max(0, delta)`, снижение = `max(0, -delta)`.
- Дельты-позиции, которых нет в базе (новые/исключённые), выносить отдельными строками.
