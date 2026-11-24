#!/usr/bin/env python3
"""Применяет глоссарий к готовому черновику (post_replace).

Читает translation_sources/ru-mt.json, заменяет запрещённые варианты
из translation_glossary.json на основной вариант и пишет ru-mt.json обратно.

Пример:
    python tools/apply_glossary.py
    LIMIT=31,87,141,95 python tools/apply_glossary.py   # только эти развороты

Работает как детерминированная постобработка: ни модель, ни LLM
не вызываются, только замены по словарю.
"""

from __future__ import annotations
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "translation_sources" / "ru-mt.json"
GLOSSARY = ROOT / "translation_glossary.json"


def wanted():
    limit = os.environ.get("LIMIT", "").strip()
    if not limit:
        return None
    return {str(int(x)) for x in limit.split(",") if x.strip()}


def main():
    if not DRAFT.exists():
        print(f"нет черновика: {DRAFT}")
        return
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))["terms"]

    # Собираем замены: rejected -> ru
    repl = []
    for term in glossary:
        ru = term["ru"]
        for variant in term.get("rejected", []):
            if not variant.strip():
                continue
            # границы слова, без учёта регистра, сохраняем первую букву
            repl.append((variant, ru))

    only = wanted()
    changed_total = 0
    for num, text in draft.items():
        if only and num not in only:
            continue
        new_text = text
        for src, dst in repl:
            # case-insensitive, word boundary
            pattern = re.compile(r"\b" + re.escape(src) + r"\b", flags=re.IGNORECASE)

            def _keep_case(m):
                # сохраняем заглавную если исходник с заглавной
                w = m.group(0)
                if w and w[0].isupper():
                    return dst[:1].upper() + dst[1:]
                return dst

            new_text, n = pattern.subn(_keep_case, new_text)
            changed_total += n
        draft[num] = new_text

    DRAFT.write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"замен по глоссарию: {changed_total} (rejected → ru)")
    # Для отчёта
    for src, dst in repl:
        print(f"  {src} → {dst}")


if __name__ == "__main__":
    main()
