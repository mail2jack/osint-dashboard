"""PDF generation safety and basic output checks."""

from pathlib import Path

from cms.pdf_report import generate_results_pdf


def test_generate_results_pdf_sanitizes_path_components(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    filename = generate_results_pdf(
        {"ip": "127.0.0.1"},
        "../../email",
        "../../outside.pdf",
        watermark_text="test",
    )

    generated = Path(filename)
    assert generated.parent == Path("reports")
    assert generated.name.endswith(".pdf")
    assert generated.exists()
    assert generated.read_bytes().startswith(b"%PDF")
    assert not (tmp_path / "outside.pdf").exists()
