import smtplib
import urllib.request

from cybotech.infrastructure.sandbox.tools import SandboxDispatcher

from .conftest import report_action


def test_sandbox_report_execution_has_no_external_transport(monkeypatch):
    monkeypatch.setattr(
        smtplib, "SMTP", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("smtp"))
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("http")),
    )
    dispatcher = SandboxDispatcher([])
    result = dispatcher.execute(report_action())
    assert result.success
    assert result.result == {
        "simulated": True,
        "destination": "reports@internal.example",
        "delivery_id": "delivery-1",
    }
