"""Cada prueba T-M5-NN del spec existe y sigue nombrada como el plan la trazó."""

from pathlib import Path

HERE = Path(__file__).parent
TRACE = {
    "T-M5-01": ("test_thresholds.py", "test_t_m5_01_absent_combination_is_below_threshold"),
    "T-M5-02": ("test_chain.py", "test_t_m5_02_timeout_of_first_provider_uses_second"),
    "T-M5-03": ("test_chain.py", "test_t_m5_03_out_of_schema_twice_then_next_provider"),
    "T-M5-04": ("test_tokens.py", "test_t_m5_04_unknown_token_invalidates_everything"),
    "T-M5-05": ("test_thresholds.py", "test_t_m5_05_null_p_raw_is_below_threshold_even_with_zero_threshold"),
    "T-M5-06": ("test_thresholds.py", "test_t_m5_06_threshold_is_per_language"),
    "T-M5-07": ("test_understand.py", "test_t_m5_07_start_flow_marks_command_and_flow_only"),
    "T-M5-08": ("test_event.py", "test_t_m5_08_event_has_all_fields"),
    "T-M5-09": ("test_calibrate.py", "test_t_m5_09_same_split_same_artifact_even_shuffled"),
    "T-M5-10": ("test_leaks.py", "test_t_m5_10_model_view_reaches_provider_without_pii_direct"),
}


def test_the_ten_spec_cases_are_mapped() -> None:
    assert sorted(TRACE) == [f"T-M5-{n:02d}" for n in range(1, 11)]


def test_every_spec_test_exists_and_names_its_id() -> None:
    missing = []
    for spec_id, (file, name) in TRACE.items():
        text = (HERE / file).read_text(encoding="utf-8")
        if f"def {name}" not in text or spec_id not in text:
            missing.append(spec_id)
    assert missing == []
