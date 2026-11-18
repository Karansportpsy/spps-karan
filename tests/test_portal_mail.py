"""Portal account e-mails rendered for a real delivery provider (no network)."""
from server.portal_auth import ProviderOutbox


class Capture:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def test_verification_and_reset_links_carry_token_only_to_recipient():
    cap = Capture()
    out = ProviderOutbox(cap, "https://spps.example.invalid/")
    out.send("verify-email", "a@example.invalid", "tok/en+1")
    out.send("password-reset", "a@example.invalid", "reset-token")
    out.send("existing-account-notice", "a@example.invalid")
    verify, reset, notice = cap.messages
    assert verify.to == "a@example.invalid" and "https://spps.example.invalid/portal?verify=tok%2Fen%2B1" in verify.body
    assert "https://spps.example.invalid/portal?reset=reset-token" in reset.body and "signs out all devices" in reset.body
    assert "?" not in notice.body.split("https://spps.example.invalid/portal")[1][:1]  # no token in the notice
    for m in cap.messages:
        assert "practitioner" not in m.body.lower() and "athlete" not in m.body.lower()
