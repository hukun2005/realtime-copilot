"""RI-1：简历 HTTP API。"""

from __future__ import annotations

from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient


def docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("张三")
    document.add_heading("项目经历", level=1)
    document.add_paragraph("智能面试助手 2025.01-2025.06")
    document.add_paragraph("- 使用 Vue3、TypeScript 开发")
    document.add_paragraph("- GitHub: https://github.com/example/repo")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_upload_list_activate_delete(client: TestClient, token: str) -> None:
    headers = {"X-Copilot-Token": token}
    response = client.post(
        "/api/resumes",
        headers=headers,
        files={
            "file": (
                "resume.docx",
                docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 200, response.text
    resume = response.json()
    assert resume["name"] == "张三"
    assert len(resume["projects"]) == 1
    assert resume["projects"][0]["github_urls"] == ["https://github.com/example/repo"]

    assert client.get("/api/resumes", headers=headers).json()[0]["id"] == resume["id"]
    assert client.post(f"/api/resumes/{resume['id']}/activate", headers=headers).json() == {"active": True}
    assert client.get("/api/resumes/active", headers=headers).json()["id"] == resume["id"]
    assert client.get(f"/api/resumes/{resume['id']}", headers=headers).status_code == 200
    assert client.delete(f"/api/resumes/{resume['id']}", headers=headers).json() == {"deleted": True}
    assert client.get(f"/api/resumes/{resume['id']}", headers=headers).status_code == 404


def test_rejects_bad_pdf(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/resumes",
        headers={"X-Copilot-Token": token},
        files={"file": ("evil.pdf", b"MZ" + b"\x00" * 20, "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "executable_file"
