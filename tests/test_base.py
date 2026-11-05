from engines.base import Credentials


def test_credentials_repr_redacts_values():
    creds = Credentials({"password": "hunter2"})
    assert "hunter2" not in repr(creds)
