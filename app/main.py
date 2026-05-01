from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import SessionLocal
from app.services import run_scheduled_backup_if_due, write_audit_log
from app.web import audit_http_exception_handler, router as web_router


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, debug=settings.debug)
    application.add_exception_handler(HTTPException, audit_http_exception_handler)
    application.include_router(web_router)
    application.mount("/static", StaticFiles(directory="app/static"), name="static")

    @application.on_event("startup")
    def run_scheduled_backup_startup() -> None:
        with SessionLocal() as db:
            try:
                run_scheduled_backup_if_due(db)
            except Exception as exc:
                write_audit_log(
                    db,
                    event_type="backups.scheduled_failed",
                    severity="error",
                    message=f"Scheduled backup failed: {exc}",
                    message_ru=f"Не удалось выполнить бэкап по расписанию: {exc}",
                    details=str(exc),
                )

    return application


app = create_app()
