import pytest

import custom_kpis


@pytest.fixture(autouse=True)
def temp_custom_store(tmp_path, monkeypatch):
    """공통 KPI 저장 파일을 테스트마다 임시 경로로 분리."""
    monkeypatch.setattr(custom_kpis, "DATA_FILE", str(tmp_path / "custom_kpis.json"))
