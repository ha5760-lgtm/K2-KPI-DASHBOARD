import pytest

import custom_kpis
import layout


@pytest.fixture(autouse=True)
def temp_stores(tmp_path, monkeypatch):
    """공통 KPI · 화면 배치 저장 파일을 테스트마다 임시 경로로 분리."""
    monkeypatch.setattr(custom_kpis, "DATA_FILE", str(tmp_path / "custom_kpis.json"))
    monkeypatch.setattr(layout, "DATA_FILE", str(tmp_path / "layout.json"))
    layout.apply()
    yield
    layout.reset_core()  # KPI 정의(이름 · 목표)를 기본값으로 복구
