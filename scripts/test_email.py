"""Send a one-shot test email to verify the SMTP pipeline."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.notify import notify_test, SMTP_USER, SMTP_PASS, EMAIL_TO


def main():
    print(f"=== EMAIL PIPELINE TEST ===")
    print(f"  SMTP_USER set: {bool(SMTP_USER)}")
    print(f"  SMTP_PASS set: {bool(SMTP_PASS)}")
    print(f"  EMAIL_TO:      {EMAIL_TO}")
    print()
    if not SMTP_USER or not SMTP_PASS:
        print("❌  SMTP_USER and SMTP_PASS not set in .env.")
        print("   To enable email:")
        print("   1. Visit https://myaccount.google.com/apppasswords (need 2FA on)")
        print("   2. Create an app password named 'trader'")
        print("   3. Add to /Users/richardchen/trader/.env:")
        print("        SMTP_USER=<your-gmail>@gmail.com")
        print("        SMTP_PASS=<the-16-char-app-password>")
        print("   4. Re-run this script.")
        return
    result = notify_test()
    if result["email"]:
        print(f"✅  Email sent to {result['to']}")
        print("   Check inbox; subject is '[trader/info] trader email test'")
    else:
        print(f"❌  Email send failed (see error above)")


if __name__ == "__main__":
    main()
