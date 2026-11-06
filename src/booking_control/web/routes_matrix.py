"""5 bang xuong song (SRSv4 muc 68).

SRS: "5 bang nay moi la xuong song cua he thong. Neu chi dua vao file Follow Up Excel de
code thi he thong se nhanh chong tro nen rat kho bao tri khi Buyer hoac Forwarder thay
doi quy trinh."

    1. Buyer x Forwarder Matrix   config/buyer_rules.yaml      > forwarder_rules
    2. Email Pattern Matrix       config/email_profiles.yaml   > forwarders
    3. Deadline Matrix            config/config.yaml           > deadlines
                                  config/workflow.yaml         > document_deadlines
    4. Document Matrix            config/workflow.yaml         > document_*
    5. Field Ownership Matrix     config/followup_template.yaml > columns

Trang gom ca nam bang ve mot cho va - quan trong hon - KIEM TRA CHEO giua chung. Sai lech
giua cac bang (Forwarder co rule nhung khong co bo doc email, chung tu bat buoc ma khong
ai chiu trach nhiem...) la loai loi khong bao gio bao ngay, chi am tham lam automation
bo sot.

Toan bo viec doc cau hinh, gom bang va kiem tra cheo nam trong services/matrices.py de
viet test duoc ma khong can dung den tang web.

Chi doc, khong sua: nguon van la cac file YAML trong config/ (de dua vao git, review va
quay lui duoc). Trang ghi ro moi bang nam o file nao de nguoi sua biet mo dau.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..services import matrices as mx
from .deps import engine, render, require, settings

router = APIRouter()


@router.get("/admin/matrices", response_class=HTMLResponse)
def matrices_page(request: Request):
    require(request, "rules.view")
    cfg = settings()
    m = mx.build(cfg, engine().template)
    return render(
        request, "matrices.html", m=m, nav_override="matrices",
        scan_hours=cfg.scheduler.scan_interval_hours,
        window=(cfg.scheduler.notification_window.start, cfg.scheduler.notification_window.end),
    )
