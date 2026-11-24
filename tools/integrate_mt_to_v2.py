#!/usr/bin/env python3
"""Влить машинный черновик из translation_sources/ru-mt.json в ручной слой ru_translations_v2.py.

Берёт только те развороты, которых ещё нет в ручном слое (чтобы не затереть
проверенные 1–5). Форматирует как TRANSLATIONS = { 6: \"\"\"...\"\"\", ... }.

Запуск после LLM:
    LIMIT=1,2,3,4,5,6,7,8,9,10 python tools/translate_github_models.py
    python tools/apply_glossary.py
    python tools/integrate_mt_to_v2.py
"""

from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "translation_sources" / "ru-mt.json"
TARGET = ROOT / "ru_translations_v2.py"

HEADER = '''"""Новый перевод: машинный черновик, вычитанный вручную, разворот за разворотом.

Здесь лежат развороты, переведённые заново по выверенному английскому тексту.
Файл собирается порциями по 2–3 разворота: черновик перевода обрабатывается
вручную (сверка со сканом, числа, сноски, терминология), и только после этого
строка попадает сюда. Старые слои (ru_translations_front.py и прочие) этим
файлом не переписываются, а перекрываются: приоритет в translation.py —
пользовательские правки, затем этот файл, затем старые слои.

Ключ — номер разворота; текст — абзацы через пустую строку, как в читалке.
Числа, суммы и дроби сохраняются без изменений: 10,318 · £0 8s. 10d. · 8½.
"""

TRANSLATIONS = {
'''

def load_existing():
    try:
        import ru_translations_v2
        # reload to avoid cache
        import importlib
        importlib.reload(ru_translations_v2)
        return dict(ru_translations_v2.TRANSLATIONS)
    except Exception:
        return {}

def write_target(translations: dict):
    # sort by spread number
    items = sorted(translations.items())
    out = [HEADER]
    for i, (k, v) in enumerate(items):
        # escape triple quotes inside text (should not happen)
        v_escaped = v.replace('"""', '\\"\\"\\"')
        out.append(f'    {k}: """{v_escaped}""",')
        if i < len(items) - 1:
            out.append('')
    out.append('}')
    out.append('')
    TARGET.write_text('\n'.join(out), encoding='utf-8')
    print(f"записано {TARGET} — {len(items)} разворотов")

def main():
    if not DRAFT.exists():
        raise SystemExit(f"нет черновика: {DRAFT}")
    draft = json.loads(DRAFT.read_text(encoding='utf-8'))
    # draft keys are strings
    draft_int = {int(k): v for k, v in draft.items() if v and v.strip()}
    existing = load_existing()
    print(f"в ручном слое {len(existing)} разворотов: {sorted(existing)}")
    print(f"в черновике {len(draft_int)} разворотов: {sorted(draft_int)}")

    added = 0
    for k, v in draft_int.items():
        if k in existing:
            print(f"  {k}: пропуск — уже в ручном слое")
            continue
        # базовая чистка: убрать мусорные незакрытые кавычки LLM
        v = v.strip()
        existing[k] = v
        added += 1
        print(f"  {k}: добавлен ({len(v.split())} слов)")

    if added == 0:
        print("ничего нового не добавлено")
        return

    write_target(existing)

    # Проверим импорт
    import py_compile
    py_compile.compile(str(TARGET), doraise=True)
    print("py_compile ok")

if __name__ == '__main__':
    main()
