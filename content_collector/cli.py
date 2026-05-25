from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from content_collector.database import init_db, new_session
from content_collector.workflows import ImportWorkflow

app = typer.Typer(no_args_is_help=True)


@app.command("init-db")
def init_database() -> None:
    init_db()
    typer.echo("数据库已初始化")


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    uvicorn.run("content_collector.main:app", host=host, port=port, reload=reload)


@app.command("ingest")
def ingest(root_path: Path, auto_extract: bool = False) -> None:
    with new_session() as session:
        scan = ImportWorkflow(session).run(str(root_path), auto_extract=auto_extract)
        typer.echo(f"扫描完成：{scan.id}，文件数：{scan.file_count}")


@app.command("extract-scan")
def extract_scan(scan_id: str) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_scan(scan_id)
        typer.echo("抽取完成")


@app.command("extract-post")
def extract_post(post_id: str) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_post(post_id)
        typer.echo("抽取完成")


if __name__ == "__main__":
    app()