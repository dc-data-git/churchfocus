"""Per-group pipeline. Each group is processed end to end and saved before the next one starts,
so a run can be stopped at any time and every finished group is usable.

work/groups/<id>/
  sources.json    what was found (Wikipedia match + verdict, Wikidata facts, pages crawled)
  chunks.json     source excerpts
  classify.json   Christian / not (decides whether Christian-only fields apply)
  proposals.json  every proposed change, each with a verified quote + URL
  done.json       stats
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import yaml

from . import prompts
from .discover import crawl_site, wikidata, wikipedia, wikipedia_by_title
from .index import BM25, chunk_docs, quote_found
from .util import read_json, write_json
from .workbook import KB, is_unknown

log = logging.getLogger("denomkb.pipeline")

NON_CHRISTIAN_FAMILY = re.compile(r"(muslim|islam|jewish|judaism|hindu|buddhis|baha|sikh|jain|taoi|shinto|"
                                  r"zoroastr|pagan|wicca|unitarian)", re.I)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Runner:
    def __init__(self, cfg: dict, root: Path, kb: KB, web, llm, dry_run: bool = False):
        self.cfg, self.root, self.kb, self.web, self.llm, self.dry_run = cfg, root, kb, web, llm, dry_run
        self.work = (root / cfg["work_dir"]).resolve()
        with open(root / cfg["fields"], encoding="utf-8") as f:
            self.spec = yaml.safe_load(f)
        ov = root / cfg.get("overrides", "data/overrides.yaml")
        self.overrides = (yaml.safe_load(ov.read_text(encoding="utf-8")) or {}) if ov.exists() else {}
        self.model = cfg["llm"]["chat_model"]
        self.examples = self._examples()

    # ------------------------------------------------------------------ helpers
    def _examples(self) -> dict[str, list[str]]:
        ex: dict[str, list[str]] = {}
        for (g, f), v in self.kb.values.items():
            if not is_unknown(v) and v != "Not applicable" and len(v) <= 90:
                lst = ex.setdefault(f, [])
                if v not in lst and len(lst) < 3:
                    lst.append(v)
        return ex

    def gdir(self, gid: str) -> Path:
        d = self.work / "groups" / gid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def order(self) -> list[dict]:
        gcfg = self.cfg.get("groups", {})
        only = set(gcfg.get("only") or [])
        skip = set(gcfg.get("skip") or []) | {k for k, v in self.overrides.items() if (v or {}).get("skip")}
        gs = [g for g in self.kb.groups if (not only or g["id"] in only) and g["id"] not in skip]
        gs.sort(key=lambda g: -(g["adherents"] or 0))
        n = gcfg.get("max")
        return gs[:n] if n else gs

    # ------------------------------------------------------------------ steps
    def find_sources(self, g: dict) -> tuple[dict, list[dict]]:
        gid, ov = g["id"], (self.overrides.get(g["id"]) or {})
        meta: dict = {"group": g["census_name"], "checked_at": now()}
        docs: list[dict] = []

        wp = wikipedia_by_title(self.web, ov["wikipedia"]) if ov.get("wikipedia") else wikipedia(self.web, g["census_name"])
        if wp:
            verdict = "yes" if ov.get("wikipedia") else self._match(g, wp)
            meta["wikipedia"] = {"title": wp["title"], "url": wp["url"], "score": wp["match_score"], "verdict": verdict}
            if verdict == "yes":
                docs.append({"url": wp["url"], "title": wp["title"], "text": wp["text"], "source_type": "wikipedia"})
                meta["wikidata"] = wikidata(self.web, wp.get("qid"))
        site = ov.get("website")
        kb_site = self.kb.values.get((gid, "identity.official_website"), "")
        if not site and kb_site.startswith("http"):
            site = kb_site.split()[0]
        if not site:
            site = (meta.get("wikidata") or {}).get("official_website")
        meta["official_site"] = site
        c = self.cfg["crawl"]
        if site and c.get("enabled", True):
            for p in crawl_site(self.web, site, c["max_pages"], c.get("max_depth", 2)):
                docs.append({"url": p["url"], "title": p["title"], "text": p["text"], "source_type": "official"})
        reg = [s["url"] for s in self.kb.source_urls_for(gid) if str(s.get("url", "")).startswith("http")]
        for u in reg + list(ov.get("extra_urls") or []):
            if any(d["url"] == u for d in docs):
                continue
            p = self.web.get_page(u)
            if p and p.get("text"):
                docs.append({"url": u, "title": p["title"], "text": p["text"], "source_type": "register"})
        meta["pages"] = [{"url": d["url"], "title": d["title"], "source_type": d["source_type"],
                          "chars": len(d["text"])} for d in docs]
        return meta, docs

    def _match(self, g: dict, wp: dict) -> str:
        msg = (f"GROUP (2020 US Religion Census): {g['census_name']}\n"
               f"Congregations: {int(g['congregations'] or 0)}  Adherents: {int(g['adherents'] or 0)}\n\n"
               f"ARTICLE TITLE: {wp['title']}\nARTICLE START:\n{wp['text'][:1500]}")
        fake = lambda _m: {"same_group": "yes" if wp["match_score"] >= 0.5 else "no", "reason": "dry-run"}
        v = self.llm.complete_json("match", [{"role": "system", "content": prompts.MATCH_SYSTEM},
                                             {"role": "user", "content": msg}], prompts.MATCH_SCHEMA,
                                   model=self.model, fake=fake)
        return (v or {}).get("same_group", "unsure")

    def classify(self, g: dict, chunks: list[dict]) -> dict:
        fam = self.kb.values.get((g["id"], "identity.primary_family"), "")
        if not is_unknown(fam):
            return {"is_christian": "no" if NON_CHRISTIAN_FAMILY.search(fam) else "yes", "basis": f"workbook family: {fam}"}
        if NON_CHRISTIAN_FAMILY.search(g["census_name"]):
            return {"is_christian": "no", "basis": "census group name"}
        if not chunks:
            return {"is_christian": "unclear", "basis": "no sources"}
        text = chunks[0]["text"][:1500]
        fake = lambda _m: {"is_christian": "yes" if re.search(r"christ|church|gospel|bible", text, re.I) else "unclear",
                           "quote": " ".join(text.split()[:12])}
        v = self.llm.complete_json("classify", [{"role": "system", "content": prompts.CLASSIFY_SYSTEM},
                                                {"role": "user", "content": f"GROUP: {g['census_name']}\n\nEXCERPT:\n{text}"}],
                                   prompts.CLASSIFY_SCHEMA, model=self.model, fake=fake)
        v = v or {"is_christian": "unclear", "quote": ""}
        return {**v, "basis": chunks[0]["url"]}

    def _excerpts(self, bm: BM25, fids: list[str]) -> list[dict]:
        k = self.cfg["extract"]["excerpts_per_field"]
        cap = self.cfg["extract"]["max_excerpts"]
        lists = [bm.search(self.spec[f]["keywords"] + " " + self.spec[f]["description"], k) for f in fids]
        out, seen = [], set()
        for i in range(k):
            for lst in lists:
                if i < len(lst) and lst[i]["id"] not in seen and len(out) < cap:
                    out.append(lst[i])
                    seen.add(lst[i]["id"])
        return out

    @staticmethod
    def _excerpt_block(ex: list[dict]) -> str:
        return "\n\n".join(f"[{i + 1}] ({e['source_type']}: {e['title'][:80]})\n{e['text']}" for i, e in enumerate(ex))

    def extract(self, g: dict, bm: BM25, fids: list[str]) -> list[dict]:
        ex = self._excerpts(bm, fids)
        if not ex:
            return []
        flines = []
        for f in fids:
            eg = "; ".join(f'"{x}"' for x in self.examples.get(f, [])) or "(none yet)"
            flines.append(f"- {f}: {self.spec[f]['description']}. Example values from other groups: {eg}")
        user = (f"GROUP: {g['census_name']}\n\nFIELDS:\n" + "\n".join(flines) + "\n\nEXCERPTS:\n" + self._excerpt_block(ex))

        def check(v):
            errs = []
            got = [x["field_id"] for x in v["fields"]]
            if sorted(got) != sorted(fids):
                errs.append(f"return exactly these field ids once each: {fids}")
            for x in v["fields"]:
                if x["status"] == "stated":
                    if not (1 <= x["excerpt"] <= len(ex)):
                        errs.append(f"{x['field_id']}: excerpt number out of range")
                    elif not quote_found(x["quote"], ex[x["excerpt"] - 1]["text"]):
                        errs.append(f"{x['field_id']}: quote is not copied exactly from excerpt {x['excerpt']}")
                    if not x["value"].strip() or len(x["value"].split()) > 30:
                        errs.append(f"{x['field_id']}: value must be 1-20 words")
            return "; ".join(errs[:6]) or None

        def fake(_m):
            out = []
            for f in fids:
                kws = [w for w in self.spec[f]["keywords"].split() if len(w) > 4]
                hit = next((i for i, e in enumerate(ex) if any(w in e["text"].lower() for w in kws)), None)
                if hit is None:
                    out.append({"field_id": f, "status": "not_stated", "value": "", "excerpt": 0, "quote": ""})
                else:
                    q = " ".join(ex[hit]["text"].split()[:14])
                    out.append({"field_id": f, "status": "stated", "value": "(dry-run) " + q[:40], "excerpt": hit + 1, "quote": q})
            return {"fields": out}

        v = self.llm.complete_json("extract", [{"role": "system", "content": prompts.EXTRACT_SYSTEM},
                                               {"role": "user", "content": user}], prompts.EXTRACT_SCHEMA,
                                   model=self.model, fake=fake, check=check, partial=True)
        results = []
        for x in (v or {}).get("fields", []):
            if x["field_id"] not in fids or x["status"] != "stated":
                continue
            if not (1 <= x["excerpt"] <= len(ex)) or not quote_found(x["quote"], ex[x["excerpt"] - 1]["text"]):
                continue                         # unverifiable -> dropped
            if not x["value"].strip() or len(x["value"].split()) > 30:
                continue
            results.append({**x, "chunk": ex[x["excerpt"] - 1]})
        return results

    def validate(self, g: dict, bm: BM25, fids: list[str]) -> list[dict]:
        ex = self._excerpts(bm, fids)
        if not ex:
            return []
        vals = self.kb.group_values(g["id"])
        lines = [f"- {f} ({self.spec[f]['description']}): \"{vals.get(f, '')}\"" for f in fids]
        user = (f"GROUP: {g['census_name']}\n\nEXISTING ENTRIES:\n" + "\n".join(lines) + "\n\nEXCERPTS:\n" + self._excerpt_block(ex))

        def check(v):
            errs = []
            if sorted(x["field_id"] for x in v["fields"]) != sorted(fids):
                errs.append(f"return exactly these field ids once each: {fids}")
            for x in v["fields"]:
                if x["verdict"] != "not_addressed":
                    if not (1 <= x["excerpt"] <= len(ex)) or not quote_found(x["quote"], ex[x["excerpt"] - 1]["text"]):
                        errs.append(f"{x['field_id']}: quote is not copied exactly from excerpt {x['excerpt']}")
                if x["verdict"] == "contradicted" and not x["corrected_value"].strip():
                    errs.append(f"{x['field_id']}: contradicted needs corrected_value")
            return "; ".join(errs[:6]) or None

        fake = lambda _m: {"fields": [{"field_id": f, "verdict": "not_addressed", "corrected_value": "", "excerpt": 0,
                                       "quote": ""} for f in fids]}
        v = self.llm.complete_json("validate", [{"role": "system", "content": prompts.VALIDATE_SYSTEM},
                                                {"role": "user", "content": user}], prompts.VALIDATE_SCHEMA,
                                   model=self.model, fake=fake, check=check, partial=True)
        out = []
        for x in (v or {}).get("fields", []):
            if x["field_id"] not in fids or x["verdict"] == "not_addressed":
                continue
            if not (1 <= x["excerpt"] <= len(ex)) or not quote_found(x["quote"], ex[x["excerpt"] - 1]["text"]):
                continue
            out.append({**x, "chunk": ex[x["excerpt"] - 1]})
        return out

    # ------------------------------------------------------------------ proposal builder
    def _prop(self, g, fid, action, new_value, quote, url, source_type, confidence, model=None):
        old = self.kb.values.get((g["id"], fid), "")
        ev = self.kb.evidence.get((g["id"], fid), {})
        return {"group_id": g["id"], "layer": self.kb.fields[fid]["layer"], "field_id": fid, "action": action,
                "old_value": old, "old_status": ev.get("status") or ("unknown" if is_unknown(old) else ""),
                "new_value": new_value, "quote": quote, "url": url, "source_type": source_type,
                "source_id": "", "confidence": confidence, "model": model or self.model, "checked_at": now()}

    def run_group(self, g: dict, force: bool = False) -> dict:
        d = self.gdir(g["id"])
        if (d / "done.json").exists() and not force:
            return read_json(d / "done.json")
        log.info("== %s (%s adherents)", g["census_name"], int(g["adherents"] or 0))
        meta, docs = self.find_sources(g)
        chunks = chunk_docs(docs, self.cfg["extract"]["chunk_words"])
        write_json(d / "sources.json", meta)
        write_json(d / "chunks.json", chunks)
        cls = self.classify(g, chunks)
        write_json(d / "classify.json", cls)
        christian = cls["is_christian"] != "no"
        vals = self.kb.group_values(g["id"])
        props: list[dict] = []

        # deterministic Wikidata fills
        wd = meta.get("wikidata") or {}
        det = {"identity.official_website": wd.get("official_website"),
               "history.founding_date": wd.get("founding_year"),
               "history.founders": ", ".join(wd.get("founders", [])) or None,
               "history.founding_location": ", ".join(wd.get("founding_location", [])) or None}
        for fid, v in det.items():
            if v and is_unknown(vals.get(fid)):
                props.append(self._prop(g, fid, "fill", v, f"Wikidata {wd['qid']}", wd["url"], "wikidata", "medium", "wikidata"))
        filled = {p["field_id"] for p in props}

        todo_extract, todo_validate, na = [], [], []
        for fid, sp in self.spec.items():
            if not sp.get("extract") or fid in filled:
                continue
            if sp.get("christian_only") and not christian:
                if is_unknown(vals.get(fid)):
                    na.append(fid)
                continue
            v = vals.get(fid)
            if is_unknown(v):
                todo_extract.append(fid)
            elif v != "Not applicable" and self.cfg["validate"]["enabled"]:
                todo_validate.append(fid)
        for fid in na:
            props.append(self._prop(g, fid, "not_applicable", "Not applicable",
                                    f"Christian-specific field; group classified non-Christian ({cls['basis']})", "",
                                    "classification", "medium"))

        bs = self.cfg["extract"]["fields_per_call"]
        stats = {"chunks": len(chunks), "extract_fields": len(todo_extract), "validate_fields": len(todo_validate)}
        if chunks:
            bm = BM25(chunks)
            for i in range(0, len(todo_extract), bs):
                for x in self.extract(g, bm, todo_extract[i:i + bs]):
                    c = x["chunk"]
                    conf = "high" if c["source_type"] in ("official", "register") else "medium"
                    props.append(self._prop(g, x["field_id"], "fill", x["value"], x["quote"], c["url"], c["source_type"], conf))
            for i in range(0, len(todo_validate), bs):
                for x in self.validate(g, bm, todo_validate[i:i + bs]):
                    c = x["chunk"]
                    conf = "high" if c["source_type"] in ("official", "register") else "medium"
                    act = "confirm" if x["verdict"] == "supported" else "conflict"
                    newv = vals.get(x["field_id"], "") if act == "confirm" else x["corrected_value"]
                    props.append(self._prop(g, x["field_id"], act, newv, x["quote"], c["url"], c["source_type"], conf))
        write_json(d / "proposals.json", props)
        for a in ("fill", "confirm", "conflict", "not_applicable"):
            stats[a] = sum(1 for p in props if p["action"] == a)
        stats.update({"christian": cls["is_christian"], "wikipedia": (meta.get("wikipedia") or {}).get("title"),
                      "wikipedia_verdict": (meta.get("wikipedia") or {}).get("verdict"),
                      "official_site": meta.get("official_site"), "pages": len(meta["pages"]), "finished": now()})
        write_json(d / "done.json", stats)
        log.info("   %s", stats)
        return stats
