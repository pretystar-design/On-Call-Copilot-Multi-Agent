from unittest.mock import patch


def test_get_azure_credential_prefers_client_secret_when_env_is_present(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "client-secret")

    from app.config import get_azure_credential

    with patch("azure.identity.ClientSecretCredential") as client_secret_cls, patch(
        "azure.identity.DefaultAzureCredential"
    ) as default_cls:
        credential = get_azure_credential()

    assert credential is client_secret_cls.return_value
    client_secret_cls.assert_called_once_with(
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="client-secret",
    )
    default_cls.assert_not_called()


def test_get_azure_credential_falls_back_to_default_when_env_is_missing(monkeypatch):
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)

    from app.config import config, get_azure_credential

    config.azure_tenant_id = ""
    config.azure_client_id = ""
    config.azure_client_secret = ""

    with patch("azure.identity.DefaultAzureCredential") as default_cls:
        credential = get_azure_credential()

    assert credential is default_cls.return_value
