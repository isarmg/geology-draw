"""End-to-end tests for the standard-library HTTP server."""

import concurrent.futures
import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode


os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from server import MAX_UPLOAD_BYTES, create_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]

COLUMN_CSV = (
    "界,系,组,岩性,厚度,描述\n"
    "古生界,二叠系,山西组,砂岩,2.5,灰白色砂岩\n"
    "古生界,二叠系,山西组,泥岩,1.5,深灰色泥岩\n"
).encode("utf-8")

SECTION_CSV = (
    "钻孔,距离,孔口标高,层号,岩性,厚度,接触关系\n"
    "ZK1,0,10,1,砂岩,1,\n"
    "ZK1,0,10,2,泥岩,2,\n"
    "ZK2,20,11,1,砂岩,1.5,\n"
    "ZK2,20,11,2,泥岩,1,\n"
).encode("utf-8")


class HTTPServerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(
            "127.0.0.1",
            0,
            web_dir=str(PROJECT_ROOT / "web"),
            example_dir=str(PROJECT_ROOT / "examples"),
        )
        # Keep unittest output readable while retaining the real handler.
        cls.server.RequestHandlerClass.log_message = lambda *_args: None
        cls.host, cls.port = cls.server.server_address[:2]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever,
            name="strat-http-test-server",
            daemon=True,
        )
        cls.thread.start()

        # The listening socket is already bound; this only accommodates thread
        # scheduling on unusually loaded CI hosts.
        last_error = None
        for _attempt in range(20):
            try:
                status, _headers, _body = cls._request(
                    "GET", "/api/v1/health", timeout=2)
                if status == 200:
                    return
            except OSError as exc:
                last_error = exc
            time.sleep(0.02)
        raise RuntimeError("test HTTP server did not start") from last_error

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=10)
        if cls.thread.is_alive():
            raise RuntimeError("test HTTP server thread did not stop")

    @classmethod
    def _request(cls, method, path, body=None, headers=None, timeout=120):
        connection = http.client.HTTPConnection(cls.host, cls.port,
                                                timeout=timeout)
        try:
            connection.request(method, path, body=body,
                               headers=dict(headers or {}))
            response = connection.getresponse()
            response_body = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            return response.status, response_headers, response_body
        finally:
            connection.close()

    @classmethod
    def _json_request(cls, method, path, payload, timeout=120):
        body = json.dumps(
            payload, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls._request(
            method,
            path,
            body=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )

    @classmethod
    def _upload(cls, filename, raw):
        target = "/api/v1/documents?" + urlencode({"filename": filename})
        status, headers, body = cls._request(
            "POST",
            target,
            body=raw,
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )
        if status != 201:
            raise AssertionError(
                "upload failed with %s: %s" %
                (status, body.decode("utf-8", errors="replace")))
        return json.loads(body.decode("utf-8")), headers

    def _assert_error(self, response, status, code):
        actual_status, headers, body = response
        self.assertEqual(actual_status, status)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["error"]["code"], code)
        self.assertIsInstance(payload["error"]["message"], str)
        return payload

    def test_index_health_and_capabilities(self):
        status, headers, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn(b"<!doctype html>", body.lower())
        self.assertIn("地层绘图".encode("utf-8"), body)

        status, headers, body = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-cache")
        self.assertIn(b"/api/v1", body)

        status, health_headers, body = self._request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertRegex(health_headers["x-request-id"], r"^[0-9a-f]{16}$")
        health = json.loads(body.decode("utf-8"))
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["api_version"], "v1")

        status, _headers, body = self._request(
            "GET", "/api/v1/capabilities")
        self.assertEqual(status, 200)
        capabilities = json.loads(body.decode("utf-8"))
        self.assertIn("csv", capabilities["upload"]["formats"])
        self.assertEqual(capabilities["upload"]["max_bytes"],
                         MAX_UPLOAD_BYTES)
        self.assertIn("png", capabilities["render"]["formats"])
        self.assertIn("A4", capabilities["render"]["pages"])
        self.assertEqual(
            capabilities["render"]["width_limits"]["图例"]["min"], 3.0)
        self.assertEqual(
            capabilities["render"]["pattern_row_height_mm"],
            {
                "default": 2.5,
                "min": 1.0,
                "max": 10.0,
                "step": 0.1,
                "unit": "mm",
            },
        )
        self.assertEqual(capabilities["document"]["kinds"],
                         ["column", "section"])

    def test_raw_upload_metadata_render_formats_and_delete(self):
        metadata, upload_headers = self._upload("my-column.csv", COLUMN_CSV)
        document_id = metadata["id"]
        self.assertTrue(upload_headers["content-type"].startswith(
            "application/json"))
        self.assertEqual(metadata["filename"], "my-column.csv")
        self.assertEqual(metadata["kind"], "column")
        self.assertEqual(metadata["layer_count"], 2)
        self.assertIn("2 层", metadata["summary"])
        self.assertEqual(metadata["lithologies"], ["砂岩", "泥岩"])

        render_path = "/api/v1/documents/%s/render" % document_id
        status, headers, png = self._json_request(
            "POST", render_path, {"format": "png", "dpi": 36})
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

        status, headers, legend_png = self._json_request(
            "POST", render_path,
            {"format": "png", "dpi": 72,
             "options": {"show_legend": True}})
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertTrue(legend_png.startswith(b"\x89PNG\r\n\x1a\n"))

        status, headers, pdf = self._json_request(
            "POST", render_path, {"format": "pdf", "dpi": 36})
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/pdf")
        self.assertTrue(pdf.startswith(b"%PDF-"))

        status, headers, svg = self._json_request(
            "POST", render_path, {"format": "svg", "dpi": 36})
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("image/svg+xml"))
        self.assertIn("attachment", headers.get("content-disposition", ""))
        self.assertIn(b"<svg", svg)

        status, _headers, body = self._request(
            "DELETE", "/api/v1/documents/%s" % document_id)
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")

        self._assert_error(
            self._json_request("POST", render_path, {"format": "png"}),
            404,
            "document_not_found",
        )

    def test_render_allows_hiding_every_stratigraphic_category(self):
        metadata, _headers = self._upload("no-strata.csv", COLUMN_CSV)
        document_id = metadata["id"]
        render_path = "/api/v1/documents/%s/render" % document_id
        try:
            status, headers, png = self._json_request(
                "POST", render_path,
                {"format": "png", "dpi": 36,
                 "options": {"strata": []}},
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "image/png")
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        finally:
            self._request("DELETE", "/api/v1/documents/%s" % document_id)

    def test_pattern_row_height_is_validated_and_propagated(self):
        documents = []
        try:
            fixtures = (
                ("row-height-column.csv", COLUMN_CSV, "column"),
                ("row-height-section.csv", SECTION_CSV, "section"),
            )
            for filename, raw, kind in fixtures:
                metadata, _headers = self._upload(filename, raw)
                document_id = metadata["id"]
                documents.append(document_id)
                self.assertEqual(metadata["kind"], kind)
                render_path = "/api/v1/documents/%s/render" % document_id

                status, _headers, implicit_default = self._json_request(
                    "POST", render_path, {"format": "png", "dpi": 72})
                self.assertEqual(status, 200)

                status, _headers, explicit_default = self._json_request(
                    "POST", render_path,
                    {"format": "png", "dpi": 72,
                     "options": {"pattern_row_height_mm": 2.5}},
                )
                self.assertEqual(status, 200)

                status, _headers, custom_height = self._json_request(
                    "POST", render_path,
                    {"format": "png", "dpi": 72,
                     "options": {"pattern_row_height_mm": 4.0}},
                )
                self.assertEqual(status, 200)
                self.assertEqual(hashlib.sha256(implicit_default).digest(),
                                 hashlib.sha256(explicit_default).digest())
                self.assertNotEqual(hashlib.sha256(implicit_default).digest(),
                                    hashlib.sha256(custom_height).digest())

                for invalid in (True, 0.9, 10.1):
                    with self.subTest(kind=kind, invalid=invalid):
                        response = self._json_request(
                            "POST", render_path,
                            {"format": "png", "dpi": 72,
                             "options": {
                                 "pattern_row_height_mm": invalid,
                             }},
                        )
                        self._assert_error(response, 422, "invalid_option")

                # JSON's exponent syntax can overflow to infinity even when
                # literal NaN/Infinity tokens are never emitted by clients.
                non_finite = self._request(
                    "POST", render_path,
                    body=(b'{"format":"png","options":'
                          b'{"pattern_row_height_mm":1e309}}'),
                    headers={"Content-Type": "application/json"},
                )
                self._assert_error(non_finite, 422, "non_finite_number")
        finally:
            for document_id in documents:
                self._request(
                    "DELETE", "/api/v1/documents/%s" % document_id)

    def test_templates_and_examples(self):
        for kind in ("column", "section"):
            with self.subTest(template=kind):
                status, headers, body = self._request(
                    "GET", "/api/v1/templates/%s" % kind)
                self.assertEqual(status, 200)
                self.assertEqual(
                    headers["content-type"],
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet",
                )
                self.assertIn("attachment", headers["content-disposition"])
                self.assertTrue(body.startswith(b"PK"))

            with self.subTest(example=kind):
                status, headers, body = self._json_request(
                    "POST", "/api/v1/examples/%s" % kind, {})
                self.assertEqual(status, 201)
                self.assertTrue(headers["content-type"].startswith(
                    "application/json"))
                metadata = json.loads(body.decode("utf-8"))
                self.assertEqual(metadata["kind"], kind)
                self.assertGreater(metadata["layer_count"], 0)
                if kind == "section":
                    self.assertGreaterEqual(metadata["hole_count"], 2)

    def test_rejects_invalid_uploads_json_fields_and_traversal(self):
        unsupported = self._request(
            "POST",
            "/api/v1/documents?filename=data.txt",
            body=COLUMN_CSV,
            headers={"Content-Type": "text/plain"},
        )
        self._assert_error(unsupported, 415, "unsupported_file_type")
        self.assertEqual(unsupported[1].get("connection"), "close")

        oversized = self._request(
            "POST",
            "/api/v1/documents?filename=huge.csv",
            body=b"",
            headers={
                "Content-Type": "text/csv",
                "Content-Length": str(MAX_UPLOAD_BYTES + 1),
            },
        )
        self._assert_error(oversized, 413, "request_too_large")

        metadata, _headers = self._upload("validation.csv", COLUMN_CSV)
        render_path = "/api/v1/documents/%s/render" % metadata["id"]

        # Column widths keep the established CLI/UI behaviour: finite values
        # outside the advertised range are clamped by the renderer boundary.
        clamped = self._json_request(
            "POST", render_path,
            {"format": "png", "dpi": 36,
             "options": {"widths": {"柱状图": 999}}})
        self.assertEqual(clamped[0], 200)
        self.assertTrue(clamped[2].startswith(b"\x89PNG\r\n\x1a\n"))

        invalid_json = self._request(
            "POST",
            render_path,
            body=b'{"format":',
            headers={"Content-Type": "application/json"},
        )
        self._assert_error(invalid_json, 400, "invalid_json")

        unknown_field = self._json_request(
            "POST", render_path, {"format": "png", "unexpected": True})
        self._assert_error(unknown_field, 422, "unknown_field")

        traversal = self._request("GET", "/%2e%2e/README.md")
        payload = self._assert_error(traversal, 404, "not_found")
        self.assertNotIn(str(PROJECT_ROOT), payload["error"]["message"])

        filename_traversal = self._request(
            "POST",
            "/api/v1/documents?filename=..%2Fevil.csv",
            body=COLUMN_CSV,
            headers={"Content-Type": "text/csv"},
        )
        self._assert_error(filename_traversal, 400, "invalid_filename")

    def test_request_scoped_style_isolation(self):
        metadata, _headers = self._upload("style-isolation.csv", COLUMN_CSV)
        render_path = "/api/v1/documents/%s/render" % metadata["id"]
        baseline_request = {
            "format": "png",
            "dpi": 72,
            "options": {"title": "样式隔离测试"},
        }

        status, _headers, before = self._json_request(
            "POST", render_path, baseline_request)
        self.assertEqual(status, 200)

        styled_request = dict(baseline_request)
        styled_request["style"] = {
            "lithology": {"砂岩": {"color": "#ff00ff"}}
        }
        status, _headers, styled = self._json_request(
            "POST", render_path, styled_request)
        self.assertEqual(status, 200)

        status, _headers, after = self._json_request(
            "POST", render_path, baseline_request)
        self.assertEqual(status, 200)

        self.assertNotEqual(hashlib.sha256(before).digest(),
                            hashlib.sha256(styled).digest())
        self.assertEqual(hashlib.sha256(before).digest(),
                         hashlib.sha256(after).digest())

    def test_pattern_preview_and_library_sheet(self):
        preview = {
            "spec": [{
                "type": "rows",
                "spacing": 1.3,
                "heights": [1, 2],
                "rows": ["横线", "空心圆"],
            }],
            "face": "#f2efe6",
            "dpi": 72,
        }
        status, headers, body = self._json_request(
            "POST", "/api/v1/patterns/preview", preview)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

        invalid = {"spec": {"type": "lines", "spacing": 1e-300}}
        self._assert_error(
            self._json_request(
                "POST", "/api/v1/patterns/preview", invalid),
            422,
            "invalid_spec",
        )

        sheet = {"kind": "shapes", "format": "png", "dpi": 36}
        status, headers, body = self._json_request(
            "POST", "/api/v1/sheets/render", sheet)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_concurrent_column_and_section_renders_succeed(self):
        column, _headers = self._upload("parallel-column.csv", COLUMN_CSV)
        section, _headers = self._upload("parallel-section.csv", SECTION_CSV)
        document_ids = [column["id"], section["id"]] * 2
        barrier = threading.Barrier(len(document_ids))

        def render(document_id):
            barrier.wait(timeout=10)
            return self._json_request(
                "POST",
                "/api/v1/documents/%s/render" % document_id,
                {"format": "png", "dpi": 36},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(render, document_id)
                       for document_id in document_ids]
            responses = [future.result(timeout=120) for future in futures]

        for status, headers, body in responses:
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "image/png")
            self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
