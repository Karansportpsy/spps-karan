"""Offline source preservation contract for consolidated SPPS branch."""
from pathlib import Path
from tools.verify_full_stack import inspect_stack
from tools.check_integration_dependencies import summarize

ROOT = Path(__file__).resolve().parents[1]


def test_cumulative_source_and_webview_assets_are_preserved():
    problems, meta = inspect_stack(ROOT)
    assert problems == [], "\n".join(problems)
    assert meta["orm_tables"] == 32
    assert meta["web_android_asset_pairs_matching"] == meta["asset_pair_count"]


def test_dependency_check_only_reports_names_and_not_secret_values():
    vals = {
        "RAZORPAY_KEY_ID": "TEST_DO_NOT_EXPOSE",
        "RAZORPAY_KEY_SECRET": "SECRET_DO_NOT_EXPOSE",
        "RAZORPAY_WEBHOOK_SECRET": "WEBHOOK_SECRET_DO_NOT_EXPOSE",
        "RAZORPAY_PLAN_CORE_ID": "plan_testing",
        "RAZORPAY_PLAN_PRO_ID": "plan_testing_pro",
    }
    summary = summarize(vals)
    assert summary["razorpay_sandbox"]["all_present"]
    assert "SECRET_DO_NOT_EXPOSE" not in repr(summary)
    assert "WEBHOOK_SECRET_DO_NOT_EXPOSE" not in repr(summary)
    assert summary["postgresql_hosted"]["missing_names"]


def test_not_yet_implemented_roles_are_not_misrepresented_as_integrated():
    model = (ROOT / "server/models.py").read_text(encoding="utf-8")
    # The source still represents athlete as a practitioner-owned case profile;
    # do not silently label it an authenticated athlete login.
    assert "class Athlete(Base):" in model
    assert "owner_practitioner_id" in model
