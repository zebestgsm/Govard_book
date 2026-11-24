"""Машинный черновик через NLLB (facebook/nllb-200).

Идея та же, что у translate_opus.py, но модель — многоязычный NLLB,
который часто чище OPUS на en→ru по терминам.

    MT_MODEL=facebook/nllb-200-distilled-600M LIMIT=31,87,141,95 python tools/translate_nllb.py
    MT_MODEL=facebook/nllb-200-1.3B python tools/translate_nllb.py

Требует transformers + sentencepiece + torch (CPU).
"""

from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "translation_sources" / "work-doc.json"
OUT = ROOT / "translation_sources" / "ru-mt.json"
STATS = ROOT / "translation_sources" / "ru-mt-stats.json"
MODEL = os.environ.get("MT_MODEL", "facebook/nllb-200-distilled-600M")
TOKEN = os.environ.get("HF_TOKEN") or None
BATCH = int(os.environ.get("MT_BATCH", "8"))


def sentences(paragraph: str):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"'(\[])", paragraph)
    return [s.strip() for s in parts if s.strip()]


def wanted():
    limit = os.environ.get("LIMIT", "").strip()
    if not limit:
        return None
    return {int(x) for x in limit.split(",") if x.strip()}


def main():
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    print("модель:", MODEL, flush=True)
    tok = AutoTokenizer.from_pretrained(MODEL, token=TOKEN)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL, token=TOKEN)
    model.eval()
    torch.set_num_threads(max(1, os.cpu_count() or 1))

    # NLLB требует указания языков
    src_lang = "eng_Latn"
    tgt_lang = "rus_Cyrl"

    def translate(batch):
        # NLLB tokenizer поддерживает src_lang как аргумент
        with torch.no_grad():
            inputs = tok(batch, return_tensors="pt", padding=True, truncation=True, max_length=512)
            generated = model.generate(
                **inputs,
                forced_bos_token_id=tok.convert_tokens_to_ids(tgt_lang),
                num_beams=4,
                max_length=512,
            )
        return tok.batch_decode(generated, skip_special_tokens=True)

    doc = json.loads(SRC.read_text(encoding="utf-8"))
    only = wanted()
    out, stats = {}, {}
    started = time.time()
    for number in sorted(doc["spreads"], key=int):
        if only and int(number) not in only:
            continue
        spread = doc["spreads"][number]
        t0 = time.time()
        paragraphs, count = [], 0
        if spread["running_heads"]:
            paragraphs.extend(spread["running_heads"])
        for paragraph in spread["paragraphs"]:
            pieces = sentences(paragraph["text"].replace("\n", " "))
            translated = []
            for i in range(0, len(pieces), BATCH):
                chunk = pieces[i : i + BATCH]
                try:
                    translated.extend(translate(chunk))
                except Exception as error:
                    print("error", number, error, flush=True)
                    translated.extend(chunk)
                count += len(chunk)
            paragraphs.append(" ".join(translated))
        for table in spread["tables"]:
            paragraphs.append(table["text"])
        for note in spread["footnotes"]:
            paragraphs.append(" ".join(translate([note["text"].replace("\n", " ")])))
        out[number] = "\n\n".join(p for p in paragraphs if p.strip())
        stats[number] = {"sentences": count, "seconds": round(time.time() - t0, 1)}
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
        print(number, count, stats[number]["seconds"], flush=True)
    print("готово:", len(out), "разворотов за", round(time.time() - started), "с", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
