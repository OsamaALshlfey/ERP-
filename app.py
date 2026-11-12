#!/usr/bin/env python3
"""Raseed phase-one application; stdlib-only HTTP and SQLite."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree


APP_NAME = "رصــــيـــد"
SESSION_SECONDS = 8 * 60 * 60
IMPORT_SECONDS = 10 * 60
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ACCOUNT_TYPES = {"asset", "liability", "equity", "revenue", "expense", "cogs"}
ACCOUNT_NATURES = {"debit", "credit"}
ROLES = {"admin", "accountant", "viewer"}
TYPE_LABELS = {
    "asset": "أصول",
    "liability": "التزامات",
    "equity": "حقوق ملكية",
    "revenue": "إيرادات",
    "expense": "مصروفات",
    "cogs": "تكلفة المبيعات",
}
ROLE_PERMISSIONS = {
    "admin": {"accounts:read", "accounts:write", "company:write", "users:read", "users:write"},
    "accountant": {"accounts:read", "accounts:write"},
    "viewer": {"accounts:read"},
}
HEADERS = {
    "code": {"code", "account_code", "رمز الحساب", "كود الحساب"},
    "name": {"name", "account_name", "اسم الحساب"},
    "type": {"type", "account_type", "نوع الحساب"},
    "nature": {"nature", "طبيعة الحساب"},
    "parent_code": {"parent_code", "parent", "رمز الأب", "رمز الحساب الأب"},
    "english_name": {"english_name", "name_en", "الاسم الإنجليزي"},
    "is_group": {"is_group", "group", "حساب رئيسي", "مجموعة"},
    "active": {"active", "is_active", "نشط", "التفعيل"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}:{value.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split(":", 1)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def initialize(db_path: str, initial_password: str | None = None) -> str | None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'SAR',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                email TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin','accountant','viewer')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(company_id, email)
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                english_name TEXT,
                type TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','expense','cogs')),
                nature TEXT NOT NULL CHECK (nature IN ('debit','credit')),
                parent_id INTEGER REFERENCES accounts(id),
                is_group INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(company_id, code),
                CHECK (parent_id IS NULL OR parent_id != id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                csrf_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                actor_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS accounts_company_idx ON accounts(company_id);
            CREATE INDEX IF NOT EXISTS audit_company_idx ON audit_log(company_id, id DESC);
            """
        )
        company = db.execute("SELECT id FROM companies ORDER BY id LIMIT 1").fetchone()
        if company is None:
            db.execute(
                "INSERT INTO companies(name, currency, created_at) VALUES(?,?,?)",
                ("منشأة رصيد التجريبية", "SAR", utc_now()),
            )
            company_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            company_id = company["id"]
        admin = db.execute(
            "SELECT id FROM users WHERE company_id=? AND role='admin' LIMIT 1", (company_id,)
        ).fetchone()
        generated_password = None
        if admin is None:
            generated_password = initial_password or secrets.token_urlsafe(18)
            db.execute(
                """INSERT INTO users(company_id,email,name,password_hash,role,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    company_id,
                    "admin@raseed.local",
                    "مدير المنشأة التجريبية",
                    password_hash(generated_password),
                    "admin",
                    utc_now(),
                ),
            )
        db.commit()
        return generated_password


def normalize_header(value: str) -> str | None:
    candidate = " ".join(value.strip().lower().split())
    for key, aliases in HEADERS.items():
        if candidate in aliases:
            return key
    return None


def parse_csv(content: bytes) -> tuple[list[dict], dict]:
    text = content.decode("utf-8-sig")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    data, columns = rows_to_accounts(rows, "CSV")
    return data, {"sheets": 1, "columns": columns, "rows": max(0, len(rows) - 1)}


def column_number(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value


def parse_xlsx(content: bytes) -> tuple[list[dict], dict]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        package_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall(f"{package_rel}Relationship")
        }
        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            shared_xml = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                for item in shared_xml
            ]
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        all_rows: list[dict] = []
        column_counts = []
        data_row_count = 0
        sheets = workbook.find(f"{ns}sheets")
        for sheet in list(sheets or []):
            target = targets[sheet.attrib[f"{rel_ns}id"]].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            if target not in names:
                raise ValueError(f"تعذر قراءة ورقة {sheet.attrib.get('name', '')}")
            sheet_xml = ElementTree.fromstring(archive.read(target))
            xml_rows = sheet_xml.find(f"{ns}sheetData")
            matrix = []
            max_column = 0
            for xml_row in list(xml_rows or []):
                cells = {}
                for cell in xml_row.findall(f"{ns}c"):
                    col = column_number(cell.attrib.get("r", "A1"))
                    max_column = max(max_column, col)
                    value_node = cell.find(f"{ns}v")
                    inline_node = cell.find(f"{ns}is")
                    value = value_node.text if value_node is not None else ""
                    if cell.attrib.get("t") == "s" and value:
                        value = shared_strings[int(value)]
                    elif cell.attrib.get("t") == "inlineStr" and inline_node is not None:
                        value = "".join(item.text or "" for item in inline_node.iter(f"{ns}t"))
                    cells[col - 1] = value or ""
                matrix.append([cells.get(index, "") for index in range(max_column)])
            if matrix:
                data, columns = rows_to_accounts(matrix, sheet.attrib.get("name", ""))
                all_rows.extend(data)
                column_counts.append(columns)
                data_row_count += max(0, len(matrix) - 1)
        return all_rows, {
            "sheets": len(column_counts),
            "columns": max(column_counts, default=0),
            "rows": data_row_count,
        }


def rows_to_accounts(rows: list[list[str]], sheet_name: str) -> tuple[list[dict], int]:
    if not rows:
        return [], 0
    header_row = [str(value or "") for value in rows[0]]
    indexes = {}
    for index, value in enumerate(header_row):
        key = normalize_header(value)
        if key and key not in indexes:
            indexes[key] = index
    required = {"code", "name", "type", "nature"}
    if not required.issubset(indexes):
        missing = "، ".join(sorted(required - indexes.keys()))
        raise ValueError(f"الأعمدة المطلوبة غير موجودة في ورقة {sheet_name}: {missing}")
    result = []
    for row_number, values in enumerate(rows[1:], start=2):
        if not any(str(value or "").strip() for value in values):
            continue
        item = {key: str(values[index]).strip() if index < len(values) and values[index] is not None else ""
                for key, index in indexes.items()}
        item["_row"] = row_number
        item["_sheet"] = sheet_name
        result.append(item)
    return result, len(header_row)


def _truth(value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y", "نعم", "نشط"}


def preview_accounts(db: sqlite3.Connection, company_id: int, rows: list[dict]) -> dict:
    existing_rows = db.execute(
        "SELECT * FROM accounts WHERE company_id=?", (company_id,)
    ).fetchall()
    existing = {row["code"]: dict(row) for row in existing_rows}
    seen = set()
    candidates = {}
    issues = []
    for row in rows:
        code = str(row.get("code", "")).strip()
        issue = None
        if not code:
            issue = "رمز الحساب مطلوب"
        elif len(code) > 64:
            issue = "رمز الحساب يتجاوز 64 حرفًا"
        elif code in seen:
            issue = "رمز الحساب مكرر في الملف"
        elif not row.get("name", "").strip():
            issue = "اسم الحساب مطلوب"
        elif row.get("type") not in ACCOUNT_TYPES:
            issue = "نوع الحساب غير صالح أو غير محدد"
        elif row.get("nature") not in ACCOUNT_NATURES:
            issue = "طبيعة الحساب يجب أن تكون debit أو credit"
        if issue:
            issues.append({"row": row.get("_row"), "sheet": row.get("_sheet"), "code": code, "reason": issue})
            continue
        seen.add(code)
        is_group = _truth(row.get("is_group", ""), False)
        candidate = {
            "code": code,
            "name": row["name"].strip(),
            "english_name": row.get("english_name", "").strip() or None,
            "type": row["type"],
            "nature": row["nature"],
            "parent_code": row.get("parent_code", "").strip() or None,
            "is_group": is_group,
            "active": int(_truth(row.get("active", ""), True)),
            "_row": row.get("_row"),
            "_sheet": row.get("_sheet"),
        }
        if code in existing:
            old = existing[code]
            old_parent = db.execute("SELECT code FROM accounts WHERE id=?", (old["parent_id"],)).fetchone() if old["parent_id"] else None
            same = (
                old["name"] == candidate["name"]
                and (old["english_name"] or None) == candidate["english_name"]
                and old["type"] == candidate["type"]
                and old["nature"] == candidate["nature"]
                and (old_parent["code"] if old_parent else None) == candidate["parent_code"]
                and bool(old["is_group"]) == candidate["is_group"]
                and bool(old["active"]) == bool(candidate["active"])
            )
            if same:
                continue
            issues.append({"row": candidate["_row"], "sheet": candidate["_sheet"], "code": code,
                           "reason": "الحساب موجود ببيانات مختلفة؛ لم يتم تغييره"})
            continue
        candidates[code] = candidate

    graph = {}
    for code, candidate in candidates.items():
        parent_code = candidate["parent_code"]
        if parent_code:
            parent = candidates.get(parent_code) or existing.get(parent_code)
            if parent is None:
                issues.append({"row": candidate["_row"], "sheet": candidate["_sheet"], "code": code,
                               "reason": f"الحساب الأب {parent_code} غير موجود"})
                continue
            if parent_code == code:
                issues.append({"row": candidate["_row"], "sheet": candidate["_sheet"], "code": code,
                               "reason": "لا يمكن ربط الحساب بنفسه"})
                continue
            if not parent["is_group"]:
                issues.append({"row": candidate["_row"], "sheet": candidate["_sheet"], "code": code,
                               "reason": f"الحساب الأب {parent_code} ليس حسابًا رئيسيًا"})
                continue
            if not parent["active"]:
                issues.append({"row": candidate["_row"], "sheet": candidate["_sheet"], "code": code,
                               "reason": f"الحساب الأب {parent_code} غير نشط"})
                continue
            graph[code] = parent_code if parent_code in candidates else None
        else:
            graph[code] = None
    invalid = {issue["code"] for issue in issues if issue["code"]}
    visited, visiting, cycle = set(), set(), set()

    def visit(code):
        if code in visiting:
            cycle.update(visiting)
            return
        if code in visited or code not in graph or code in invalid:
            return
        visiting.add(code)
        parent = graph[code]
        if parent:
            visit(parent)
        visiting.remove(code)
        visited.add(code)

    for code in graph:
        visit(code)
    for code in cycle:
        candidate = candidates[code]
        issues.append({"row": candidate["_row"], "sheet": candidate["_sheet"], "code": code,
                       "reason": "ارتباط دائري في الحسابات"})
    blocked = invalid | cycle
    planned = [candidate for code, candidate in candidates.items() if code not in blocked]
    return {
        "expected": len(rows),
        "read": len(rows),
        "new": len(planned),
        "matched": len(rows) - len(candidates) - sum(
            1 for issue in issues if issue["reason"] == "رمز الحساب مكرر في الملف"
        ),
        "skipped": len(issues),
        "failed": len(issues),
        "issues": issues,
        "accounts": planned,
    }


def add_audit(db: sqlite3.Connection, company_id: int, user_id: int, action: str, details: dict):
    db.execute(
        "INSERT INTO audit_log(company_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?)",
        (company_id, user_id, action, json.dumps(details, ensure_ascii=False), utc_now()),
    )


class RaseedHandler(BaseHTTPRequestHandler):
    server_version = "Raseed/1"
    imports: dict[str, dict] = {}

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status: int, value: dict, headers: dict | None = None):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self, max_size=MAX_UPLOAD_BYTES):
        length = int(self.headers.get("Content-Length", "0"))
        if length > max_size:
            raise ValueError("حجم الطلب أكبر من الحد المسموح")
        return self.rfile.read(length)

    def read_json(self):
        try:
            return json.loads(self.read_body(256 * 1024))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("تعذر قراءة البيانات المرسلة") from error

    def session(self):
        cookie = self.headers.get("Cookie", "")
        token = next((part.strip()[8:] for part in cookie.split(";") if part.strip().startswith("raseed_s=")), None)
        if not token:
            return None
        with connect(self.server.db_path) as db:
            row = db.execute(
                """SELECT s.token_hash,s.csrf_token,s.expires_at,u.id,u.company_id,u.email,u.name,u.role,u.active
                   FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            if not row or row["expires_at"] < int(time.time()) or not row["active"]:
                if row:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
                return None
            return dict(row)

    def require_permission(self, permission):
        user = self.session()
        if not user:
            self.send_json(401, {"error": "انتهت الجلسة أو لم يتم تسجيل الدخول", "code": "unauthenticated"})
            return None
        if permission not in ROLE_PERMISSIONS.get(user["role"], set()):
            self.send_json(403, {"error": "ليس لديك صلاحية لتنفيذ هذا الإجراء", "code": "forbidden"})
            return None
        return user

    def check_csrf(self, user):
        if not user or not hmac.compare_digest(user["csrf_token"], self.headers.get("X-CSRF-Token", "")):
            self.send_json(403, {"error": "تعذر التحقق من الطلب؛ حدّث الصفحة ثم أعد المحاولة", "code": "csrf"})
            return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self.serve_static("index.html", "text/html; charset=utf-8")
            return
        if path in {"/app.js", "/style.css"}:
            content_type = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            self.serve_static(path[1:], content_type)
            return
        if path == "/api/session":
            user = self.session()
            if not user:
                self.send_json(200, {"authenticated": False})
            else:
                self.send_json(200, {
                    "authenticated": True,
                    "csrf_token": user["csrf_token"],
                    "user": {"name": user["name"], "email": user["email"], "role": user["role"]},
                })
            return
        if path == "/api/bootstrap":
            user = self.require_permission("accounts:read")
            if not user:
                return
            with connect(self.server.db_path) as db:
                company = dict(db.execute("SELECT id,name,currency FROM companies WHERE id=?",
                                          (user["company_id"],)).fetchone())
                accounts = [self.account_dict(row) for row in db.execute(
                    "SELECT * FROM accounts WHERE company_id=? ORDER BY code", (user["company_id"],)
                )]
                audits = [dict(row) for row in db.execute(
                    "SELECT action,details,created_at FROM audit_log WHERE company_id=? ORDER BY id DESC LIMIT 8",
                    (user["company_id"],)
                )]
                users = []
                if "users:read" in ROLE_PERMISSIONS[user["role"]]:
                    users = [dict(row) for row in db.execute(
                        "SELECT id,email,name,role,active,created_at FROM users WHERE company_id=? ORDER BY id",
                        (user["company_id"],)
                    )]
            self.send_json(200, {"company": company, "accounts": accounts, "audits": audits, "users": users,
                                 "demo": True, "updated_at": utc_now(), "types": TYPE_LABELS})
            return
        if path == "/api/accounts":
            user = self.require_permission("accounts:read")
            if not user:
                return
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0].strip().lower()
            with connect(self.server.db_path) as db:
                rows = db.execute("SELECT * FROM accounts WHERE company_id=? ORDER BY code",
                                  (user["company_id"],)).fetchall()
                accounts = [self.account_dict(row) for row in rows]
            if query:
                accounts = [item for item in accounts if query in item["code"].lower()
                            or query in item["name"].lower()
                            or query in (item["english_name"] or "").lower()]
            self.send_json(200, {"accounts": accounts})
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            try:
                payload = self.read_json()
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            email = str(payload.get("email", "")).strip()
            password = str(payload.get("password", ""))
            with connect(self.server.db_path) as db:
                user = db.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE AND active=1",
                                  (email,)).fetchone()
                if not user or not password_matches(password, user["password_hash"]):
                    self.send_json(401, {"error": "البريد الإلكتروني أو كلمة المرور غير صحيحة"})
                    return
                token = secrets.token_urlsafe(32)
                csrf = secrets.token_urlsafe(24)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
                db.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)",
                           (token_hash, user["id"], csrf, int(time.time()) + SESSION_SECONDS))
                add_audit(db, user["company_id"], user["id"], "login", {"email": user["email"]})
            self.send_json(200, {"authenticated": True, "csrf_token": csrf, "user": {"name": user["name"], "role": user["role"]}},
                           {"Set-Cookie": f"raseed_s={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_SECONDS}"})
            return
        user = self.session()
        if not user:
            self.send_json(401, {"error": "انتهت الجلسة أو لم يتم تسجيل الدخول", "code": "unauthenticated"})
            return
        if not self.check_csrf(user):
            return
        if path == "/api/logout":
            token = next((part.strip()[8:] for part in self.headers.get("Cookie", "").split(";")
                          if part.strip().startswith("raseed_s=")), "")
            with connect(self.server.db_path) as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                add_audit(db, user["company_id"], user["id"], "logout", {})
            self.send_json(200, {"ok": True}, {"Set-Cookie": "raseed_s=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})
            return
        if path == "/api/company":
            if not self.require_permission("company:write"):
                return
            try:
                payload = self.read_json()
                name = str(payload.get("name", "")).strip()
                if not name or len(name) > 120:
                    raise ValueError("أدخل اسم منشأة صحيحًا (حتى 120 حرفًا)")
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            with connect(self.server.db_path) as db:
                db.execute("UPDATE companies SET name=? WHERE id=?", (name, user["company_id"]))
                add_audit(db, user["company_id"], user["id"], "company.updated", {"name": name})
            self.send_json(200, {"ok": True})
            return
        if path == "/api/accounts":
            if not self.require_permission("accounts:write"):
                return
            try:
                payload = self.read_json()
                account = self.validate_manual_account(payload)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            with connect(self.server.db_path) as db:
                try:
                    parent_id = self.validate_parent(db, user["company_id"], account["parent_code"])
                    db.execute(
                        """INSERT INTO accounts(company_id,code,name,english_name,type,nature,parent_id,is_group,active,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (user["company_id"], account["code"], account["name"], account["english_name"],
                         account["type"], account["nature"], parent_id, account["is_group"], account["active"], utc_now()),
                    )
                except (sqlite3.IntegrityError, ValueError) as error:
                    self.send_json(409, {"error": str(error) if isinstance(error, ValueError) else "رمز الحساب مستخدم مسبقًا"})
                    return
                add_audit(db, user["company_id"], user["id"], "account.created", {"code": account["code"]})
            self.send_json(201, {"ok": True})
            return
        if path == "/api/users":
            if not self.require_permission("users:write"):
                return
            try:
                payload = self.read_json()
                email = str(payload.get("email", "")).strip()
                name = str(payload.get("name", "")).strip()
                password = str(payload.get("password", ""))
                role = payload.get("role")
                if "@" not in email or len(email) > 254:
                    raise ValueError("أدخل بريدًا إلكترونيًا صحيحًا")
                if not name or len(name) > 100:
                    raise ValueError("أدخل اسمًا صحيحًا")
                if len(password) < 12:
                    raise ValueError("كلمة المرور يجب ألا تقل عن 12 حرفًا")
                if role not in {"accountant", "viewer"}:
                    raise ValueError("الدور المحدد غير صالح")
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            with connect(self.server.db_path) as db:
                try:
                    db.execute(
                        "INSERT INTO users(company_id,email,name,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                        (user["company_id"], email, name, password_hash(password), role, utc_now()),
                    )
                except sqlite3.IntegrityError:
                    self.send_json(409, {"error": "يوجد مستخدم بهذا البريد في المنشأة"})
                    return
                add_audit(db, user["company_id"], user["id"], "user.created", {"email": email, "role": role})
            self.send_json(201, {"ok": True})
            return
        if path == "/api/import/preview":
            if not self.require_permission("accounts:write"):
                return
            try:
                content = self.read_body()
                if not content:
                    raise ValueError("اختر ملف CSV أو Excel أولًا")
                filename = self.headers.get("X-Filename", "accounts.csv").lower()
                if filename.endswith(".xlsx"):
                    rows, metadata = parse_xlsx(content)
                elif filename.endswith(".csv"):
                    rows, metadata = parse_csv(content)
                else:
                    raise ValueError("الصيغ المدعومة هي CSV وExcel بصيغة XLSX")
                with connect(self.server.db_path) as db:
                    result = preview_accounts(db, user["company_id"], rows)
                preview_id = secrets.token_urlsafe(24)
                self.imports[preview_id] = {
                    "rows": rows,
                    "company_id": user["company_id"],
                    "user_id": user["id"],
                    "expires": time.time() + IMPORT_SECONDS,
                }
                result.pop("accounts", None)
                result.update(metadata)
                result["preview_id"] = preview_id
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
                self.send_json(400, {"error": str(error) or "تعذر قراءة الملف"})
            return
        if path == "/api/import/commit":
            if not self.require_permission("accounts:write"):
                return
            try:
                payload = self.read_json()
                preview_id = str(payload.get("preview_id", ""))
                staged = self.imports.get(preview_id)
                if not staged or staged["expires"] < time.time():
                    self.imports.pop(preview_id, None)
                    raise ValueError("انتهت صلاحية المعاينة؛ أعد رفع الملف")
                if staged["company_id"] != user["company_id"] or staged["user_id"] != user["id"]:
                    raise ValueError("المعاينة غير صالحة لهذا المستخدم")
                with connect(self.server.db_path) as db:
                    result = preview_accounts(db, user["company_id"], staged["rows"])
                    parent_ids = {row["code"]: row["id"] for row in db.execute(
                        "SELECT id,code FROM accounts WHERE company_id=?", (user["company_id"],)
                    )}
                    pending = {item["code"]: item for item in result["accounts"]}
                    # Insert parents before children, independent of spreadsheet row order.
                    while pending:
                        ready = [item for item in pending.values()
                                 if not item["parent_code"] or item["parent_code"] in parent_ids]
                        if not ready:
                            raise ValueError("تعذر ترتيب الحسابات؛ راجع روابط الحسابات الرئيسية")
                        for item in ready:
                            parent_id = parent_ids.get(item["parent_code"])
                            cursor = db.execute(
                                """INSERT INTO accounts(company_id,code,name,english_name,type,nature,parent_id,is_group,active,created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                (user["company_id"], item["code"], item["name"], item["english_name"], item["type"],
                                 item["nature"], parent_id, item["is_group"], item["active"], utc_now()),
                            )
                            parent_ids[item["code"]] = cursor.lastrowid
                            del pending[item["code"]]
                    add_audit(db, user["company_id"], user["id"], "accounts.imported",
                              {"created": result["new"], "matched": result["matched"], "skipped": result["skipped"]})
                self.imports.pop(preview_id, None)
                self.send_json(200, {"created": result["new"], "matched": result["matched"],
                                     "skipped": result["skipped"], "failed": result["failed"],
                                     "complete": result["skipped"] == 0})
            except (ValueError, sqlite3.IntegrityError) as error:
                self.send_json(400, {"error": str(error) or "تعذر تنفيذ الاستيراد"})
            return
        self.send_json(404, {"error": "المسار غير موجود"})

    @staticmethod
    def account_dict(row):
        return {
            "id": row["id"], "code": row["code"], "name": row["name"], "english_name": row["english_name"],
            "type": row["type"], "type_label": TYPE_LABELS[row["type"]], "nature": row["nature"],
            "parent_id": row["parent_id"], "is_group": bool(row["is_group"]), "active": bool(row["active"]),
        }

    @staticmethod
    def validate_manual_account(payload):
        code = str(payload.get("code", "")).strip()
        name = str(payload.get("name", "")).strip()
        account_type = payload.get("type")
        nature = payload.get("nature")
        if not code or len(code) > 64:
            raise ValueError("رمز الحساب مطلوب (حتى 64 حرفًا)")
        if not name or len(name) > 160:
            raise ValueError("اسم الحساب مطلوب (حتى 160 حرفًا)")
        if account_type not in ACCOUNT_TYPES:
            raise ValueError("نوع الحساب غير صالح")
        if nature not in ACCOUNT_NATURES:
            raise ValueError("طبيعة الحساب غير صالحة")
        return {
            "code": code, "name": name,
            "english_name": str(payload.get("english_name", "")).strip() or None,
            "type": account_type, "nature": nature,
            "parent_code": str(payload.get("parent_code", "")).strip() or None,
            "is_group": int(bool(payload.get("is_group"))), "active": int(bool(payload.get("active", True))),
        }

    @staticmethod
    def validate_parent(db, company_id, parent_code):
        if not parent_code:
            return None
        parent = db.execute("SELECT id,is_group,active FROM accounts WHERE company_id=? AND code=?",
                            (company_id, parent_code)).fetchone()
        if not parent:
            raise ValueError("الحساب الأب غير موجود في المنشأة")
        if not parent["is_group"] or not parent["active"]:
            raise ValueError("يجب أن يكون الحساب الأب رئيسيًا ونشطًا")
        return parent["id"]

    def serve_static(self, filename, content_type):
        root = Path(__file__).parent / "static"
        path = root / filename
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)


def main():
    db_path = os.environ.get("RASEED_DB", str(Path.home() / ".raseed" / "raseed.sqlite3"))
    password = initialize(db_path)
    if password:
        print("بيئة تجريبية — البيانات غير حقيقية")
        print("بيانات الدخول الأولية: admin@raseed.local")
        print(f"كلمة المرور الأولية (تظهر مرة واحدة): {password}")
    else:
        print("بيئة تجريبية — البيانات غير حقيقية")
    host = os.environ.get("RASEED_HOST", "127.0.0.1")
    port = int(os.environ.get("RASEED_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), RaseedHandler)
    server.db_path = db_path
    print(f"{APP_NAME} يعمل على http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nتم إيقاف رصيد.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
