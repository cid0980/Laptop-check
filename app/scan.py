"""
scan.py -- one inspection session: every CheckResult, the machine context,
the grade, the valuation and the report files it produced.

Saved as a single JSON file under LCP_Data/reports so the shop keeps a full
history of every laptop it has ever tested (and can re-open an old report and
re-print it). Nothing is uploaded anywhere - this is a 100% offline tool.
"""

from __future__ import annotations

import csv
import glob
import os
import time
from typing import Any, Dict, List, Optional

from app.core import (APP_NAME, APP_VERSION, CHECKS, CheckResult, DATA_DIR,
                      IS_WINDOWS, PHOTOS_DIR, REPORTS_DIR, file_stamp, load_json,
                      make_result, new_scan_id, slug, write_json, write_text)

INVENTORY_CSV = os.path.join(DATA_DIR, "inventory.csv")
CSV_COLUMNS = ["scan_id", "date", "technician", "manufacturer", "model", "serial",
               "cpu", "ram", "storage", "display", "battery_health", "grade", "score",
               "suggested_price", "buy_price", "flags", "report_file"]


class Scan:
    def __init__(self, scan_id: Optional[str] = None) -> None:
        self.id = scan_id or new_scan_id()
        self.results: Dict[str, CheckResult] = {}
        self.ctx: Dict[str, Any] = {}
        self.grade: Dict[str, Any] = {}
        self.valuation: Dict[str, Any] = {}
        self.started = time.time()
        self.finished: Optional[float] = None
        self.log: List[Dict[str, Any]] = []
        self.report_html: str = ""
        self.report_customer_html: str = ""
        self.demo = False

    # ---------------------------------------------------------------- results
    def result(self, cid: str) -> CheckResult:
        if cid not in self.results:
            self.results[cid] = make_result(cid)
        return self.results[cid]

    def add(self, res: CheckResult) -> None:
        self.results[res.cid] = res

    def status_of(self, cid: str) -> str:
        r = self.results.get(cid)
        return r.status if r is not None else "missing"

    def note(self, text: str) -> None:
        self.log.append({"t": time.strftime("%H:%M:%S"), "text": text})

    # ------------------------------------------------------------------ json
    def to_dict(self) -> Dict[str, Any]:
        ident = self.ctx.get("identity") or {}
        return {
            "app": {"name": APP_NAME, "version": APP_VERSION, "windows": IS_WINDOWS},
            "scan_id": self.id,
            "demo": self.demo,
            "started": self.started,
            "started_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "finished": self.finished,
            "finished_text": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.finished))
                              if self.finished else ""),
            "duration_seconds": round((self.finished or time.time()) - self.started, 1),
            "machine": ident,
            "technician": self.ctx.get("technician", ""),
            "signed_off": bool(self.ctx.get("signed_off", False)),
            "admin": bool(self.ctx.get("admin", False)),
            "cosmetic_grade": self.ctx.get("cosmetic_grade", ""),
            "context": {k: v for k, v in self.ctx.items()
                        if k not in ("raw", "battery_report", "emit", "bus", "settings")},
            "results": {cid: self.results[cid].to_dict() for cid in self.results},
            "order": [c[0] for c in CHECKS],
            "grade": self.grade,
            "valuation": self.valuation,
            "log": self.log[-400:],
            "report_html": os.path.basename(self.report_html),
            "report_customer_html": os.path.basename(self.report_customer_html),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scan":
        scan = cls(scan_id=data.get("scan_id"))
        scan.started = data.get("started") or time.time()
        scan.finished = data.get("finished")
        scan.demo = bool(data.get("demo"))
        scan.grade = data.get("grade") or {}
        scan.valuation = data.get("valuation") or {}
        scan.log = data.get("log") or []
        ctx = dict(data.get("context") or {})
        ctx["identity"] = data.get("machine") or {}
        ctx["technician"] = data.get("technician", "")
        ctx["signed_off"] = data.get("signed_off", False)
        ctx["admin"] = data.get("admin", True)
        scan.ctx = ctx
        for cid, rd in (data.get("results") or {}).items():
            res = make_result(cid) if cid in {c[0] for c in CHECKS} else CheckResult(
                cid=cid, name=str(rd.get("name", cid)), group=str(rd.get("group", "Other")))
            for key in ("name", "group", "weight", "status", "score", "summary", "details",
                        "notes", "subtests", "tech", "seconds", "error", "marks", "required"):
                if key in rd:
                    setattr(res, key, rd[key])
            res.flags = [tuple(f) if isinstance(f, list) else f for f in (rd.get("flags") or [])]
            base = os.path.join(PHOTOS_DIR, "")
            res.photos = [os.path.join(base, p) for p in (rd.get("photos") or [])
                          if os.path.exists(os.path.join(base, p))]
            scan.results[cid] = res
        scan.report_html = os.path.join(REPORTS_DIR, data.get("report_html") or "")
        scan.report_customer_html = os.path.join(REPORTS_DIR, data.get("report_customer_html") or "")
        return scan

    # ------------------------------------------------------------------ save
    def save(self) -> str:
        self.finished = self.finished or time.time()
        path = os.path.join(REPORTS_DIR, "%s.json" % self.id)
        write_json(path, self.to_dict())
        self.append_inventory(path)
        return path

    def append_inventory(self, report_path: str) -> None:
        try:
            ident = self.ctx.get("identity") or {}
            cpu = self.ctx.get("cpu") or {}
            mem = self.ctx.get("memory") or {}
            sto = self.ctx.get("storage") or {}
            disp = self.ctx.get("display") or {}
            batt = self.ctx.get("battery") or {}
            val = self.valuation or {}
            new_file = not os.path.exists(INVENTORY_CSV)
            with open(INVENTORY_CSV, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new_file:
                    w.writerow(CSV_COLUMNS)
                w.writerow([
                    self.id,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(self.started)),
                    self.ctx.get("technician", ""),
                    ident.get("manufacturer", ""), ident.get("model", ""), ident.get("serial", ""),
                    (cpu.get("name") or "")[:60],
                    "%.0f GB" % (mem.get("total_gb") or 0),
                    "%.0f GB" % ((sto.get("total_bytes") or 0) / 1024 ** 3),
                    disp.get("klass", ""),
                    ("%.0f%%" % batt["health"]) if batt.get("health") else "",
                    self.grade.get("grade", ""), self.grade.get("score", ""),
                    val.get("suggested", ""), val.get("buy_price", ""),
                    len(self.grade.get("flags") or []),
                    os.path.basename(report_path),
                ])
        except Exception:
            pass


def list_history(limit: int = 200) -> List[Dict[str, Any]]:
    files = sorted(glob.glob(os.path.join(REPORTS_DIR, "*.json")),
                   key=lambda p: os.path.getmtime(p), reverse=True)[:limit]
    out = []
    for f in files:
        data = load_json(f)
        if not isinstance(data, dict):
            continue
        ident = data.get("machine") or {}
        grade = data.get("grade") or {}
        val = data.get("valuation") or {}
        out.append({
            "file": f, "scan_id": data.get("scan_id"),
            "date": data.get("started_text", ""),
            "machine": "%s %s" % (ident.get("manufacturer", "?"), ident.get("model", "?")),
            "serial": ident.get("serial", ""),
            "grade": grade.get("grade", "?"), "score": grade.get("score", ""),
            "suggested": val.get("suggested", ""),
            "flags": len(grade.get("flags") or []),
            "technician": data.get("technician", ""),
            "demo": data.get("demo", False),
        })
    return out


def report_path(scan_id: str, customer: bool = False) -> str:
    return os.path.join(REPORTS_DIR, "%s%s-%s.html" % (
        scan_id, "-CUSTOMER" if customer else "", file_stamp()))
