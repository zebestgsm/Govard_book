"""Машинный черновик через GitHub Models (LLM API).

Вход — `translation_sources/work-doc.json` (склейка абзацев, как для OPUS).
Выход — `translation_sources/ru-mt.json` в том же формате, что и у OPUS,
чтобы `tools/mt_report.py` работал без изменений.

Модель берётся из окружения GH_MODEL, по умолчанию `openai/gpt-4o-mini`.
GitHub Models доступен в Actions по GITHUB_TOKEN без отдельного ключа —
нужно разрешение `models: read` в workflow.

    GH_MODEL=openai/gpt-4o-mini LIMIT=31,87,141,95 python tools/translate_github_models.py
    GH_MODEL=meta/Meta-Llama-3.1-405B-Instruct LIMIT=31,87,141,95 ...

Требует: GITHUB_TOKEN (или GH_TOKEN) в окружении, доступ к https://models.github.ai
"""

from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "translation_sources" / "work-doc.json"
OUT = ROOT / "translation_sources" / "ru-mt.json"
STATS = ROOT / "translation_sources" / "ru-mt-stats.json"
GLOSSARY_PATH = ROOT / "translation_glossary.json"

MODEL = os.environ.get("GH_MODEL", "openai/gpt-4o-mini") or "openai/gpt-4o-mini"
ENDPOINT = os.environ.get("GH_ENDPOINT", "https://models.github.ai/inference/chat/completions")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("HF_TOKEN")

BATCH_SENTENCES = int(os.environ.get("MT_BATCH", "1"))  # LLM: переводим абзац целиком

SYSTEM_PROMPT = (
    "Ты — опытный переводчик документальной прозы XVIII века с английского на русский. "
    "Переводи дословно, сохраняя длинный период Говарда, без сглаживания и без пересказа. "
    "Требования:\n"
    "- Сохрани все числа, суммы (£, s., d.), дроби и даты ТОЧНО, без пересчёта и без изменений.\n"
    "- Сохрани маркеры сносок ¹ ² ³ на тех же местах.\n"
    "- Не добавляй пояснений, комментариев, примечаний — только перевод.\n"
    "- Сохрани кавычки и пунктуацию, соответствующую оригиналу.\n"
    "- Используй термины из глоссария ниже, если встречаются."
)


def wanted():
    limit = os.environ.get("LIMIT", "").strip()
    if not limit:
        return None
    return {int(x) for x in limit.split(",") if x.strip()}


def load_glossary_terms():
    try:
        data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
        return data.get("terms", [])
    except Exception:
        return []


def glossary_for_text(text: str, terms):
    """Возвращает только те статьи, где англ. термин встречается в тексте."""
    found = []
    low = text.lower()
    for t in terms:
        en = t["en"].lower()
        # простой поиск по слову (границы не требуем для составных типа county gaol)
        if re.search(r"\b" + re.escape(en) + r"\b", low):
            found.append(t)
    return found


def build_glossary_prompt(terms_subset):
    if not terms_subset:
        return ""
    lines = ["Глоссарий (используй именно эти русские соответствия, запрещённые варианты не употребляй):"]
    for t in terms_subset:
        ru = t["ru"]
        alt = ", ".join(t.get("alt", [])) if t.get("alt") else ""
        rej = ", ".join(t.get("rejected", [])) if t.get("rejected") else ""
        note = t.get("note", "")
        parts = [f"{t['en']} → {ru}"]
        if rej:
            parts.append(f"не использовать: {rej}")
        if note:
            parts.append(note)
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines)


def chat_translate(text: str, glossary_prompt: str):
    import urllib.request
    import urllib.error

    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN не задан — GitHub Models требует токен")

    prompt = glossary_prompt + "\n\nПереведи на русский следующий абзац:\n\n" + text if glossary_prompt else "Переведи на русский следующий абзац:\n\n" + text

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "top_p": 0.9,
    }).encode("utf-8")

    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    # retry 3 times
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                # OpenAI-compatible: choices[0].message.content
                try:
                    return data["choices"][0]["message"]["content"].strip()
                except KeyError:
                    # fallback for other shapes
                    return data["choices"][0].get("text", "").strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:2000]
            print(f"HTTP {e.code} attempt {attempt+1}: {body}", flush=True)
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            print(f"error attempt {attempt+1}: {e}", flush=True)
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def main():
    if not SRC.exists():
        print(f"нет входного документа: {SRC}", file=sys.stderr)
        sys.exit(1)

    doc = json.loads(SRC.read_text(encoding="utf-8"))
    only = wanted()
    glossary = load_glossary_terms()
    print(f"модель LLM: {MODEL} endpoint {ENDPOINT}", flush=True)
    print(f"глоссарий: {len(glossary)} статей", flush=True)

    out, stats = {}, {}
    started = time.time()
    for number in sorted(doc["spreads"], key=int):
        if only and int(number) not in only:
            continue
        spread = doc["spreads"][number]
        t0 = time.time()
        paragraphs = []
        # running heads — переводим как отдельные абзацы (если есть)
        if spread["running_heads"]:
            for head in spread["running_heads"]:
                try:
                    terms = glossary_for_text(head, glossary)
                    gp = build_glossary_prompt(terms)
                    paragraphs.append(chat_translate(head, gp))
                except Exception as e:
                    print(f"error head {number}: {e}", flush=True)
                    paragraphs.append(head)
                time.sleep(0.3)

        count = 0
        for para in spread["paragraphs"]:
            src = para["text"].replace("\n", " ").strip()
            if not src:
                continue
            count += 1
            terms = glossary_for_text(src, glossary)
            gp = build_glossary_prompt(terms)
            try:
                ru = chat_translate(src, gp)
            except Exception as e:
                print(f"error {number} para: {e}", flush=True)
                ru = src  # fallback — оригинал, чтобы не терять текст
            paragraphs.append(ru)
            time.sleep(0.4)  # щадим rate limit

        # таблицы — не переводим, числа должны остаться
        for table in spread["tables"]:
            paragraphs.append(table["text"])
        for note in spread["footnotes"]:
            src = note["text"].replace("\n", " ").strip()
            terms = glossary_for_text(src, glossary)
            gp = build_glossary_prompt(terms)
            try:
                paragraphs.append(chat_translate(src, gp))
            except Exception as e:
                print(f"error footnote {number}: {e}", flush=True)
                paragraphs.append(src)
            time.sleep(0.3)

        out[number] = "\n\n".join(p for p in paragraphs if p.strip())
        stats[number] = {"sentences": count, "seconds": round(time.time() - t0, 1)}
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
        print(number, count, stats[number]["seconds"], flush=True)

    print(f"готово: {len(out)} разворотов за {round(time.time()-started)}с", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
