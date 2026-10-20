import importlib.util
import os
import re
from urllib.parse import unquote
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "woocommerce-mcp"
    / "server.py"
)


class WooCommerceApiUrlTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "woocommerce_mcp_server_for_url_test", SERVER_PATH
        )
        cls.server = importlib.util.module_from_spec(spec)
        with patch.dict(
            os.environ,
            {
                "WOOCOMMERCE_BASE_URL": "http://localhost:8081",
                "WOOCOMMERCE_CANONICAL_URL": "http://localhost:8081",
                "WOOCOMMERCE_CONSUMER_KEY": "test-key",
                "WOOCOMMERCE_CONSUMER_SECRET": "test-secret",
            },
        ):
            spec.loader.exec_module(cls.server)

    def test_product_and_order_urls_use_woocommerce_rest_api_path(self):
        base_url = "http://localhost:8081/"
        expected = {
            "products": "http://localhost:8081/wp-json/wc/v3/products",
            "orders": "http://localhost:8081/wp-json/wc/v3/orders",
            "orders/16": "http://localhost:8081/wp-json/wc/v3/orders/16",
        }

        for endpoint, expected_url in expected.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.server._build_api_url(base_url, endpoint), expected_url
                )

    def test_internal_request_url_is_separate_from_canonical_signature_url(self):
        internal_url = self.server._build_api_url(
            "http://mcphub-wordpress", "products"
        )
        canonical_url = self.server._build_api_url(
            "http://localhost:8081", "products"
        )

        self.assertEqual(
            internal_url,
            "http://mcphub-wordpress/wp-json/wc/v3/products",
        )
        self.assertEqual(
            canonical_url,
            "http://localhost:8081/wp-json/wc/v3/products",
        )
        self.assertNotEqual(internal_url, canonical_url)

    def test_oauth_signature_matches_canonical_url_fixture(self):
        canonical_url = self.server._build_api_url(
            "http://localhost:8081", "products"
        )
        with (
            patch.object(self.server.secrets, "token_hex", return_value="nonce0123"),
            patch.object(self.server.time, "time", return_value=1700000000),
            patch.object(self.server, "CONSUMER_KEY", "ck_dummy"),
            patch.object(self.server, "CONSUMER_SECRET", "cs_dummy"),
        ):
            header = self.server._oauth_header(
                "GET", canonical_url, {"per_page": 10, "search": ""}
            )

        header_params = {
            name: unquote(value)
            for name, value in re.findall(r'(oauth_[a-z_]+)="([^"]*)"', header)
        }
        self.assertEqual(
            header_params["oauth_signature"],
            "bC92iH1VRgWTG1htN53V88KcffDKNkednAteW/6TwFY=",
        )

    def test_oauth_signature_uses_woocommerce_raw_secret_encoding(self):
        canonical_url = self.server._build_api_url(
            "http://localhost:8081", "products"
        )
        with (
            patch.object(self.server.secrets, "token_hex", return_value="nonce0123"),
            patch.object(self.server.time, "time", return_value=1700000000),
            patch.object(self.server, "CONSUMER_KEY", "ck_dummy"),
            patch.object(self.server, "CONSUMER_SECRET", "cs/dummy&"),
        ):
            header = self.server._oauth_header(
                "GET", canonical_url, {"per_page": 10, "search": ""}
            )

        header_params = {
            name: unquote(value)
            for name, value in re.findall(r'(oauth_[a-z_]+)="([^"]*)"', header)
        }
        self.assertEqual(
            header_params["oauth_signature"],
            "dyBxW0akBMZY9vnQicoT/ZgJd2/OYUJslNjUP6wuZAQ=",
        )

    def test_oauth_signer_rejects_duplicate_or_array_query_values(self):
        canonical_url = self.server._build_api_url(
            "http://localhost:8081", "products"
        )
        with self.assertRaisesRegex(ValueError, "duplicate query parameter"):
            self.server._oauth_header(
                "GET", f"{canonical_url}?per_page=10", {"per_page": 10}
            )
        with self.assertRaisesRegex(ValueError, "scalar values"):
            self.server._oauth_header(
                "GET", canonical_url, {"tag": ["one", "two"]}
            )

    def test_safe_error_detail_reports_api_error_and_redacts_credentials(self):
        response = self.server.httpx.Response(
            401,
            json={
                "code": "woocommerce_rest_authentication_error",
                "message": "Invalid secret test-secret",
            },
        )
        with patch.object(self.server, "CONSUMER_SECRET", "test-secret"):
            detail = self.server._safe_response_detail(response)

        self.assertIn("woocommerce_rest_authentication_error", detail)
        self.assertIn("[redacted]", detail)
        self.assertNotIn("test-secret", detail)

    def test_safe_error_detail_handles_non_json_response(self):
        response = self.server.httpx.Response(502, content=b"<html>bad gateway</html>")
        self.assertEqual(
            self.server._safe_response_detail(response),
            "WooCommerce returned a non-JSON error response.",
        )

    async def test_wc_request_sends_internal_url_and_signs_canonical_url(self):
        class FakeResponse:
            is_error = False
            content = b"[]"

            @staticmethod
            def json():
                return []

        class FakeClient:
            def __init__(self, **_kwargs):
                self.response = FakeResponse()
                self.request_args = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def request(self, method, url, **kwargs):
                self.request_args = (method, url, kwargs)
                return self.response

        client = FakeClient()
        params = {"search": "", "per_page": 10, "status": "publish"}
        with (
            patch.object(self.server, "BASE_URL", "http://mcphub-wordpress"),
            patch.object(self.server, "CANONICAL_URL", "http://localhost:8081"),
            patch.object(self.server.httpx, "AsyncClient", return_value=client),
            patch.object(self.server, "_oauth_header", wraps=self.server._oauth_header) as signer,
        ):
            result = await self.server.wc_request("GET", "products", params=params)

        self.assertEqual(result, [])
        self.assertEqual(
            client.request_args[1],
            "http://mcphub-wordpress/wp-json/wc/v3/products",
        )
        signer.assert_called_once_with(
            "GET",
            "http://localhost:8081/wp-json/wc/v3/products",
            params,
        )

    async def test_list_orders_maps_line_items_and_defaults_missing_items_to_empty(self):
        orders = [
            {
                "id": 16,
                "number": "10016",
                "status": "processing",
                "payment_method": "cod",
                "payment_method_title": "Cash on delivery",
                "total": "25.00",
                "currency": "USD",
                "date_created": "2026-10-16T12:00:00",
                "billing": {"first_name": "Ada", "last_name": "Lovelace"},
                "line_items": [{"name": "Widget", "quantity": 2}],
            },
            {
                "id": 17,
                "number": "10017",
                "status": "pending",
                "total": "0.00",
                "currency": "USD",
                "date_created": "2026-10-16T13:00:00",
                "billing": {},
            },
        ]
        request = AsyncMock(return_value=orders)
        with patch.object(self.server, "wc_request", request):
            result = await self.server.list_orders()

        self.assertEqual(
            result[0],
            {
                "id": 16,
                "number": "10016",
                "status": "processing",
                "payment_method": "cod",
                "payment_method_title": "Cash on delivery",
                "total": "25.00",
                "currency": "USD",
                "date_created": "2026-10-16T12:00:00",
                "customer": {"first_name": "Ada", "last_name": "Lovelace"},
                "line_items": [{"name": "Widget", "quantity": 2}],
            },
        )
        self.assertEqual(result[1]["line_items"], [])
        self.assertEqual(result[1]["payment_method"], "")
        self.assertEqual(result[1]["payment_method_title"], "")
        request.assert_awaited_once_with(
            "GET",
            "orders",
            params={"per_page": 10, "orderby": "date", "order": "desc"},
        )

    async def test_get_order_includes_payment_method(self):
        order = {
            "id": 29,
            "number": "10029",
            "status": "cancelled",
            "payment_method": "cod",
            "total": "0.01",
            "currency": "USD",
            "date_created": "2026-10-20T10:00:00",
            "billing": {},
            "line_items": [],
        }
        request = AsyncMock(return_value=order)
        with patch.object(self.server, "wc_request", request):
            result = await self.server.get_order(29)

        self.assertEqual(result["id"], 29)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["payment_method"], "cod")
        request.assert_awaited_once_with("GET", "orders/29")


if __name__ == "__main__":
    unittest.main()
