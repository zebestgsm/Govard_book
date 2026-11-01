"""Translate the recovered English source with Helsinki-NLP OPUS-MT en→ru.

Run in CI (or locally with the model cached).  The output is a JSON mapping of
spread number to Russian text.  Paragraph breaks are preserved; long paragraphs
are translated sentence by sentence so that the model's 512-token limit is not
silently truncating the book.
"""
from __future__ import annotations
import json,re,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'translation_sources'/'clean-source.json'
OUT=ROOT/'translation_sources'/'ru-opus.json'

def sentences(p:str):
    parts=re.split(r'(?<=[.!?])\s+(?=[A-Z“"\'])',p)
    return [s for s in parts if s.strip()]

def main():
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    name='Helsinki-NLP/opus-mt-en-ru'
    print('loading model',flush=True)
    tok=MarianTokenizer.from_pretrained(name)
    model=MarianMTModel.from_pretrained(name)
    model.eval()
    def translate_batch(batch):
        with torch.no_grad():
            inputs=tok(batch,return_tensors='pt',padding=True,truncation=True,max_length=512)
            gen=model.generate(**inputs,num_beams=4,max_length=512)
        return tok.batch_decode(gen,skip_special_tokens=True)
    src=json.loads(SRC.read_text())
    out={};stats={}
    for n in range(1,166):
        text=src.get(str(n),'')
        if not text:continue
        t0=time.time();paras=[];ns=0
        for p in text.split('\n\n'):
            if not p.strip():continue
            ss=sentences(p);tt=[]
            for i in range(0,len(ss),8):
                batch=ss[i:i+8]
                try:
                    tt.extend(translate_batch(batch))
                except Exception as e:
                    print('error',n,e,flush=True)
                    tt.extend(batch)
                ns+=len(batch)
            paras.append(' '.join(tt))
        out[str(n)]='\n\n'.join(paras)
        stats[str(n)]={'sentences':ns,'seconds':round(time.time()-t0,1)}
        OUT.write_text(json.dumps(out,ensure_ascii=False))
        (ROOT/'translation_sources'/'ru-opus-stats.json').write_text(json.dumps(stats,ensure_ascii=False))
        print(n,ns,stats[str(n)]['seconds'],flush=True)
    print('done',len(out),flush=True)
if __name__=='__main__':
    try:
        main()
    except Exception:
        import traceback;traceback.print_exc();raise
