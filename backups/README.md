# Резервные копии

## Что уже сделано (2026-10-30)

### 1. Метка-снимок в GitHub

```
тег:        backup-verified-165
коммит:     a905e330f13c52165caf17993490c4b1dd2d0f1a
ветка:      arena/b1f6ea5c-govard-book
```

Метка аннотированная, лежит **на сервере GitHub** (`git ls-remote --tags origin` подтверждает).
Состояние на момент снимка: все 165 разворотов вычитаны по сканам, 132 принято, 28 ждут
дальнейшей проверки, перевод — план и заметка о выборе модели. Тег не двигается: что бы мы ни
делали дальше в ветке, вернуться к снимку можно в любой момент.

Восстановление:

```sh
git fetch origin --tags
git checkout backup-verified-165          # посмотреть снимок
git switch -c restore-from-backup backup-verified-165   # или начать с него новую ветку
```

### 2. Контрольные суммы данных

`backups/CHECKSUMS.txt` — sha256 и размер 26 файлов: данные читалки (`text_edits.json`,
`scan_quality.json`, `collation_audit.json`, `ocr_review/*`), слои переводов
(`ru_translations_*.py`, `ru_context.py`), выверенные тексты (`verified_*.py`) и код читалки.

Проверка целостности:

```sh
cd /путь/к/репозиторию
python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
ok = bad = 0
for line in Path('backups/CHECKSUMS.txt').read_text(encoding='utf-8').splitlines():
    if not line or line.startswith('#'): continue
    digest, size, name = line.split(None, 2)
    if sha256(Path(name).read_bytes()).hexdigest() == digest: ok += 1
    else: print('MISMATCH', name); bad += 1
print('совпало:', ok, 'расхождений:', bad)
PY
```

### 3. Проверка восстановления (сделана)

Свежий клон ветки с GitHub: 547 файлов, все 165 сканов на месте, **26 из 26 контрольных сумм
совпали**, расхождений нет. Локально: 101 тест — OK, рабочее дерево чистое.

---

## Что не входит в снимок и почему

| Данные | Где | Восстанавливается |
|---|---|---|
| Сканы `govard001–165.pdf` (165 файлов, ~50 МБ) | репозиторий | из клона |
| `text_edits.json` (3,9 МБ: английский текст, история, решения) | репозиторий | из клона |
| Переводы и примечания | репозиторий | из клона |
| `translation_edits.json` (правки перевода пользователем) | **не существует** | — |
| Крупные справочные издания (docx / epub / PDF IA) | репозиторий | из клона |

Отдельный git-bundle не нужен: всё, включая сканы и историю, уже в GitHub — bundle весил бы
около 70 МБ и дублировал сервер. Проверено, что клон с GitHub самодостаточен.

---

## Как сделать локальную копию (по желанию, на ваш компьютер)

```sh
# полный клон со всей историей и сканами (~110 МБ)
git clone https://github.com/zebestgsm/Govard_book.git

# только снимок-метка, без остальных веток
git clone --branch backup-verified-165 --single-branch https://github.com/zebestgsm/Govard_book.git
```

Архив без git-истории (если нужен просто «слепок файлов»):

```sh
git archive --format=zip -o govard-verified-165.zip backup-verified-165
```

## Перед большими изменениями в переводе

Перед заменой машинного слоя стоит поставить ещё одну метку — так у каждого этапа будет
своя точка возврата:

```sh
git tag -a backup-before-translation -m "Перед новым проходом перевода"
git push origin refs/tags/backup-before-translation
```
