"""Web noi bo: dang nhap, luoi Follow Up, phan cong, xuat/nhap Excel, Automation."""


def create_app(*args, **kwargs):  # import muon de tranh vong import (services <-> web)
    from .app import create_app as _create

    return _create(*args, **kwargs)


__all__ = ["create_app"]
