import json

from pydantic import EmailStr, TypeAdapter, ValidationError

from core.model import ConfigurationSetting

SETTING_KEY = "EKIRJASTO_CONSORTIUM_MUNICIPALITY_EMAILS"
EMAIL_ADAPTER = TypeAdapter(EmailStr)


def get_consortium_municipality_emails(db):
    setting = ConfigurationSetting.sitewide(db, SETTING_KEY)
    if not setting.value:
        return {"consortiums": {}, "municipalities": {}}

    try:
        values = json.loads(setting.value)
    except json.JSONDecodeError:
        return {"consortiums": {}, "municipalities": {}}
    if not isinstance(values, dict):
        return {"consortiums": {}, "municipalities": {}}

    return {
        "consortiums": (
            values.get("consortiums", {})
            if isinstance(values.get("consortiums", {}), dict)
            else {}
        ),
        "municipalities": (
            values.get("municipalities", {})
            if isinstance(values.get("municipalities", {}), dict)
            else {}
        ),
    }


def save_consortium_municipality_email(db, kind, key, email):
    if kind not in {"consortiums", "municipalities"}:
        raise ValueError("Unsupported email setting type.")
    if not isinstance(key, str) or not key or len(key) > 128:
        raise ValueError("A valid consortium slug or municipality code is required.")
    if not isinstance(email, str):
        raise ValueError("Email address must be text.")

    email = email.strip()
    if email:
        try:
            email = str(EMAIL_ADAPTER.validate_python(email))
        except ValidationError as error:
            raise ValueError("Enter a valid email address.") from error

    setting = ConfigurationSetting.sitewide(db, SETTING_KEY)
    try:
        values = json.loads(setting.value) if setting.value else {}
    except json.JSONDecodeError:
        values = {}
    if not isinstance(values, dict):
        values = {}

    emails = values.get(kind)
    if not isinstance(emails, dict):
        emails = {}
        values[kind] = emails
    if email:
        emails[key] = email
    else:
        emails.pop(key, None)

    setting.value = json.dumps(values, ensure_ascii=False, sort_keys=True)
    return get_consortium_municipality_emails(db)
