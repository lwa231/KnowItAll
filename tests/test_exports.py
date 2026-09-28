import json

from knowitall import export, paths


def test_export_jobs_writes_json_and_csv_to_exports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "docs" / "exports")   # created on demand
    jobs = [{"company": "Acme", "title": "Engineer", "url": "https://x.com/1", "location": "Berlin",
             "remote": None, "department": None, "posted": None, "source": "lever"}]
    files = export.export_jobs("x.com", jobs)
    assert [f.split("/")[-1] for f in files] == ["jobs_x.com.json", "jobs_x.com.csv"]
    assert json.loads(open(files[0], encoding="utf-8").read()) == jobs
    assert open(files[1], encoding="utf-8-sig").read().splitlines()[0].startswith("company,title")
