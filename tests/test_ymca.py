import contextlib
import io
import unittest
from unittest.mock import patch

import ymca


class FakeTransactionsApi:
    pass


class YmcaTests(unittest.TestCase):
    def test_load_ynab_api_key_prefers_environment(self) -> None:
        with patch.dict("os.environ", {"YNAB_API_KEY": "  secret-key  "}, clear=True):
            self.assertEqual(ymca.load_ynab_api_key(), "secret-key")

    def test_main_prints_ready_message(self) -> None:
        stdout = io.StringIO()

        with patch("ymca.create_api_client", return_value=contextlib.nullcontext(object())):
            with patch("ymca.ynab.TransactionsApi", return_value=FakeTransactionsApi()):
                with contextlib.redirect_stdout(stdout):
                    ymca.main()

        output = stdout.getvalue()
        self.assertIn("YNAB client is ready.", output)
        self.assertIn("Example object: FakeTransactionsApi", output)
