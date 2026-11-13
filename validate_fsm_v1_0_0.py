#!/usr/bin/env python3
"""Release validator for FSM Affirming Dataset v1.0.0 (patched: exact D-token, Appendix, and [A-Z].[IVX]+ token matching, heading-anchored item check, W-MIXED).

Usage:
  python validate_fsm_v1_0_0.py FSM_dataset_v1_0_0.txt
  python validate_fsm_v1_0_0.py FSM_dataset_v1_0_0.txt --regulations 'The Regulations of the Free State Militia.txt'

Exit 0 means no errors in the checks performed. It does NOT certify legal
correctness, constitutional fidelity, completeness of research, or independent
review. Warnings are disclosed and do not imply that the dataset passed those
substantive tests.
"""
from __future__ import annotations
import argparse, csv, hashlib, io, json, re, sys
from pathlib import Path

ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')
UNIT_RE = re.compile(r'^U-[A-Za-z0-9_-]+$')
EXT_RE = re.compile(r"^Regs (D\.\d+[A-Za-z0-9.]*(?: item \d+)?|§\d[\d.A-Za-z]*|Appendix [A-Z]\.\d+|[A-Z]\.[IVX]+|'[^']+')$")
INTERNAL_FIELDS = ('depends_on','grounding','framework_answer_refs')
GATE_FIELDS = ('gated_by','gated_by_transitive')
STRENGTH = {'primary_verified': 2, 'secondary_verified': 1}
MENTIONS = {
    'Heller':{'C-HELLER'},'Presser':{'C-PRESSER'},'Perpich':{'C-PERPICH'},
    'Thornton':{'C-THORNTON'},'Tucker':{'C-TUCKER'},'Rawle':{'C-RAWLE'},
    'Von Steuben':{'C-VON-STEUBEN'},'Story':{'C-STORY-1196','C-STORY-1200','C-STORY-1900'},
    'Federalist No. 29':{'C-FED-29'},'1792 Act':{'C-1792'},'§246':{'C-246'}
}


def read_records(lines, err):
    recs=[]
    for n,line in enumerate(lines,1):
        if line.startswith('{"id"') or line.startswith('{"unit_id"'):
            try: recs.append((n,json.loads(line)))
            except json.JSONDecodeError as e: err('E-JSON',n,f'invalid JSON ({e.msg} at char {e.pos})')
    return recs


def extract_schema(text, warn):
    pos=text.find('"$schema"')
    if pos<0:
        warn('W-SCHEMA',0,'embedded JSON Schema not found')
        return None
    start=text.rfind('\n{',0,pos)+1
    if start<=0:
        warn('W-SCHEMA',0,'could not locate schema object start')
        return None
    try: return json.JSONDecoder().raw_decode(text[start:])[0]
    except json.JSONDecodeError as e:
        warn('W-SCHEMA',0,f'embedded schema parse failed: {e.msg}')
        return None


def parse_ledger(lines, err, warn):
    header=None; ledger={}; header_line=0
    for n,line in enumerate(lines,1):
        if line.startswith('cite_id,'):
            try: header=next(csv.reader([line]))
            except Exception as e: err('E-LEDGER',n,f'header parse failed: {e}')
            header_line=n; break
    if not header:
        err('E-LEDGER',0,'ledger header not found'); return ledger
    required={'cite_id','status','checked_directly','limits'}
    if not required.issubset(header): err('E-LEDGER',header_line,f'missing required columns: {sorted(required-set(header))}')
    for n,line in enumerate(lines,1):
        if not line.startswith('C-'): continue
        try: row=next(csv.reader([line]))
        except Exception as e: err('E-LEDGER',n,f'CSV parse failed: {e}'); continue
        if len(row)!=len(header):
            err('E-LEDGER',n,f'{row[0] if row else "?"}: {len(row)} columns; header has {len(header)}'); continue
        d=dict(zip(header,row)); cid=d.get('cite_id','')
        if cid in ledger: err('E-LEDGER-ID',n,f'duplicate ledger ID {cid} (first line {ledger[cid][0]})')
        else: ledger[cid]=(n,d)
        status=d.get('status'); checked=d.get('checked_directly')
        if status=='primary_verified' and checked=='NO': warn('W-LEDGER',n,f'{cid}: primary_verified but checked_directly=NO')
        if status=='secondary_verified' and checked=='YES': warn('W-LEDGER',n,f'{cid}: secondary_verified but checked_directly=YES')
        if checked=='YES' and re.search(r'from memory|not pulled|opinions? not',d.get('limits',''),re.I):
            err('E-LEDGER2',n,f'{cid}: checked_directly=YES but limits say not pulled')
    return ledger


def ref_exists(ref, src_lines):
    whole='\n'.join(src_lines)
    # D.* appendix entries are embedded as inline headings in this source.
    m=re.match(r'^Regs D\.(\d+)(?: item (\d+))?$',ref)
    if m:
        tok=re.compile(rf'(?<![\w.])D\.{m.group(1)}(?!\d)')
        hits=[i for i,l in enumerate(src_lines,1) if tok.search(l)]
        if not hits: return 'missing',hits
        if m.group(2):
            # Prefer a heading line that begins with the token; fall back to the first mention.
            head=[i for i in hits if tok.match(src_lines[i-1].lstrip())]
            start=(head or hits)[0]-1
            block=[]
            for l in src_lines[start+1:start+400]:
                if re.match(r'^\s*D\.\d+(?!\d)',l): break
                block.append(l)
            item=re.escape(m.group(2))
            pat=re.compile(rf'^\s*(\({item}\)|{item}[.)])\s|\bitem {item}\b',re.I)
            if not any(pat.search(l) for l in block): return 'item-unlocated',hits
        return 'ok',hits
    # Exact named heading may occur inline rather than at the start of a line.
    if ref.startswith("Regs '") and ref.endswith("'"):
        title=ref[6:-1].strip().casefold()
        hits=[i for i,l in enumerate(src_lines,1) if title in l.casefold()]
        return ('ok' if hits else 'missing'),hits
    m=re.match(r'^Regs §([\d.A-Za-z]+)$',ref)
    if m:
        num=m.group(1)
        # Numbered headings recur in different parts of this long source.
        pat=re.compile(rf'(?m)^\s*{re.escape(num)}(?:\s|$|[—–:-])')
        hits=[i for i,l in enumerate(src_lines,1) if pat.search(l)]
        if not hits:
            # Some cross-references are embedded in prose; existence is weaker
            # than a unique heading and is reported as a warning, not a pass.
            hits=[i for i,l in enumerate(src_lines,1) if re.search(rf'(?<![\w.]){re.escape(num)}(?![\w.])',l)]
            return ('mentioned' if hits else 'missing'),hits
        return ('ok' if len(hits)==1 else 'ambiguous'),hits
    m=re.match(r'^Regs Appendix ([A-Z])\.(\d+)$',ref)
    if m:
        needle=f'Appendix {m.group(1)}.{m.group(2)}'
        tok=re.compile(rf'(?<![\w.]){re.escape(needle)}(?!\d)',re.I)
        hits=[i for i,l in enumerate(src_lines,1) if tok.search(l)]
        return ('ok' if hits else 'missing'),hits
    m=re.match(r'^Regs ([A-Z])\.([IVX]+)$',ref)
    if m:
        needle=f'{m.group(1)}.{m.group(2)}'
        tok=re.compile(rf'(?<![\w.]){re.escape(needle)}(?![\w.])')
        hits=[i for i,l in enumerate(src_lines,1) if tok.search(l)]
        return ('ok' if hits else 'missing'),hits
    return 'bad-format',[]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('dataset',type=Path)
    ap.add_argument('--regulations',type=Path,default=None)
    args=ap.parse_args()
    path=args.dataset
    if not path.exists(): print(f'ERROR: dataset not found: {path}'); return 2
    text=path.read_text(encoding='utf-8').replace('\r\n','\n')
    lines=text.splitlines(); errors=[]; warnings=[]
    def err(c,n,m): errors.append(f'{c} line {n}: {m}')
    def warn(c,n,m): warnings.append(f'{c} line {n}: {m}')

    recs=read_records(lines,err)
    ids={}; byid={}
    for n,r in recs:
        key=r.get('id') or r.get('unit_id')
        pat=UNIT_RE if 'unit_id' in r else ID_RE
        if not key or not pat.fullmatch(key): err('E-ID',n,f'invalid ID {key!r}'); continue
        if key in ids: err('E-ID',n,f'duplicate ID {key} (first line {ids[key]})')
        else: ids[key]=n; byid[key]=r

    # Embedded Draft 2020-12 schema validation.
    schema=extract_schema(text,warn)
    if schema:
        try:
            import jsonschema
            validator=jsonschema.Draft202012Validator(schema)
            for n,r in recs:
                for e in validator.iter_errors(r):
                    loc='/'.join(str(x) for x in e.absolute_path) or '(record)'
                    err('E-SCHEMA',n,f'{r.get("id") or r.get("unit_id")}.{loc}: {e.message[:160]}')
        except ImportError: warn('W-SCHEMA',0,'jsonschema package unavailable; schema validation skipped')

    ledger=parse_ledger(lines,err,warn)
    ledger_ids=set(ledger)
    gate_ids={k for k,v in byid.items() if k.startswith('GATE-') or k=='FOUNDATIONAL-CONTENT'}

    for n,r in recs:
        rid=r.get('id') or r.get('unit_id')
        for f in INTERNAL_FIELDS:
            for v in r.get(f,[]) or []:
                if v not in ids and v not in {'FOUNDATIONAL-CONTENT','GATE-REGULATIONS-SHA256'}:
                    err('E-REF',n,f'{rid}.{f}: {v!r} does not resolve to an internal record')
        for f in GATE_FIELDS:
            for v in r.get(f,[]) or []:
                if v not in gate_ids:
                    err('E-GATE',n,f'{rid}.{f}: {v!r} is not a declared gate record')
        for c in r.get('cites',[]) or []:
            if c not in ledger_ids: err('E-CITE',n,f'{rid}.cites: {c!r} missing from provenance ledger')
        ext=r.get('external_refs',[]) or []
        if len(ext)!=len(set(ext)): err('E-EXT',n,f'{rid}: duplicate external_refs')
        for ref in ext:
            if not EXT_RE.fullmatch(ref): err('E-EXT',n,f'{rid}: invalid external reference format {ref!r}')
        if r.get('review_status')=='agreed' and r.get('assessment_status')=='pending_independent_review':
            err('E-AGREED',n,f'{rid}: agreed status with pending assessment')
        if r.get('review_status')=='record_verified':
            for c in r.get('cites',[]) or []:
                if ledger.get(c,(None,{}))[1].get('checked_directly')=='NO':
                    warn('W-PROV',n,f'{rid}: record_verified but cited {c} is not checked directly')
        cites=r.get('cites',[]) or []
        levels=[STRENGTH.get(ledger.get(c,(None,{}))[1].get('status'),0) for c in cites]
        known=[x for x in levels if x]
        sv=r.get('source_verification')
        if known and sv in STRENGTH and sv!='mixed' and STRENGTH[sv]>min(known):
            warn('W-SRCVER',n,f'{rid}: source_verification={sv} exceeds weakest cited source; use mixed or qualify evidence roles')
        if sv=='mixed' and not re.search(r'primary basis|secondary_verified|title-level|not checked directly|evidentiary role|corroborat',str(r.get('notes','')),re.I):
            warn('W-MIXED',n,f'{rid}: source_verification=mixed but notes do not state which sources play which evidentiary role')
        blob=json.dumps(r,ensure_ascii=False)
        for name,cids in MENTIONS.items():
            if name in blob and not (cids & set(cites)) and (cids & ledger_ids):
                warn('W-MENTION',n,f'{rid}: mentions {name} but cites none of {sorted(cids & ledger_ids)}')
        if isinstance(r.get('unit_id'),str) and r.get('subunit_of'):
            parent=r['subunit_of']
            if parent not in byid: err('E-SUBUNIT',n,f'{rid}: parent {parent} does not exist')
            if parent=='U-04' and not rid.startswith('U-04'): err('E-SUBUNIT',n,f'{rid}: inconsistent U-04 child ID')

    # Explicit aggregate/child completeness.
    for parent,expected in [('U-04',{'U-04a','U-04b','U-04c','U-04d','U-04e','U-04f'}),('U-12',{'U-12a','U-12b'})]:
        actual={k for k,v in byid.items() if v.get('subunit_of')==parent}
        if actual!=expected: err('E-SUBUNIT',ids.get(parent,0),f'{parent}: expected children {sorted(expected)}, found {sorted(actual)}')

    # Optional source-document pin and external-reference resolution.
    regpath=args.regulations or (path.parent/'The Regulations of the Free State Militia.txt')
    if regpath.exists():
        src=regpath.read_text(encoding='utf-8',errors='replace').splitlines()
        actual_hash=hashlib.sha256(regpath.read_bytes()).hexdigest()
        pin=re.search(r'SHA256:\s*([0-9a-f]{64})',text)
        if pin and pin.group(1)!=actual_hash:
            err('E-HASH',0,f'pinned Regulations SHA-256 {pin.group(1)} does not match supplied source {actual_hash}')
        elif not pin: warn('W-HASH',0,'no SHA-256 pin found in dataset')
        for n,r in recs:
            rid=r.get('id') or r.get('unit_id')
            for ref in r.get('external_refs',[]) or []:
                status,hits=ref_exists(ref,src)
                if status=='missing': err('E-EXT-RESOLVE',n,f'{rid}: {ref!r} not found in supplied Regulations text')
                elif status=='mentioned': warn('W-EXT-MENTION',n,f'{rid}: {ref!r} occurs in source but no unique numbered heading was located')
                elif status=='ambiguous': warn('W-EXT-AMBIG',n,f'{rid}: {ref!r} has repeated section headings in source at {hits[:8]}; add a heading qualifier where necessary')
                elif status=='item-unlocated': warn('W-EXT-ITEM',n,f'{rid}: {ref!r} item number not located by pattern under the D heading; check manually')
                elif status=='bad-format': err('E-EXT',n,f'{rid}: cannot parse {ref!r}')
    else:
        warn('W-SOURCE',0,f'Regulations source not supplied/found ({regpath}); hash and reference-resolution checks skipped')

    print(f'{path}: {len(recs)} records, {len(ledger)} provenance rows')
    print(f'  internal IDs: {len(ids)}; external source checks: {"enabled" if regpath.exists() else "skipped"}')
    for w in warnings: print('  WARN ',w)
    for e in errors: print('  ERROR',e)
    print(f'{len(errors)} error(s), {len(warnings)} warning(s)')
    return 1 if errors else 0

if __name__=='__main__': sys.exit(main())
