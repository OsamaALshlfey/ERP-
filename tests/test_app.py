import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import app


def account(code, name, kind="asset", nature="debit", parent="", group=""):
    return {
        "code": code, "name": name, "type": kind, "nature": nature,
        "parent_code": parent, "is_group": group, "_row": 2, "_sheet": "الحسابات",
    }


def make_xlsx():
    files = {
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
          <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
            <sheets><sheet name="Accounts" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Target="worksheets/sheet1.xml"
              Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
          </Relationships>""",
        "xl/worksheets/sheet1.xml": """<?xml version="1.0" encoding="UTF-8"?>
          <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
            <row r="1"><c r="A1" t="inlineStr"><is><t>code</t></is></c><c r="B1" t="inlineStr"><is><t>name</t></is></c><c r="C1" t="inlineStr"><is><t>type</t></is></c><c r="D1" t="inlineStr"><is><t>nature</t></is></c></row>
            <row r="2"><c r="A2" t="inlineStr"><is><t>0010</t></is></c><c r="B2" t="inlineStr"><is><t>نقدية تجريبية</t></is></c><c r="C2" t="inlineStr"><is><t>asset</t></is></c><c r="D2" t="inlineStr"><is><t>debit</t></is></c></row>
          </sheetData></worksheet>""",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


class AccountImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "raseed.sqlite3")
        app.initialize(self.db_path, "InitialSyntheticPass12")
        with app.connect(self.db_path) as db:
            self.company_id = db.execute("SELECT id FROM companies").fetchone()["id"]
            self.user_id = db.execute("SELECT id FROM users").fetchone()["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_import_parent_after_child_preserves_leading_zero_and_is_idempotent(self):
        rows = [
            account("001001", "حساب فرعي تجريبي", parent="0010"),
            account("0010", "مجموعة تجريبية", group="yes"),
        ]
        with app.connect(self.db_path) as db:
            preview = app.preview_accounts(db, self.company_id, rows)
            self.assertEqual(preview["new"], 2)
            self.assertEqual(preview["failed"], 0)
            result = app.commit_accounts(db, self.company_id, self.user_id, rows)
            self.assertEqual(result["new"], 2)
            child = db.execute("SELECT code,parent_id FROM accounts WHERE code='001001'").fetchone()
            parent = db.execute("SELECT id,code,is_group FROM accounts WHERE code='0010'").fetchone()
            self.assertEqual(child["code"], "001001")
            self.assertEqual(child["parent_id"], parent["id"])
            self.assertEqual(parent["code"], "0010")
            self.assertTrue(parent["is_group"])
            repeated = app.preview_accounts(db, self.company_id, rows)
            self.assertEqual(repeated["new"], 0)
            self.assertEqual(repeated["matched"], 2)
            app.commit_accounts(db, self.company_id, self.user_id, rows)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 2)

    def test_preview_reports_duplicate_orphan_and_invalid_type_with_row_numbers(self):
        rows = [
            account("100", "مجموعة", group="yes"),
            account("100", "مكرر"),
            account("200", "يتيم", parent="missing"),
            account("300", "نوع غير معروف", kind="unknown"),
        ]
        with app.connect(self.db_path) as db:
            result = app.preview_accounts(db, self.company_id, rows)
        reasons = {item["reason"] for item in result["issues"]}
        self.assertIn("رمز الحساب مكرر في الملف", reasons)
        self.assertTrue(any("غير موجود" in reason for reason in reasons))
        self.assertIn("نوع الحساب غير صالح أو غير محدد", reasons)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["failed"], 3)
        self.assertTrue(all(item["row"] is not None for item in result["issues"]))

    def test_self_and_circular_parent_links_are_rejected(self):
        rows = [
            account("A", "ألف", parent="A", group="yes"),
            account("B", "باء", parent="C", group="yes"),
            account("C", "جيم", parent="B", group="yes"),
        ]
        with app.connect(self.db_path) as db:
            result = app.preview_accounts(db, self.company_id, rows)
        self.assertEqual(result["new"], 0)
        self.assertTrue(any("نفسه" in item["reason"] for item in result["issues"]))
        self.assertTrue(any("دائري" in item["reason"] for item in result["issues"]))

    def test_csv_and_xlsx_read_file_structure_and_text_codes(self):
        csv_bytes = b"code,name,type,nature\\n0010,Cash,asset,debit\\n"
        rows, metadata = app.parse_csv(csv_bytes)
        self.assertEqual(rows[0]["code"], "0010")
        self.assertEqual(metadata, {"sheets": 1, "columns": 4, "rows": 1})
        xlsx_rows, xlsx_metadata = app.parse_xlsx(make_xlsx())
        self.assertEqual(xlsx_rows[0]["code"], "0010")
        self.assertEqual(xlsx_metadata, {"sheets": 1, "columns": 4, "rows": 1})


class HttpApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "http.sqlite3")
        app.initialize(self.db_path, "InitialSyntheticPass12")
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.RaseedHandler)
        self.server.db_path = self.db_path
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def call(self, path, method="GET", body=None, headers=None, opener=None):
        request = urllib.request.Request(
            self.base + path, data=body, method=method, headers=headers or {},
        )
        return (opener or urllib.request).open(request, timeout=3)

    def test_login_csrf_and_tenant_isolation(self):
        with app.connect(self.db_path) as db:
            db.execute("INSERT INTO companies(name,currency,created_at) VALUES('منشأة ثانية','SAR',?)",
                       (app.utc_now(),))
            second_company = db.execute("SELECT MAX(id) FROM companies").fetchone()[0]
            db.execute("INSERT INTO users(company_id,email,name,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                       (second_company, "second@raseed.local", "مستخدم ثان", app.password_hash("SecondSyntheticPass12"),
                        "admin", app.utc_now()))
            db.execute("INSERT INTO accounts(company_id,code,name,type,nature,created_at) VALUES(?,?,?,?,?,?)",
                       (second_company, "SECOND", "حساب خاص", "asset", "debit", app.utc_now()))
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            self.call("/api/accounts", opener=urllib.request)
        self.assertEqual(unauthorized.exception.code, 401)
        login = self.call(
            "/api/login", "POST",
            json.dumps({"email": "admin@raseed.local", "password": "InitialSyntheticPass12"}).encode(),
            {"Content-Type": "application/json"}, opener,
        )
        session = json.loads(login.read())
        csrf = session["csrf_token"]
        response = self.call("/api/accounts", opener=opener)
        self.assertNotIn("SECOND", response.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as forbidden:
            self.call("/api/company", "POST", b'{"name":"bad"}',
                      {"Content-Type": "application/json"}, opener)
        self.assertEqual(forbidden.exception.code, 403)
        self.assertIn("csrf", forbidden.exception.read().decode())
        saved = self.call("/api/company", "POST", b'{"name":"منشأة معدلة"}',
                          {"Content-Type": "application/json", "X-CSRF-Token": csrf}, opener)
        self.assertEqual(saved.status, 200)


if __name__ == "__main__":
    unittest.main()
