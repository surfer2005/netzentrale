"""Passwörter im macOS-Schlüsselbund (über das Paket „keyring“).

In der Geräteliste (geraete.json) stehen nie Passwörter – nur hier im Schlüsselbund,
Eintrag „Netzentrale“, Konto „<Geräte-ID>/<Dienst>“. Im Programm „Schlüsselbundverwaltung“
sind die Einträge unter „Netzentrale“ zu finden.
"""
from __future__ import annotations

SERVICE = "Netzentrale"


class SecretError(Exception):
    pass


def _kr():
    try:
        import keyring
        return keyring
    except ImportError as e:
        raise SecretError("Python-Paket „keyring“ fehlt – Passwörter können nicht gespeichert werden.") from e


def _account(dev_id: str, svc: str) -> str:
    return f"{dev_id}/{svc}"


def get(dev_id: str, svc: str) -> str | None:
    try:
        return _kr().get_password(SERVICE, _account(dev_id, svc))
    except SecretError:
        return None
    except Exception:  # noqa: BLE001  (z. B. Schlüsselbund gesperrt / abgelehnt)
        return None


def put(dev_id: str, svc: str, password: str) -> None:
    try:
        _kr().set_password(SERVICE, _account(dev_id, svc), password)
    except SecretError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SecretError(f"Schlüsselbund: {e}") from e


def delete(dev_id: str, svc: str) -> None:
    try:
        _kr().delete_password(SERVICE, _account(dev_id, svc))
    except Exception:  # noqa: BLE001
        pass


def delete_all(dev_id: str, services) -> None:
    for s in services:
        delete(dev_id, s)
