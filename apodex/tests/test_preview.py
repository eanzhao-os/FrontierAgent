from apodex.preview import build_preview, classify_preview


def test_notebook_preview(tmp_path):
    path = tmp_path / "test.ipynb"
    path.write_text('{"cells":[{"cell_type":"markdown","source":["# Title\\n","Hello notebook"]}]}')
    prev = build_preview(path)
    assert prev["family"] == "notebook"
    assert "Hello notebook" in prev["text"]


def test_truncation_flag(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"a" * 500_010)
    prev = build_preview(path)
    assert prev["truncated"] is True
    assert prev["family"] in {"text", "markdown"}


def test_classify_preview_families(tmp_path):
    assert classify_preview(tmp_path / "a.md") == "markdown"
    assert classify_preview(tmp_path / "a.csv") == "csv"
    assert classify_preview(tmp_path / "a.pdf") == "pdf"
    assert classify_preview(tmp_path / "a.docx") == "docx"
    assert classify_preview(tmp_path / "a.xlsx") == "xlsx"
    assert classify_preview(tmp_path / "a.pptx") == "pptx"
    assert classify_preview(tmp_path / "a.ipynb") == "notebook"
    assert classify_preview(tmp_path / "a.png") == "image"
    assert classify_preview(tmp_path / "a.zip") == "archive"
    assert classify_preview(tmp_path / "a.pdb") == "pdb"
    assert classify_preview(tmp_path / "a.obj") == "model3d"
    assert classify_preview(tmp_path / "a.bin") == "unknown"
