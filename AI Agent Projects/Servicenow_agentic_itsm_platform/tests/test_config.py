import re
from pathlib import Path

from pydantic import SecretStr

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_env_example_parses_with_the_real_settings_class(monkeypatch):
    for name in Settings.model_fields:              # environment outranks files; isolate the file's own values
        monkeypatch.delenv(name.upper(), raising=False)
    s = Settings(_env_file=ROOT / ".env.example")
    assert s.servicenow_mode == "mock" and s.llm_provider == "mock" and s.app_env == "development"
    assert s.servicenow_write_enabled is False and s.auth_enabled is True
    assert s.approval_allow_self_approval is None            # blank => environment-dependent default
    assert s.max_workflow_steps == 20 and s.knowledge_min_score == 0.10


def test_env_example_documents_every_setting():
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"),
                                re.MULTILINE))
    missing = [name.upper() for name in Settings.model_fields if name.upper() not in documented]
    # Test/ops-only knobs that are intentionally not part of the example file.
    assert [m for m in missing if m not in {"AUTO_CREATE_TABLES"} - documented] == []
    assert "API_URL" in documented                            # dashboard variable


def test_env_example_contains_no_real_looking_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"gsk_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]{20,}", text)
    s = Settings(_env_file=ROOT / ".env.example")
    for name, value in s:
        if isinstance(value, SecretStr) and name != "jwt_secret":
            assert value.get_secret_value() == "", name        # every key/password placeholder is empty
    assert "change-me" in s.jwt_secret.get_secret_value()      # development default is flagged as unsafe in prod


def test_safe_dict_masks_all_secret_fields():
    s = Settings(groq_api_key="gsk_realvalue_1234567890", servicenow_password="pw-value-123",
                 jwt_secret="x" * 40, database_url="postgresql://u:dbsecret@h/db")
    dumped = str(s.safe_dict())
    for leaked in ("gsk_realvalue", "pw-value-123", "x" * 40, "dbsecret"):
        assert leaked not in dumped
    assert s.safe_dict()["groq_api_key"] == "***set***"
    assert repr(s.groq_api_key) == "SecretStr('**********')"


def test_production_flag():
    assert Settings(app_env="production").is_production and not Settings(app_env="development").is_production
